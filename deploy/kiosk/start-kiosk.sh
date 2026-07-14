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

# --password-store=basic makes Chromium use its built-in cookie store instead
# of the system keyring. On this headless kiosk the GNOME keyring is locked and
# pops a password prompt nobody can answer; Chromium then blocks on cookie init,
# which hangs every page navigation and leaves the panel a blank white screen.
while true; do
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
