# Deployment (Raspberry Pi 4, Pi OS Bookworm or Trixie)

## Prerequisites

- Raspberry Pi OS Bookworm or Trixie **with desktop** (labwc Wayland session,
  the default), autologin enabled (default on a fresh image).
- VSDISPLAY 14.5" 2560×720 panel on HDMI, USB touch cable connected.
- Network access for the install (recipe browsing needs it too; timers,
  launcher and cached clean views work offline).

## Install

```bash
git clone <repo> ~/recipe_hud
cd ~/recipe_hud
bash deploy/install.sh
```

Run it as the desktop user, **not** with sudo (it asks for sudo itself where
needed, and refuses to run as root). It is idempotent (re-run after
`git pull` to update). It:

1. apt-installs `chromium` (or `chromium-browser` on older releases), `wlopm
   wlr-randr kanshi python3-venv python3-dev curl git rsync ddcutil
   libinput-tools fonts-noto-color-emoji`, and `squeekboard` (on-screen
   keyboard) where available
2. rsyncs the repo to `/opt/recipehud`, creates the venv, installs the package
3. adds your user to the `input` group (touch-wake needs it)
4. seeds the database on a first install (an existing database is left alone)
5. installs, enables and (re)starts the `recipehud-backend` systemd service
6. installs `/etc/chromium.d/50-recipehud-keyring` (keeps Chromium off the
   locked system keyring — without it the kiosk boots to a white screen)
7. adds `kanshi`, `squeekboard` and the kiosk launch line to
   `~/.config/labwc/autostart`
8. writes `~/.config/kanshi/config` with `transform 90` (portrait)
9. disables the OS screen blanking (the backend owns power management)

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
2. Admin panel from a phone: `http://<pi>.local:8000/admin` (no login).
3. Timer alarm is audible (HDMI audio must route to the panel's speakers if
   present, else attach a speaker; check `raspi-config` → audio output).
4. Leave it idle: the clock screen appears, later the screen goes black (and
   the backlight dims, where the panel supports DDC/CI); a tap wakes it. The
   panel's *power* is deliberately left on — see the first troubleshooting row.
   While a seasonal screensaver deck is in season, the clock screen is the
   screensaver and stays up all day.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Display never powers off | By design. The idle "off" state paints a black screen and dims the backlight; it does not cut panel power, because many HDMI touch panels (this one included) power their USB touch controller from the monitor, and a powered-off panel then can't be woken by touch. Real power-off is the opt-in `panel_power_off` setting (API only: `curl -X PATCH :8000/api/settings -d '{"panel_power_off": true}'`) — enable it only on hardware whose touch demonstrably survives. If it is on and still nothing happens: `wlopm` must run as the session user, so check `systemctl status recipehud-backend`, confirm `XDG_RUNTIME_DIR=/run/user/<your uid>` (`id -u`) and `WAYLAND_DISPLAY=wayland-0` match your session, and try `wlopm --off <output>` by hand. The output name is a setting in the admin panel. |
| Overlay buttons do nothing on a recipe site | The overlay's requests go through the extension's service worker (a page can't reach `localhost` under Chromium's Local Network Access rules). After an update that touched `extension/`, restart the kiosk (admin → Restart kiosk) so Chromium reloads it. Chromium keeps the *first* service worker an unpacked extension ever registered; `start-kiosk.sh` clears that stored copy whenever the extension's files change. If the buttons are dead anyway, force it: stop the kiosk browser, delete `~/.config/recipehud-chromium/Default/Service Worker`, and let it relaunch. |
| A change to `deploy/kiosk/start-kiosk.sh` has no effect | The script is started once per desktop session and keeps running its loop; "Restart kiosk" only restarts the browser inside it. Reboot (or log out and in) to pick up the new script. |
| Power off works, wake doesn't | Usually the touch listener: confirm the user is in `input` (`groups`), re-login/reboot after install. Set an explicit device in admin → "Touch device override" (e.g. `/dev/input/event4`). |
| Touch is 90° off | The `<touch mapToOutput>` line is missing/has the wrong device name. |
| No alarm sound | Wrong audio output (`sudo raspi-config` → System → Audio). Test in admin → "Test alarm sound" from the kiosk itself. |
| Overlay missing on pages | Chromium must load the extension: check `ps aux | grep load-extension`. If your Chromium build removed `--load-extension`, open `chrome://extensions` once in the kiosk profile and Load unpacked from `/opt/recipehud/extension` — it persists in the profile. |
| Kiosk shows an error page at boot | Backend not up yet is handled (start script waits for `/healthz`); check `journalctl -u recipehud-backend -e`. |
| Clean view fails on a site | Most bot-blockers are already defeated (browser headers + `curl_cffi` TLS impersonation). If a site still refuses or serves no recipe data, keep it on "direct" open mode. On 32-bit Pi OS `curl_cffi` has no prebuilt wheel and is skipped — use 64-bit Pi OS for best extraction coverage. |

## Updating

Preferred: admin panel → System → **Update app**. It fetches the latest
code into `/opt/recipehud`, reinstalls dependencies when `pyproject.toml`
changed, and restarts the backend (running timers survive; the kiosk
reconnects in seconds). If the dependency install fails or is interrupted,
the code is put back to the previous version and the update shows as failed. Any local
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
