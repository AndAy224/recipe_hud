import os
import sys

from ..config import Config
from ..settings_store import SettingsStore
from .base import DisplayBackend
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
