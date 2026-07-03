"""Dev smoke test: connect to /ws, start a 3s timer over REST, and assert the
snapshot, ticks, alarm.start and (after dismiss) alarm.stop all arrive."""

import asyncio
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    seen = []
    async with websockets.connect(BASE.replace("http", "ws") + "/ws?role=test") as ws:
        first = json.loads(await ws.recv())
        assert first["type"] == "snapshot", first
        print("snapshot ok:", list(first["data"].keys()))

        async with httpx.AsyncClient() as client:
            resp = await client.post(BASE + "/api/timers",
                                     json={"label": "smoke", "seconds": 3})
            timer_id = resp.json()["id"]

        alarm_seen = False
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            seen.append(msg["type"])
            if msg["type"] == "alarm.start":
                alarm_seen = True
                print("alarm.start ok:", msg["data"])
                async with httpx.AsyncClient() as client:
                    await client.post(f"{BASE}/api/timers/{timer_id}/dismiss")
            if msg["type"] == "alarm.stop":
                assert alarm_seen
                print("alarm.stop ok")
                break
    assert "timer.created" in seen and "timer.tick" in seen, seen
    print("event order:", " -> ".join(dict.fromkeys(seen)))
    print("PASS")


asyncio.run(main())
