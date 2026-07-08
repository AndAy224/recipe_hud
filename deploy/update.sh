#!/bin/bash
# Self-update for /opt/recipehud, launched by POST /api/system/update.
# Runs as the service user; needs no sudo: it updates the tree, then kills
# the backend's main pid — systemd's Restart=always boots the new code.
#
# NOTE: uses `git reset --hard origin/<branch>` — any local edits made
# directly on the Pi are intentionally discarded so the appliance always
# matches the repository.
set -euo pipefail

TARGET=/opt/recipehud
LOG="$TARGET/data/update.log"
STATUS="$TARGET/data/update-status.json"
mkdir -p "$TARGET/data"
exec >>"$LOG" 2>&1

status() {
    printf '{"phase":"%s","ok":%s,"ts":"%s","detail":"%s"}\n' \
        "$1" "$2" "$(date -Is)" "${3:-}" > "$STATUS"
}
trap 'status failed false "see update.log"' ERR

echo "== update started $(date -Is) =="

status fetching true
git -C "$TARGET" fetch origin
BRANCH=$(git -C "$TARGET" rev-parse --abbrev-ref HEAD)
git -C "$TARGET" reset --hard "origin/$BRANCH"
echo "now at $(git -C "$TARGET" rev-parse --short HEAD)"

status installing true
"$TARGET/.venv/bin/pip" install --quiet --upgrade "$TARGET"

status restarting true
# Same-user kill; a pip failure above aborts before this line, leaving the
# old code running.
MAIN_PID=$(systemctl show -p MainPID --value recipehud-backend)
if [ -n "$MAIN_PID" ] && [ "$MAIN_PID" != "0" ]; then
    kill "$MAIN_PID"
fi
status done true
echo "== update finished $(date -Is) =="
