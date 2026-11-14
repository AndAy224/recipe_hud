#!/bin/bash
# Self-update for /opt/recipehud, launched by POST /api/system/update.
# Runs as the service user; needs no sudo: it updates the tree, then kills
# the backend's main pid — systemd's Restart=always boots the new code.
#
# NOTE: uses `git reset --hard origin/<branch>` — any local edits made
# directly on the Pi are intentionally discarded so the appliance always
# matches the repository.
#
# Failure handling (each of these used to leave the appliance stuck):
#   * one run at a time (flock) — two quick taps on Update can't collide;
#   * the status file carries this script's pid, and an EXIT trap marks any
#     run that didn't reach the end as failed. The script lives in the
#     backend's cgroup, so a backend restart mid-install kills it too; without
#     the trap the status stayed "installing" and every later update was
#     refused as "already running". (A SIGKILL/power cut can't run the trap —
#     the API then sees the pid is gone.)
#   * if the install does not complete (pip fails, or the script is killed
#     mid-install), the tree goes back to the commit it was on, instead of
#     leaving new code on disk for the next restart to run on old packages;
#   * pip only runs when pyproject.toml differs from the last successful
#     install, so a code-only update takes seconds rather than minutes.
set -euo pipefail

# Overridable only so the script can be exercised against a scratch clone.
TARGET="${RECIPEHUD_UPDATE_TARGET:-/opt/recipehud}"
SERVICE="${RECIPEHUD_UPDATE_SERVICE:-recipehud-backend}"

# A copy of this script in some other checkout (the dev tree, a scratch
# instance) must never reset the appliance out from under it.
SELF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$SELF_ROOT" != "$(cd "$TARGET" && pwd -P)" ]; then
    echo "update.sh: refusing to update $TARGET from the checkout at $SELF_ROOT" >&2
    exit 1
fi

LOG="$TARGET/data/update.log"
STATUS="$TARGET/data/update-status.json"
LOCK="$TARGET/data/update.lock"
DEPS_STAMP="$TARGET/data/deps.sha256"
mkdir -p "$TARGET/data"

# Keep the log bounded: the API reads the whole file to show its tail.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 262144 ]; then
    tail -c 65536 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exec >>"$LOG" 2>&1

# The lock lives on fd 9 for as long as this script does. Long-running children
# are started with 9>&- so a straggler (a pip subprocess outliving a killed
# run) can't keep holding it and lock out the next update.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "== update skipped $(date -Is): another update is running =="
    exit 0
fi

status() {
    # Written to a temp file and renamed, so a reader never sees half a line.
    printf '{"phase":"%s","ok":%s,"ts":"%s","pid":%s,"detail":"%s"}\n' \
        "$1" "$2" "$(date -Is)" "$$" "${3:-}" > "$STATUS.tmp"
    mv "$STATUS.tmp" "$STATUS"
}

PHASE=starting
FINISHED=0
PREV=""
DEPS_OK=0   # 1 once the packages on disk are known to match the tree
on_exit() {
    code=$?
    if [ "$FINISHED" != 1 ]; then
        # The tree may already be on the new commit while its dependencies
        # never finished installing. Go back, so whatever restarts the backend
        # next runs the old code on the packages it was running on.
        if [ -n "$PREV" ] && [ "$DEPS_OK" != 1 ] \
            && [ "$(git -C "$TARGET" rev-parse HEAD 2>/dev/null)" != "$PREV" ]; then
            echo "install did not complete; putting the tree back to $(git -C "$TARGET" rev-parse --short "$PREV")"
            git -C "$TARGET" reset --hard "$PREV" 9>&- || true
        fi
        status failed false "stopped while $PHASE (exit $code); see update.log"
        echo "== update FAILED while $PHASE $(date -Is) =="
    fi
}
trap on_exit EXIT
# Turn a kill into a normal exit so the EXIT trap above still runs.
trap 'exit 143' TERM INT HUP

echo "== update started $(date -Is) =="

PHASE=fetching
status fetching true
PREV=$(git -C "$TARGET" rev-parse HEAD)
timeout 180 git -C "$TARGET" fetch origin 9>&-
BRANCH=$(git -C "$TARGET" rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" = "HEAD" ]; then
    echo "checkout is detached; following origin/main"
    BRANCH=main
fi
git -C "$TARGET" reset --hard "origin/$BRANCH"
echo "now at $(git -C "$TARGET" rev-parse --short HEAD) (was $(git -C "$TARGET" rev-parse --short "$PREV"))"

WANT=$(sha256sum "$TARGET/pyproject.toml" | cut -d' ' -f1)
HAVE=$(cat "$DEPS_STAMP" 2>/dev/null || true)
if [ "$WANT" != "$HAVE" ]; then
    PHASE=installing
    status installing true
    # A failure here exits through on_exit, which rolls the tree back.
    "$TARGET/.venv/bin/pip" install --quiet --upgrade --disable-pip-version-check "$TARGET" 9>&-
    echo "$WANT" > "$DEPS_STAMP"
else
    echo "pyproject.toml unchanged since the last install; skipping pip"
fi
DEPS_OK=1

# Everything is written *before* the kill: stopping the main pid makes systemd
# tear down the whole cgroup, this script included, so nothing after that line
# is guaranteed to run.
PHASE=restarting
FINISHED=1
status done true
echo "== update finished $(date -Is) =="
MAIN_PID=$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || true)
if [ -n "$MAIN_PID" ] && [ "$MAIN_PID" != "0" ]; then
    kill "$MAIN_PID"
fi
