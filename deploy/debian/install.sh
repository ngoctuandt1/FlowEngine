#!/usr/bin/env bash
# FlowEngine — one-shot Debian/Ubuntu installer (server only).
#
# Provisions:
#   - flowengine system user + group
#   - /opt/flowengine code tree (cloned from your fork's master branch)
#   - Python venv with requirements.txt
#   - /var/lib/flowengine (db + downloads + uploads)
#   - /var/log/flowengine
#   - /etc/flowengine/flowengine.env (from .env.example, NOT yet rotated)
#   - systemd unit flowengine-server.service (enabled, NOT started)
#
# After this script finishes, you still need to:
#   1. Edit /etc/flowengine/flowengine.env and set a strong API_KEY.
#   2. Pick nginx OR Caddy and apply the matching config.
#   3. systemctl start flowengine-server
#   4. Point your Windows worker's SERVER_URL + API_KEY at this host.
#
# Re-running is idempotent — won't clobber an existing flowengine.env.

set -euo pipefail
IFS=$'\n\t'

REPO_URL="${REPO_URL:-https://github.com/ngoctuandt1/FlowEngine.git}"
REPO_BRANCH="${REPO_BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-/opt/flowengine}"
DATA_DIR="${DATA_DIR:-/var/lib/flowengine}"
LOG_DIR="${LOG_DIR:-/var/log/flowengine}"
ETC_DIR="${ETC_DIR:-/etc/flowengine}"
SVC_USER="${SVC_USER:-flowengine}"

log() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "Run as root (or via sudo)."
    exit 1
fi

if ! command -v apt-get >/dev/null; then
    err "This installer targets Debian/Ubuntu. apt-get not found."
    exit 1
fi

# 1. System packages -----------------------------------------------------------

log "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-venv \
    python3-pip \
    ca-certificates \
    curl

# 2. Service user --------------------------------------------------------------

if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    log "Creating system user '$SVC_USER'..."
    adduser --system --group --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi

# 3. Directories ---------------------------------------------------------------

log "Provisioning directories..."
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0755 "$INSTALL_DIR"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR/downloads"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR/uploads"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$LOG_DIR"
install -d -o root          -g "$SVC_USER" -m 0750 "$ETC_DIR"

# 4. Code -----------------------------------------------------------------------

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing checkout in $INSTALL_DIR..."
    sudo -u "$SVC_USER" git -C "$INSTALL_DIR" fetch origin --quiet
    sudo -u "$SVC_USER" git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
else
    log "Cloning $REPO_URL ($REPO_BRANCH) into $INSTALL_DIR..."
    sudo -u "$SVC_USER" git clone --branch "$REPO_BRANCH" --depth 1 \
        "$REPO_URL" "$INSTALL_DIR"
fi

# 5. Python venv ---------------------------------------------------------------

log "Building Python venv..."
sudo -u "$SVC_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$SVC_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade --quiet pip wheel
sudo -u "$SVC_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet \
    -r "$INSTALL_DIR/requirements.txt"

# 6. Env file -------------------------------------------------------------------

ENV_FILE="$ETC_DIR/flowengine.env"
if [[ ! -f "$ENV_FILE" ]]; then
    log "Seeding $ENV_FILE from template..."
    install -o root -g "$SVC_USER" -m 0640 \
        "$INSTALL_DIR/deploy/debian/flowengine.env.example" "$ENV_FILE"
    # Generate a real API_KEY on first run so the operator gets a working
    # default rather than 'CHANGE_ME_BEFORE_DEPLOY'. They should still
    # rotate it, but at least it's not the literal example string.
    NEW_KEY="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p -c 64)"
    sed -i "s|^API_KEY=.*$|API_KEY=$NEW_KEY|" "$ENV_FILE"
    log "Generated random API_KEY in $ENV_FILE — copy it to your worker's .env now."
else
    log "Keeping existing $ENV_FILE (will not rotate API_KEY)."
fi

# 7. systemd unit --------------------------------------------------------------

log "Installing systemd unit..."
install -o root -g root -m 0644 \
    "$INSTALL_DIR/deploy/debian/flowengine-server.service" \
    /etc/systemd/system/flowengine-server.service
systemctl daemon-reload
systemctl enable flowengine-server.service

cat <<EOF

  ✅ FlowEngine server installed.

  Next steps:
    1. Review and rotate the API_KEY in: $ENV_FILE
    2. Pick a TLS terminator and apply ONE of:
         nginx:  cp $INSTALL_DIR/deploy/debian/nginx-flowengine.conf \\
                    /etc/nginx/sites-available/flowengine \\
                 && ln -s /etc/nginx/sites-available/flowengine \\
                          /etc/nginx/sites-enabled/ \\
                 && certbot --nginx -d ai.ciem
         caddy:  cp $INSTALL_DIR/deploy/debian/Caddyfile /etc/caddy/Caddyfile \\
                 && systemctl reload caddy
    3. Start the API:
         systemctl start flowengine-server
         systemctl status flowengine-server --no-pager
    4. Health check:
         curl -fsS https://ai.ciem/health
    5. On the Windows worker box, set:
         SERVER_URL=https://ai.ciem
         API_KEY=<value from $ENV_FILE>

EOF
