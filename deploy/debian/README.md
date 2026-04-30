# Debian deploy — FlowEngine on `ai.ciem`

Production deploy of the FlowEngine **server** (FastAPI + dashboard) on a Debian/Ubuntu host. The **worker** (Chrome automation) stays on Windows because it needs a real Chrome instance signed into Google Flow with TOTP creds — that doesn't move cleanly to a headless Linux box.

## Architecture

```
            ┌──────────────────────────┐
            │  https://ai.ciem         │   <─ users (browser)
            └─────────────┬────────────┘
                          │ TLS 443
                ┌─────────▼─────────┐
                │  nginx / Caddy    │   reverse proxy + Let's Encrypt
                └─────────┬─────────┘
                          │ http 127.0.0.1:8080
                ┌─────────▼─────────┐
                │  flowengine API   │   systemd: flowengine-server.service
                │  + SQLite + WS    │   user: flowengine
                └─────────┬─────────┘
                          │ /api/worker/* (Bearer API_KEY)
                          │ ── over public internet OR Tailscale ──
              ┌───────────▼────────────┐
              │  Windows worker box    │   python run_worker.py
              │  Chrome + ngoctuandt20 │   SERVER_URL=https://ai.ciem
              │  (existing setup)      │   API_KEY=<from server .env>
              └────────────────────────┘
```

**Why split:** the worker spawns real Chrome via `chrome.exe`, drives it via Playwright + CDP, persists cookies in `chrome-profiles/<account>/Default/`. Headless Linux Chrome works for some sites but Flow's auth + TOTP flow is brittle outside a real desktop session. The split keeps deploy simple and matches how the engine is already used.

## One-shot install

```bash
ssh root@ai.ciem
curl -fsSL https://raw.githubusercontent.com/ngoctuandt1/FlowEngine/master/deploy/debian/install.sh \
    | bash
```

What it does (idempotent):

1. `apt install` python3, venv, git, curl
2. Creates `flowengine` system user (`/usr/sbin/nologin`)
3. Clones the repo to `/opt/flowengine`
4. Builds a venv with `requirements.txt`
5. Provisions `/var/lib/flowengine` (db + downloads + uploads), `/var/log/flowengine`
6. Generates `/etc/flowengine/flowengine.env` with a random `API_KEY`
7. Installs + enables (but does NOT start) `flowengine-server.service`

## Manual install — if you don't trust the script

```bash
# 1. user + dirs
adduser --system --group --home /opt/flowengine --shell /usr/sbin/nologin flowengine
install -d -o flowengine -g flowengine -m 0750 /var/lib/flowengine{,/downloads,/uploads}
install -d -o flowengine -g flowengine -m 0750 /var/log/flowengine
install -d -o root -g flowengine -m 0750 /etc/flowengine

# 2. code + venv
sudo -u flowengine git clone https://github.com/ngoctuandt1/FlowEngine.git /opt/flowengine
sudo -u flowengine python3 -m venv /opt/flowengine/.venv
sudo -u flowengine /opt/flowengine/.venv/bin/pip install -r /opt/flowengine/requirements.txt

# 3. env
install -o root -g flowengine -m 0640 \
    /opt/flowengine/deploy/debian/flowengine.env.example \
    /etc/flowengine/flowengine.env
sed -i "s|API_KEY=.*|API_KEY=$(openssl rand -hex 32)|" /etc/flowengine/flowengine.env

# 4. systemd
install -m 0644 /opt/flowengine/deploy/debian/flowengine-server.service \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now flowengine-server
```

## Front it with TLS

Pick **one** of nginx or Caddy.

### Option A — nginx + certbot

```bash
apt install -y nginx certbot python3-certbot-nginx
cp /opt/flowengine/deploy/debian/nginx-flowengine.conf \
   /etc/nginx/sites-available/flowengine
ln -s /etc/nginx/sites-available/flowengine /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Provision cert
certbot --nginx -d ai.ciem
```

### Option B — Caddy (auto-TLS, simpler)

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
cp /opt/flowengine/deploy/debian/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

## Lock down `/api/worker/*`

The Bearer token (`API_KEY`) gates the worker endpoints server-side, but defense-in-depth: also restrict by source IP at the proxy. The nginx + Caddy configs ship with the relevant `allow` / `remote_ip` blocks commented out — uncomment, fill in your worker's public IP (or Tailscale tunnel IP), and reload.

The dashboard surfaces (`/api/jobs`, `/api/profiles`, `/`) are intentionally open. If you don't want the dashboard public, add basic-auth in nginx (`auth_basic` directive — example commented in the conf) or via `caddy hash-password` for Caddy.

## Wire up the worker

### On Windows

On the Windows host that runs Chrome:

```powershell
# In the FlowEngine repo on Windows, set env (or .env file):
$env:SERVER_URL = "https://ai.ciem"
$env:API_KEY    = "<paste the value from /etc/flowengine/flowengine.env>"
$env:WORKER_PROFILES = "ngoctuandt20"
$env:CHROME_USER_DATA_DIR = "D:\AI\FlowEngine\chrome-profiles"

python run_worker.py
```

The worker preflight (added in PR #61) checks the profile dir is warm before claiming, and exits 2 with a hint if it isn't — so a misconfigured Windows host fails fast rather than spamming Flow with a sign-in screen.

### Running the worker on Linux

If you choose to run a worker on Linux, do **not** launch Chrome as `root` by default. Chrome refuses to start as `uid=0` without `--no-sandbox`, and FlowEngine intentionally does not add that flag silently because it weakens Chrome's sandbox boundary.

Recommended: create or reuse the `flowengine` system user, make it the owner of `/opt/flowengine`, and run the worker as that user.

```bash
# Reuse the service account from the manual install steps, or create it if needed.
adduser --system --group --home /opt/flowengine --shell /usr/sbin/nologin flowengine
chown -R flowengine:flowengine /opt/flowengine

sudo -u flowengine bash -lc '
cd /opt/flowengine
export SERVER_URL="https://ai.ciem"
export API_KEY="<paste the value from /etc/flowengine/flowengine.env>"
export WORKER_PROFILES="ngoctuandt20"
export CHROME_USER_DATA_DIR="/opt/flowengine/chrome-profiles"
python run_worker.py
'
```

If you are forced to run the worker as `root` (for example in CI or a tightly constrained container), set `FLOW_ALLOW_ROOT_NO_SANDBOX=1`. FlowEngine will then append `--no-sandbox` explicitly and log a warning. Treat this as an escape hatch, not the default.

```bash
cd /opt/flowengine
export SERVER_URL="https://ai.ciem"
export API_KEY="<paste the value from /etc/flowengine/flowengine.env>"
export WORKER_PROFILES="ngoctuandt20"
export CHROME_USER_DATA_DIR="/opt/flowengine/chrome-profiles"
export FLOW_ALLOW_ROOT_NO_SANDBOX=1

python run_worker.py
```

## Auto-replace burned profiles

When Flow trips reCAPTCHA v3 invisible bot protection, the worker marks the current Chrome profile as burned, fails the active job, then tries to replace that profile automatically before the next claim cycle. The replacement path is: detect reCAPTCHA -> archive the burned profile -> pick the next fresh credential from `profiles_ultra.txt` -> warm that profile -> put it back into the worker pool.

Set `FLOW_AUTO_REPLACE_PROFILES=0` to disable the automatic swap. If the env var is unset, the default is `1`, so burned profiles are auto-replaced when a fresh account is available.

This only works if `FLOW_PROFILE_LIST_FILE` points at a populated `profiles_ultra.txt` with more fresh accounts than the worker's active pool. Keep spare warmed credentials available beyond the profiles currently listed in `WORKER_PROFILES`, or the swap path will exhaust the pool and stop at a failed job.

## File-sharing the downloads

The dashboard renders generated `<video>` tiles via `/downloads/<file>.mp4`. The worker writes those files locally on Windows, and the server is serving them from `/var/lib/flowengine/downloads` on Debian — they're not the same directory by default.

Pick **one**:

- **Sync (simplest)**: cron a 60-second `rclone sync D:\AI\FlowEngine\downloads ai.ciem:/var/lib/flowengine/downloads` from the Windows host. Files appear ~minute-late but operations stay simple.
- **SMB mount on the server**: mount the worker's `D:\AI\FlowEngine\downloads` as `/var/lib/flowengine/downloads` over SMB. Real-time but adds a network dependency.
- **Skip thumbnails**: leave `output_files` populated but accept that hover-play won't work in the production dashboard. Worker still completes jobs; users get the Flow project URL to play the video on Flow itself.

## Health check + monitoring

```bash
# Liveness
curl -fsS https://ai.ciem/health

# What's running
systemctl status flowengine-server --no-pager
journalctl -u flowengine-server -f

# DB sanity
sqlite3 /var/lib/flowengine/flowengine.db 'SELECT status, COUNT(*) FROM jobs GROUP BY status'
```

## Updating

```bash
cd /opt/flowengine
sudo -u flowengine git fetch && sudo -u flowengine git reset --hard origin/master
sudo -u flowengine .venv/bin/pip install -r requirements.txt
systemctl restart flowengine-server
```

Or just re-run `install.sh` — it's idempotent and won't rotate the env.

## Rolling back

```bash
sudo -u flowengine git -C /opt/flowengine reset --hard <known-good-sha>
systemctl restart flowengine-server
```

DB schema changes auto-migrate on startup (`server/db/database.py:_ensure_job_column`), so older code talking to a newer DB is the rollback risk to be aware of — usually fine because new columns just stay null.

## Removing

```bash
systemctl disable --now flowengine-server
rm /etc/systemd/system/flowengine-server.service
systemctl daemon-reload
rm -rf /opt/flowengine /var/lib/flowengine /var/log/flowengine /etc/flowengine
deluser --remove-home flowengine
```
