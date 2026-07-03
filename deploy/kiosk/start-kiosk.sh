#!/bin/bash
# Kiosk launcher: waits for the backend, then runs Chromium in a restart loop
# (crash recovery + the admin panel's "restart kiosk" button, which pkills
# the browser by its unique user-data-dir).
# Started from ~/.config/labwc/autostart so it inherits the session env.

BACKEND_URL="http://localhost:8000"
PROFILE_DIR="$HOME/.config/recipehud-chromium"
EXTENSION_DIR="/opt/recipehud/extension"

until curl -sf "$BACKEND_URL/healthz" > /dev/null; do
    sleep 2
done

while true; do
    chromium-browser \
        --kiosk "$BACKEND_URL/" \
        --user-data-dir="$PROFILE_DIR" \
        --load-extension="$EXTENSION_DIR" \
        --autoplay-policy=no-user-gesture-required \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --hide-crash-restore-bubble \
        --disable-features=Translate \
        --disable-component-update \
        --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 \
        --touch-events=enabled \
        --ozone-platform=wayland \
        --start-maximized
    sleep 2
done
