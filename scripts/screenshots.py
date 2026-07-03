"""Generate README screenshots against a running dev backend (port 8000).
Usage: python scripts/screenshots.py   (backend must run with RECIPEHUD_DEBUG=1)"""

import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "screenshots"
RECIPE_URL = "https://www.budgetbytes.com/dragon-noodles/"

# Playwright's headless shell can't load MV3 extensions, so for the
# "overlay on a real site" shots we inject the actual content script with a
# stubbed chrome.runtime port and feed it a real snapshot from the backend —
# rendering-wise identical to the extension.
CHROME_STUB = """
window.__rhudListeners = [];
// The overlay uses a closed shadow root; capture a reference so the
// screenshot script can click its buttons.
const origAttachShadow = Element.prototype.attachShadow;
Element.prototype.attachShadow = function (init) {
  const root = origAttachShadow.call(this, init);
  if (!window.__rhudRoot) window.__rhudRoot = root;
  return root;
};
window.chrome = {
  runtime: {
    connect: () => ({
      onMessage: { addListener: (fn) => window.__rhudListeners.push(fn) },
      onDisconnect: { addListener: () => {} },
      postMessage: () => {},
    }),
  },
};
"""

# The page's CSP blocks main-world fetches to localhost (a real extension
# content script isn't subject to it), so the panel's preset row comes back
# empty in this harness; fill it with what the backend would have returned.
FILL_PRESETS = """
const presets = [["Soft eggs", "6:00"], ["Pasta", "9:00"], ["Rice", "15:00"], ["Pizza", "12:00"]];
const row = window.__rhudRoot.querySelector(".presets");
row.replaceChildren(...presets.map(([label, time]) => {
  const btn = document.createElement("button");
  btn.textContent = `${label} ${time}`;
  return btn;
}));
"""

KIOSK = {"width": 720, "height": 1280}
PHONE = {"width": 400, "height": 820}
DESKTOP = {"width": 860, "height": 1150}


def setup_state() -> list[str]:
    """Timers + saved recipe + active display so the shots look lived-in."""
    timer_ids = []
    with httpx.Client(base_url=BASE, timeout=30) as client:
        client.post("/api/recipe/save", json={"url": RECIPE_URL})
        for label, seconds in [("Pasta", 527), ("Sauce", 192)]:
            resp = client.post("/api/timers", json={"label": label, "seconds": seconds})
            timer_ids.append(resp.json()["id"])
        client.post("/api/debug/idle/active")
    return timer_ids


def cleanup(timer_ids: list[str]) -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        for timer_id in timer_ids:
            client.post(f"/api/timers/{timer_id}/cancel")
        client.post("/api/debug/idle/active")


def shot(page, name: str) -> None:
    path = OUT / name
    page.screenshot(path=str(path))
    print(f"wrote {path}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    timer_ids = setup_state()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # Launcher: clock, timers strip, My Recipes, site tiles
            page = browser.new_page(viewport=KIOSK)
            page.goto(BASE + "/", wait_until="networkidle")
            page.wait_for_timeout(1500)  # WS snapshot + first tick
            shot(page, "launcher.png")

            # New-timer sheet with keypad
            page.click("#new-timer")
            page.wait_for_timeout(600)
            shot(page, "timer-sheet.png")
            page.click("#timer-cancel")

            # Clock idle screen (fallback scrim; extension absent in this browser)
            httpx.post(BASE + "/api/debug/idle/clock", timeout=10)
            page.wait_for_timeout(1500)
            shot(page, "clock.png")
            httpx.post(BASE + "/api/debug/idle/active", timeout=10)
            page.close()

            # Clean recipe view
            page = browser.new_page(viewport=KIOSK)
            page.goto(f"{BASE}/recipe?url={RECIPE_URL}", wait_until="networkidle")
            page.wait_for_selector("#recipe:not([hidden])")
            page.wait_for_timeout(1200)  # hero image
            shot(page, "recipe.png")

            # Cook mode
            page.click("#cook-mode-btn")
            page.wait_for_timeout(500)
            shot(page, "cookmode.png")
            page.close()

            # A real recipe site with the extension overlay (toolbar + panel)
            snapshot = {
                "timers": httpx.get(BASE + "/api/timers", timeout=10).json(),
                "display": {"state": "active", "night": False},
                "settings": {"night_dim_enabled": True, "alarm_volume": 80},
            }
            overlay_js = (REPO / "extension" / "content" / "overlay.js").read_text(encoding="utf-8")
            page = browser.new_page(viewport=KIOSK)
            page.goto(RECIPE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)  # late-loading site chrome
            # Consent banners would dominate the shot; drop common CMP roots.
            page.evaluate("""document.querySelectorAll(
                '[id*=onetrust], [id*=consent], [class*=consent], [id*=cmp], [class*=cookie]'
            ).forEach(el => el.remove())""")
            page.evaluate(CHROME_STUB)
            page.evaluate(overlay_js)
            page.evaluate(
                "snap => window.__rhudListeners.forEach(fn => fn({type: 'snapshot', data: snap}))",
                snapshot,
            )
            page.wait_for_timeout(400)
            shot(page, "site-overlay.png")
            # Invoke the handler directly: the site's own capture-phase click
            # interception (CMP left unanswered in headless) swallows real clicks.
            page.evaluate("window.__rhudRoot.querySelector('.tb-timers').onclick()")
            page.wait_for_timeout(800)
            page.evaluate(FILL_PRESETS)
            shot(page, "site-overlay-panel.png")
            page.close()

            # Admin panel
            page = browser.new_page(viewport=DESKTOP)
            page.goto(BASE + "/admin/", wait_until="networkidle")
            page.wait_for_timeout(1200)
            shot(page, "admin.png")
            page.close()

            # Send-from-phone page
            page = browser.new_page(viewport=PHONE)
            page.goto(BASE + "/send/", wait_until="networkidle")
            page.fill("#url", "https://www.seriouseats.com/foolproof-pan-pizza-recipe")
            shot(page, "send.png")
            page.close()

            browser.close()
    finally:
        cleanup(timer_ids)


if __name__ == "__main__":
    main()
