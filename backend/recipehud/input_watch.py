"""Global touch listener: any touch event resets the idle timer, which is
what wakes the display even while the panel is powered off (the USB touch
controller keeps reporting). Linux-only; silently absent elsewhere."""

import asyncio
import logging
import sys
import time

log = logging.getLogger(__name__)


def start(idle, store) -> asyncio.Task | None:
    if sys.platform != "linux":
        return None
    try:
        import evdev  # noqa: F401
    except ImportError:
        log.warning("evdev not installed; touch wake disabled")
        return None
    return asyncio.create_task(_watch(idle, store))


def _find_touch_devices(store):
    import evdev
    override = store.get("touch_device")
    if override:
        try:
            return [evdev.InputDevice(override)]
        except OSError as exc:
            log.warning("touch_device %s: %s", override, exc)
            return []
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            abs_codes = {code for code, _ in caps.get(evdev.ecodes.EV_ABS, [])}
            if evdev.ecodes.ABS_MT_POSITION_X in abs_codes or (
                evdev.ecodes.ABS_X in abs_codes
                and evdev.ecodes.BTN_TOUCH in caps.get(evdev.ecodes.EV_KEY, [])
            ):
                devices.append(dev)
            else:
                dev.close()
        except OSError:
            continue
    return devices


async def _watch(idle, store) -> None:
    while True:
        devices = _find_touch_devices(store)
        if not devices:
            log.info("no touch device found; rescanning in 30s")
            await asyncio.sleep(30)
            continue
        log.info("watching touch input: %s", ", ".join(d.name for d in devices))
        readers = [asyncio.create_task(_read(dev, idle)) for dev in devices]
        # A reader exits on device error/unplug; rescan everything.
        await asyncio.wait(readers, return_when=asyncio.FIRST_COMPLETED)
        for task in readers:
            task.cancel()
        for dev in devices:
            try:
                dev.close()
            except OSError:
                pass
        await asyncio.sleep(5)


async def _read(dev, idle) -> None:
    import evdev
    last = 0.0
    try:
        async for event in dev.async_read_loop():
            if event.type in (evdev.ecodes.EV_ABS, evdev.ecodes.EV_KEY):
                now = time.monotonic()
                if now - last > 0.5:
                    last = now
                    idle.activity("touch")
    except OSError as exc:
        log.warning("touch device %s dropped: %s", dev.path, exc)
