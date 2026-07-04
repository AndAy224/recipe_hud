#!/bin/bash
# Recipe HUD installer for Raspberry Pi OS Bookworm/Trixie (labwc/Wayland).
# Idempotent: safe to re-run after git pull. Run (without sudo) as the desktop
# user the Pi Imager created — any username works, there is no default "pi":
#   bash deploy/install.sh
set -euo pipefail

TARGET=/opt/recipehud
USER_NAME="${SUDO_USER:-$USER}"
USER_UID="$(id -u "$USER_NAME")"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Recipe HUD install (user: $USER_NAME) =="

echo "-- apt packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    wlopm wlr-randr kanshi python3-venv python3-dev curl \
    fonts-noto-color-emoji
# fonts-noto-color-emoji: the whole UI uses emoji as icons (site tiles, timers,
# buttons). Without a color-emoji font they render blank on a fresh Pi.
# Chromium's package name varies by Pi OS release: "chromium" on Bookworm/Trixie,
# "chromium-browser" on older Buster/Bullseye. Install whichever is available.
if ! sudo apt-get install -y --no-install-recommends chromium; then
    sudo apt-get install -y --no-install-recommends chromium-browser
fi

echo "-- sync repo to $TARGET"
if [ "$REPO_DIR" != "$TARGET" ]; then
    sudo mkdir -p "$TARGET"
    # .git is included on purpose: the admin panel's self-update button runs
    # `git fetch` in /opt/recipehud (see deploy/update.sh).
    sudo rsync -a --delete --exclude .venv --exclude data \
        "$REPO_DIR/" "$TARGET/"
    sudo chown -R "$USER_NAME:$USER_NAME" "$TARGET"
fi
chmod +x "$TARGET/deploy/update.sh" "$TARGET/deploy/kiosk/start-kiosk.sh"

echo "-- python venv"
if [ ! -d "$TARGET/.venv" ]; then
    python3 -m venv "$TARGET/.venv"
fi
"$TARGET/.venv/bin/pip" install --quiet --upgrade pip
"$TARGET/.venv/bin/pip" install --quiet "$TARGET"

echo "-- permissions: add $USER_NAME to input group (touch wake)"
sudo usermod -aG input "$USER_NAME"

echo "-- seed database"
RECIPEHUD_DB_PATH="$TARGET/data/recipehud.db" "$TARGET/.venv/bin/python" "$TARGET/scripts/seed_db.py"

echo "-- systemd backend service (rendered for user=$USER_NAME uid=$USER_UID)"
# Render __USER__/__UID__ placeholders for the actual desktop user. Hardcoding
# User=pi fails with status=217/USER on modern Pi OS, where there is no "pi" user.
sed -e "s|__USER__|$USER_NAME|g" -e "s|__UID__|$USER_UID|g" \
    "$TARGET/deploy/systemd/recipehud-backend.service" \
    | sudo tee /etc/systemd/system/recipehud-backend.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now recipehud-backend.service

echo "-- labwc autostart (kiosk)"
AUTOSTART="$HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$AUTOSTART")"
chmod +x "$TARGET/deploy/kiosk/start-kiosk.sh"
if ! grep -q "start-kiosk.sh" "$AUTOSTART" 2>/dev/null; then
    echo "$TARGET/deploy/kiosk/start-kiosk.sh &" >> "$AUTOSTART"
    echo "   added kiosk line to $AUTOSTART"
fi

echo "-- kanshi portrait rotation"
KANSHI="$HOME/.config/kanshi/config"
mkdir -p "$(dirname "$KANSHI")"
# Detect the connected HDMI port. The DRM connector suffix (HDMI-A-1 / HDMI-A-2)
# is exactly the output name labwc/wlr uses, and sysfs is readable without a
# Wayland session (works over SSH). The Pi's two HDMI ports are HDMI-A-1 and -2;
# the panel can be on either, so never hardcode it.
OUTPUT=""
for s in /sys/class/drm/card*-HDMI-A-*/status; do
    [ -e "$s" ] || continue
    if [ "$(cat "$s")" = "connected" ]; then
        conn="$(basename "$(dirname "$s")")"   # e.g. card1-HDMI-A-2
        OUTPUT="${conn#*-}"                     # -> HDMI-A-2
        break
    fi
done
OUTPUT="${OUTPUT:-HDMI-A-1}"
# (Re)write when missing, empty, or lacking a transform: a leftover empty file
# silently disables rotation and leaves the panel stuck in landscape.
if [ ! -s "$KANSHI" ] || ! grep -q "transform" "$KANSHI"; then
    cat > "$KANSHI" <<EOF
# ~/.config/kanshi/config — portrait rotation for the VSDISPLAY panel.
# Written by deploy/install.sh (auto-detected output: $OUTPUT).
# transform 90 turns the native 2560x720 landscape into 720x2560 portrait.
profile {
    output $OUTPUT mode 2560x720 transform 90
}
EOF
    echo "   wrote $KANSHI (output $OUTPUT, mode 2560x720, transform 90)"
else
    echo "   $KANSHI already configured — verify it targets: output $OUTPUT transform 90"
fi
if ! grep -q "^kanshi" "$AUTOSTART" 2>/dev/null; then
    sed -i '1i kanshi &' "$AUTOSTART"
fi

echo "-- disable OS screen blanking (the backend owns power management)"
sudo raspi-config nonint do_blanking 1 || echo "   (raspi-config blanking step skipped)"

echo ""
echo "== Manual step: touch rotation =="
echo "Merge deploy/labwc/rc.xml.snippet into ~/.config/labwc/rc.xml with your"
echo "touch device name and mapToOutput=\"$OUTPUT\" (the detected panel output)."
echo "Touch device candidates found on this system:"
sudo libinput list-devices 2>/dev/null | grep -i -B5 touch | grep "^Device:" | sed 's/^Device:/   /' || echo "   (libinput list-devices unavailable)"

echo ""
echo "== Done =="
IP=$(hostname -I | awk '{print $1}')
echo "Launcher:    http://localhost:8000/  (kiosk shows this after reboot)"
echo "Admin panel: http://$IP:8000/admin  or  http://$(hostname).local:8000/admin"
echo "Default admin password: recipehud  — change it in the admin panel!"
echo "Reboot to start the kiosk: sudo reboot"
