"""Dev smoke test: connect to /ws, start a 3s timer over REST, and assert the
snapshot, ticks, alarm.start and (after dismiss) alarm.stop all arrive.

It really rings: pointed at the appliance (the default, :8000) the kitchen
alarm sounds for a moment. Use RECIPEHUD_TEST_BASE=http://127.0.0.1:8010 to
run it against a scratch instance instead."""

import asyncio
import json
import os

import httpx
import websockets

BASE = os.environ.get("RECIPEHUD_TEST_BASE", "http://127.0.0.1:8000")


async def main() -> None:
    seen = []
    async with websockets.connect(BASE.replace("http", "ws") + "/ws?role=test") as ws:
        first = json.loads(await ws.recv())
        if first["type"] != "snapshot":
            raise SystemExit(f"FAIL: first frame was {first['type']}, not snapshot")
        print("snapshot ok:", list(first["data"].keys()))

        async with httpx.AsyncClient() as client:
            resp = await client.post(BASE + "/api/timers",
                                     json={"label": "smoke", "seconds": 3})
            resp.raise_for_status()
            timer_id = resp.json()["id"]

        alarm_seen = False
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            seen.append(msg["type"])
            # Someone's real timer may ring meanwhile: only ours counts.
            mine = isinstance(msg["data"], dict) and msg["data"].get("id") == timer_id
            if msg["type"] == "alarm.start" and mine:
                alarm_seen = True
                print("alarm.start ok:", msg["data"])
                async with httpx.AsyncClient() as client:
                    await client.post(f"{BASE}/api/timers/{timer_id}/dismiss")
            if msg["type"] == "alarm.stop" and mine:
                if not alarm_seen:
                    raise SystemExit("FAIL: alarm.stop before alarm.start")
                print("alarm.stop ok")
                break
    if "timer.created" not in seen or "timer.tick" not in seen:
        raise SystemExit(f"FAIL: missing events, saw {seen}")
    print("event order:", " -> ".join(dict.fromkeys(seen)))
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
