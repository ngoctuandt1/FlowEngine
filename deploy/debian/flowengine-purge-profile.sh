#!/usr/bin/env bash
# flowengine-purge-profile — privileged helper to delete a root-owned profile dir.
#
# INSTALL (Debian production host):
#   sudo install -o root -g root -m 0755 \
#       /opt/flowengine/deploy/debian/flowengine-purge-profile.sh \
#       /usr/local/bin/flowengine-purge-profile
#
#   sudo tee /etc/sudoers.d/flowengine-purge >/dev/null <<'EOF'
#   flowengine ALL=(root) NOPASSWD: /usr/local/bin/flowengine-purge-profile *
#   EOF
#   sudo chmod 440 /etc/sudoers.d/flowengine-purge
#
# USAGE:
#   /usr/local/bin/flowengine-purge-profile <profile-name>
#
# PURPOSE:
#   When chrome is launched as root, it creates root-owned subdirs inside the
#   profile directory (e.g. Default/blob_storage/<uuid>, Local State).
#   The worker (flowengine user) cannot rmtree those dirs.  This helper runs as
#   root via sudo -n and performs the deletion after strict path validation.
#
# SECURITY:
#   - Profile name is validated against [A-Za-z0-9._-]+ only (no path separators,
#     no '..', no shell metacharacters).
#   - The resolved absolute path MUST start with PROFILES_ROOT (hardcoded).
#   - The script does NOT accept a path argument — only a bare profile name.
#   - sudoers entry uses NOPASSWD only for this specific binary.

set -euo pipefail

# Hardcoded profiles root — NOT derived from arguments.
PROFILES_ROOT="/opt/flowengine/chrome-profiles"

log() {
    logger -t flowengine-purge "$*"
}

die() {
    log "ERROR: $*"
    echo "flowengine-purge-profile: ERROR: $*" >&2
    exit 1
}

# --- Argument validation ---

if [[ $# -ne 1 ]]; then
    die "Usage: flowengine-purge-profile <profile-name>"
fi

PROFILE_NAME="$1"

# Strict allowlist: alphanumeric, dot, dash, underscore.
# This rejects '/', '\\', '..', '@', '~', spaces, and shell metacharacters.
if ! [[ "$PROFILE_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "Invalid profile name (rejected by allowlist): '$PROFILE_NAME'"
fi

# Reject explicit traversal sequences just in case.
if [[ "$PROFILE_NAME" == *".."* ]]; then
    die "Profile name contains '..': '$PROFILE_NAME'"
fi

# --- Path resolution and prefix check ---

PROFILE_PATH="${PROFILES_ROOT}/${PROFILE_NAME}"

# Use realpath --canonicalize-missing so it works even when the dir is gone.
RESOLVED="$(realpath --canonicalize-missing -- "$PROFILE_PATH")"

# The resolved path MUST be a direct child of PROFILES_ROOT.
# This ensures e.g. 'foo/../../etc' cannot escape even if realpath resolves it.
EXPECTED_PREFIX="${PROFILES_ROOT}/"
if [[ "$RESOLVED" != "${EXPECTED_PREFIX}"* ]]; then
    die "Resolved path '$RESOLVED' is not under '$PROFILES_ROOT'"
fi

# Prevent deletion of PROFILES_ROOT itself.
if [[ "$RESOLVED" == "$PROFILES_ROOT" ]]; then
    die "Refusing to delete the profiles root itself"
fi

# --- Deletion ---

DELETED=0

if [[ -d "$RESOLVED" ]]; then
    log "Removing profile dir: $RESOLVED"
    rm -rf -- "$RESOLVED"
    DELETED=$((DELETED + 1))
fi

# Also remove any sibling .burned-* archives created by ProfileSwapper.
# These match the pattern <PROFILES_ROOT>/<PROFILE_NAME>.burned-*
# Use find to avoid glob-expansion issues when no matches exist.
while IFS= read -r -d '' ARCHIVE; do
    log "Removing burned archive: $ARCHIVE"
    rm -rf -- "$ARCHIVE"
    DELETED=$((DELETED + 1))
done < <(find "$PROFILES_ROOT" -maxdepth 1 -type d \
    -name "${PROFILE_NAME}.burned-*" -print0 2>/dev/null)

log "Done. Removed $DELETED item(s) for profile '$PROFILE_NAME'."
exit 0
