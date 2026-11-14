import os
import shutil
import sys

from ..config import Config
from ..settings_store import SettingsStore
from .base import DisplayBackend
from .ddc import DdcBacklight, NullBacklight
from .mock import MockDisplay
from .wlopm import WlopmDisplay
from .x11 import X11Display


def select_backend(cfg: Config, store: SettingsStore) -> DisplayBackend:
    choice = cfg.display_backend
    if choice == "auto":
        if sys.platform != "linux":
            choice = "mock"
        elif os.environ.get("WAYLAND_DISPLAY"):
            choice = "wlopm"
        elif os.environ.get("DISPLAY"):
            choice = "x11"
        else:
            choice = "mock"
    if choice == "wlopm":
        return WlopmDisplay(store)
    if choice == "x11":
        return X11Display()
    return MockDisplay()


def select_backlight(cfg: Config, store: SettingsStore):
    """ddcutil when available — but never under the mock display backend: a
    scratch/dev instance on the Pi must not fight the appliance for the real
    panel's brightness. Override with RECIPEHUD_BACKLIGHT=ddc|none."""
    choice = cfg.backlight
    if choice == "auto":
        if cfg.display_backend == "mock" or sys.platform != "linux":
            return NullBacklight("mock display backend")
        choice = "ddc" if shutil.which("ddcutil") else "none"
    if choice == "ddc":
        return DdcBacklight(store)
    return NullBacklight("ddcutil not installed" if cfg.backlight == "auto" else "disabled")
