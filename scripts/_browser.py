"""Minimal headless-browser helper for the test scripts: open a page, evaluate
JavaScript in it, get the JSON result back.

Drives the system Chromium over the DevTools protocol using `httpx` and
`websockets`, both of which the backend already depends on — so the tests run
with the appliance's own interpreter, without Playwright and its separate
browser download."""

import asyncio
import json
import shutil
import subprocess
import tempfile

import httpx
import websockets

CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")


def _binary() -> str:
    for name in CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit(f"no Chromium-family browser found (tried {', '.join(CANDIDATES)})")


async def _evaluate(url: str, expression: str, port: int, timeout: float):
    profile = tempfile.mkdtemp(prefix="recipehud-test-")
    proc = subprocess.Popen(
        [_binary(), "--headless=new", f"--user-data-dir={profile}", "--password-store=basic",
         "--disable-gpu", "--no-first-run", f"--remote-debugging-port={port}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        deadline = asyncio.get_running_loop().time() + timeout
        while target is None:
            if asyncio.get_running_loop().time() > deadline:
                raise SystemExit("browser did not open the page in time")
            await asyncio.sleep(0.3)
            try:
                pages = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
            except (httpx.HTTPError, ValueError):
                continue
            target = next((p for p in pages if p["type"] == "page" and p["url"].startswith(url)), None)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            call_id = 0

            async def run(expr: str) -> dict:
                nonlocal call_id
                call_id += 1
                await ws.send(json.dumps({"id": call_id, "method": "Runtime.evaluate", "params": {
                    "expression": expr, "awaitPromise": True, "returnByValue": True}}))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                    if msg.get("id") == call_id:
                        return msg.get("result", {})

            # The target reports its destination URL while the tab is still on
            # the initial empty document; scripts run there resolve nothing.
            while True:
                ready = (await run("document.readyState + '|' + location.href"))["result"].get("value", "")
                if ready.startswith("complete|" + url):
                    break
                if asyncio.get_running_loop().time() > deadline:
                    raise SystemExit(f"page never finished loading ({ready})")
                await asyncio.sleep(0.2)
            result = await run(expression)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise SystemExit("page script failed: "
                             + (detail.get("exception", {}).get("description") or detail.get("text", "")))
        return result["result"].get("value")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def evaluate(url: str, expression: str, port: int = 9333, timeout: float = 30):
    """Load `url`, evaluate `expression` (may be a promise), return its value."""
    return asyncio.run(_evaluate(url, expression, port, timeout))
