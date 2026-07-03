"""Generate README screenshots against a running dev backend (port 8000).
Usage: python scripts/screenshots.py   (backend must run with RECIPEHUD_DEBUG=1)"""

import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
RECIPE_URL = "https://www.budgetbytes.com/dragon-noodles/"

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
