#!/bin/bash
# Recipe HUD installer for Raspberry Pi OS Bookworm (labwc/Wayland).
# Idempotent: safe to re-run after git pull. Run as the desktop user (pi):
#   bash deploy/install.sh
set -euo pipefail

TARGET=/opt/recipehud
USER_NAME="${SUDO_USER:-$USER}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Recipe HUD install (user: $USER_NAME) =="

echo "-- apt packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    chromium-browser wlopm wlr-randr kanshi python3-venv python3-dev curl

echo "-- sync repo to $TARGET"
if [ "$REPO_DIR" != "$TARGET" ]; then
    sudo mkdir -p "$TARGET"
    sudo rsync -a --delete --exclude .venv --exclude data --exclude .git \
        "$REPO_DIR/" "$TARGET/"
    sudo chown -R "$USER_NAME:$USER_NAME" "$TARGET"
fi

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

echo "-- systemd backend service"
sudo cp "$TARGET/deploy/systemd/recipehud-backend.service" /etc/systemd/system/
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
if [ ! -f "$KANSHI" ]; then
    mkdir -p "$(dirname "$KANSHI")"
    cp "$TARGET/deploy/labwc/kanshi.config.snippet" "$KANSHI"
    echo "   wrote $KANSHI (transform 90)"
else
    echo "   $KANSHI already exists — verify it contains: output HDMI-A-1 transform 90"
fi
if ! grep -q "^kanshi" "$AUTOSTART" 2>/dev/null; then
    sed -i '1i kanshi &' "$AUTOSTART"
fi

echo "-- disable OS screen blanking (the backend owns power management)"
sudo raspi-config nonint do_blanking 1 || echo "   (raspi-config blanking step skipped)"

echo ""
echo "== Manual step: touch rotation =="
echo "Merge deploy/labwc/rc.xml.snippet into ~/.config/labwc/rc.xml with your"
echo "touch device name. Candidates found on this system:"
sudo libinput list-devices 2>/dev/null | grep -i -B5 touch | grep "^Device:" | sed 's/^Device:/   /' || echo "   (libinput list-devices unavailable)"

echo ""
echo "== Done =="
IP=$(hostname -I | awk '{print $1}')
echo "Launcher:    http://localhost:8000/  (kiosk shows this after reboot)"
echo "Admin panel: http://$IP:8000/admin  or  http://$(hostname).local:8000/admin"
echo "Default admin password: recipehud  — change it in the admin panel!"
echo "Reboot to start the kiosk: sudo reboot"
