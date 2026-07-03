# Deployment (Raspberry Pi 4, Pi OS Bookworm)

## Prerequisites

- Raspberry Pi OS Bookworm **with desktop** (labwc Wayland session, the
  default), autologin enabled (default on a fresh image).
- VSDISPLAY 14.5" 2560×720 panel on HDMI, USB touch cable connected.
- Network access for the install (recipe browsing needs it too; timers,
  launcher and cached clean views work offline).

## Install

```bash
git clone <repo> ~/recipe_hud
cd ~/recipe_hud
bash deploy/install.sh
```

The script is idempotent (re-run after `git pull` to update). It:

1. apt-installs `chromium-browser wlopm wlr-randr kanshi python3-venv`
2. rsyncs the repo to `/opt/recipehud`, creates the venv, installs the package
3. adds your user to the `input` group (touch-wake needs it)
4. seeds the database
5. installs + enables the `recipehud-backend` systemd service
6. adds the kiosk launch line to `~/.config/labwc/autostart`
7. writes `~/.config/kanshi/config` with `transform 90` (portrait)
8. disables the OS screen blanking (the backend owns power management)

### Manual step: touch rotation

Touch coordinates must follow the display rotation. Merge
`deploy/labwc/rc.xml.snippet` into `~/.config/labwc/rc.xml`:

```xml
<touch deviceName="YOUR-TOUCH-DEVICE" mapToOutput="HDMI-A-1"/>
```

The install script prints candidate device names (or run
`sudo libinput list-devices | grep -B5 -i touch`). Then reboot.

## First boot checks

1. Kiosk boots portrait into the launcher; tiles open sites; touch is accurate.
2. Admin panel from a phone: `http://<pi>.local:8000/admin`
   (password `recipehud` — **change it** in the panel).
3. Timer alarm is audible (HDMI audio must route to the panel's speakers if
   present, else attach a speaker; check `raspi-config` → audio output).
4. Leave it idle: clock screen appears, later the panel powers off; a tap
   wakes it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Display never powers off | `wlopm` must run as the session user: check `systemctl status recipehud-backend`, confirm `XDG_RUNTIME_DIR=/run/user/1000` and `WAYLAND_DISPLAY=wayland-0` match your session (`echo $WAYLAND_DISPLAY` in a desktop terminal). Try `wlopm --off HDMI-A-1` manually. Output name is a setting in the admin panel. |
| Power off works, wake doesn't | Usually the touch listener: confirm the user is in `input` (`groups`), re-login/reboot after install. Set an explicit device in admin → "Touch device override" (e.g. `/dev/input/event4`). |
| Touch is 90° off | The `<touch mapToOutput>` line is missing/has the wrong device name. |
| No alarm sound | Wrong audio output (`sudo raspi-config` → System → Audio). Test in admin → "Test alarm sound" from the kiosk itself. |
| Overlay missing on pages | Chromium must load the extension: check `ps aux | grep load-extension`. If your Chromium build removed `--load-extension`, open `chrome://extensions` once in the kiosk profile and Load unpacked from `/opt/recipehud/extension` — it persists in the profile. |
| Kiosk shows an error page at boot | Backend not up yet is handled (start script waits for `/healthz`); check `journalctl -u recipehud-backend -e`. |
| Clean view fails on a site | Some sites block server-side fetches (Cloudflare). Keep that site on "direct" open mode. |

## Updating

Preferred: admin panel → System → **Update app**. It fetches the latest
code into `/opt/recipehud`, reinstalls dependencies, and restarts the
backend (running timers survive; the kiosk reconnects in seconds). Any local
edits made directly in `/opt/recipehud` are discarded — the appliance always
matches the repository. Note: the update button works after the first
`install.sh` run that delivered the git checkout; older installs need one
manual re-run of `install.sh`.

Manual fallback:

```bash
cd ~/recipe_hud && git pull && bash deploy/install.sh
sudo systemctl restart recipehud-backend
# then admin panel → Restart kiosk (or reboot)
```

## Backup & restore

Admin panel → System → **Download backup** produces a zip of the database
(sites, settings, presets, saved recipes + tags) and all saved-recipe
images. **Restore backup…** uploads one, validates it (integrity check,
schema compatibility), and applies it on an automatic restart; the previous
database is kept on the Pi as `recipehud.db.pre-restore` just in case.
Restores replace everything — do a fresh backup first if unsure.
