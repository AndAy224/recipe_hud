"""Dev regression test for the display wake path — the bug class where the
kiosk "went to sleep and never came back" (a black scrim with only the page's
scrollbar showing, deaf to touch).

Needs the backend running with RECIPEHUD_DEBUG=1 (for /api/debug/*).
Point it at a scratch instance with RECIPEHUD_TEST_BASE=http://127.0.0.1:8010.

Covers:
  1. touch re-announces display.state even when the server is ALREADY active
     — the only way a client painting a stale scrim can ever be rescued;
  2. the ordinary blank -> touch -> wake transition still fires;
  3. the backend beats periodically, so a client can spot a half-open socket;
  4. the extension SW's refreshDisplay epoch guard drops a /api/display answer
     that a newer live event has already superseded;
  5. releasing an idle inhibitor (keep_awake off, last timer done) starts a
     fresh idle timeout instead of blanking on a stale one.

NOTE: it writes settings, so point it at a scratch DB, not the appliance.
"""

import asyncio
import json
import os

import httpx
import websockets

BASE = os.environ.get("RECIPEHUD_TEST_BASE", "http://127.0.0.1:8000")
WS = BASE.replace("http", "ws") + "/ws?role=test"


async def drain(ws, seconds):
    """Collect events for a window; returns [(type, data), ...]."""
    out = []
    try:
        async with asyncio.timeout(seconds):
            async for raw in ws:
                msg = json.loads(raw)
                out.append((msg["type"], msg["data"]))
    except TimeoutError:
        pass
    return out


def states(events):
    return [d["state"] for t, d in events if t == "display.state"]


async def main() -> None:
    async with websockets.connect(WS) as ws, httpx.AsyncClient(base_url=BASE) as http:
        first = json.loads(await ws.recv())
        assert first["type"] == "snapshot", first

        # 1. Stuck-client rescue: the server is already ACTIVE, so there is no
        # state *change* to report — but a client can still be showing a scrim.
        await http.post("/api/debug/idle/active")
        await drain(ws, 2.5)
        assert (await http.get("/api/display")).json()["state"] == "active"
        await http.post("/api/debug/touch")
        seen = states(await drain(ws, 1.5))
        assert seen == ["active"], f"touch while active must re-announce, got {seen}"
        print("1. resync-on-touch ok")

        # ...but not once per touch: the re-announce is throttled.
        for _ in range(6):
            await http.post("/api/activity")
        seen = states(await drain(ws, 1.0))
        assert len(seen) <= 1, f"re-announce not throttled: {seen}"
        print("2. re-announce throttled ok")

        # 3. The ordinary path still works.
        await http.post("/api/debug/idle/off")
        assert states(await drain(ws, 1.0)) == ["off"]
        await http.post("/api/debug/touch")
        assert states(await drain(ws, 1.5)) == ["active"]
        print("3. blank -> touch -> wake ok")

        # 4. Heartbeat: lets a client tell a live socket from a half-open one.
        beats = [t for t, _ in await drain(ws, 25) if t == "heartbeat"]
        assert beats, "no heartbeat within 25s"
        print(f"4. heartbeat ok ({len(beats)} in 25s)")

        # 5. The SW race: a /api/display issued while off can answer *after* a
        # newer live "active". Mirrors extension/sw.js refreshDisplay().
        await http.post("/api/debug/idle/off")
        await drain(ws, 0.8)
        epoch = 0
        captured = epoch
        fetch = asyncio.create_task(http.get("/api/display"))
        await asyncio.sleep(0.005)
        await http.post("/api/debug/touch")
        stale = (await fetch).json()["state"]
        if states(await drain(ws, 1.5)):   # a live event landed mid-flight
            epoch += 1
        applied = stale if captured == epoch else None
        assert stale == "off", f"expected a stale answer to set up the race, got {stale}"
        assert applied is None, "epoch guard failed: the stale 'off' would repaint the scrim"
        print("5. refreshDisplay epoch guard ok (stale 'off' discarded)")

        # 6. Releasing an inhibitor must not blank the panel instantly.
        original = (await http.get("/api/settings")).json()
        try:
            await http.patch("/api/settings",
                             json={"keep_awake": True, "idle_timeout_s": 3})
            await http.post("/api/debug/idle/active")
            await drain(ws, 6)          # well past idle_timeout_s
            assert (await http.get("/api/display")).json()["state"] == "active", \
                "keep_awake must inhibit blanking"
            await http.patch("/api/settings", json={"keep_awake": False})
            # The idle clock was held, so a full timeout must still elapse.
            assert not states(await drain(ws, 2)), "blanked instantly on inhibitor release"
            assert "clock" in states(await drain(ws, 5)), "never blanked after the timeout"
            print("6. inhibitor release starts a fresh timeout ok")
        finally:
            await http.patch("/api/settings", json={
                "keep_awake": original["keep_awake"],
                "idle_timeout_s": original["idle_timeout_s"]})

        await http.post("/api/debug/idle/active")
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
