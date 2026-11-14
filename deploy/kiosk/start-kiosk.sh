#!/bin/bash
# Kiosk launcher: waits for the backend, then runs Chromium in a restart loop
# (crash recovery + the admin panel's "restart kiosk" button, which pkills
# the browser by its unique user-data-dir).
# Started from ~/.config/labwc/autostart so it inherits the session env.

BACKEND_URL="http://localhost:8000"
PROFILE_DIR="$HOME/.config/recipehud-chromium"
EXTENSION_DIR="/opt/recipehud/extension"

# The binary is "chromium" on Bookworm/Trixie and "chromium-browser" on older
# Pi OS. Resolve whichever exists so the kiosk launches on either.
CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM" ]; then
    echo "start-kiosk: no chromium binary found (tried chromium, chromium-browser)" >&2
    exit 1
fi

until curl -sf "$BACKEND_URL/healthz" > /dev/null; do
    sleep 2
done

# Chromium stores an unpacked extension's service worker in the profile the
# first time it runs, and with --load-extension it NEVER refreshes that copy on
# a later start — not when sw.js changes on disk, not even when the manifest
# version is bumped. Content scripts are re-read from disk every time, so the
# kiosk ends up with today's overlay talking to a months-old service worker
# (this is how the July sw.js outlived every later fix to it, and why the
# overlay's buttons went dead once it started routing requests through the
# worker). The registration can't be edited, only dropped: when the extension's
# files differ from the ones the profile last started with, remove the
# profile's stored service workers so the next start registers ours from disk.
# Other extensions and sites simply re-register theirs.
refresh_extension_worker() {
    local stamp="$PROFILE_DIR/.recipehud-extension.sha256" want have
    want=$(find "$EXTENSION_DIR" -type f -print0 2>/dev/null | sort -z \
        | xargs -0 -r sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)
    have=$(cat "$stamp" 2>/dev/null || true)
    if [ "$want" != "$have" ]; then
        rm -rf "$PROFILE_DIR"/*/"Service Worker"
        mkdir -p "$PROFILE_DIR"
        echo "$want" > "$stamp"
        echo "start-kiosk: extension files changed; cleared the profile's stored service workers" >&2
    fi
}

# --password-store=basic makes Chromium use its built-in cookie store instead
# of the system keyring. On this headless kiosk the GNOME keyring is locked and
# pops a password prompt nobody can answer; Chromium then blocks on cookie init,
# which hangs every page navigation and leaves the panel a blank white screen.
while true; do
    # Before every launch, so a self-update followed by "Restart kiosk" is enough.
    refresh_extension_worker
    "$CHROMIUM" \
        --kiosk "$BACKEND_URL/" \
        --user-data-dir="$PROFILE_DIR" \
        --load-extension="$EXTENSION_DIR" \
        --autoplay-policy=no-user-gesture-required \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --hide-crash-restore-bubble \
        --password-store=basic \
        --disable-features=Translate \
        --disable-component-update \
        --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 \
        --touch-events=enabled \
        --ozone-platform=wayland \
        --start-maximized
    sleep 2
done
