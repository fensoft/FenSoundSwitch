"""Safe macOS implementations for the portable host boundary.

Native volume-key interception, notification-area menus, monitor identity,
and endpoint policy are Windows-specific and are intentionally unavailable.
"""
from __future__ import annotations

import fcntl
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class PlatformError(RuntimeError):
    pass


class InstanceAlreadyRunningError(PlatformError):
    pass


@dataclass(frozen=True)
class WindowsMonitorIdentity:
    display_device_name: str
    device_path: str
    manufacturer_id: str | None = None
    product_code: int | None = None
    serial_number: str | None = None
    monitor_description: str | None = None


@dataclass(frozen=True)
class WindowsDdcMonitor:
    handle: int
    description: str
    identity: WindowsMonitorIdentity | None


@dataclass(frozen=True)
class ScreenRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class DisplayArea:
    display_device_name: str
    bounds: ScreenRect
    work_area: ScreenRect
    scale_percent: int = 100


@dataclass(frozen=True)
class TrayMonitorMenuItem:
    label: str
    selection: object
    active: bool = False


@dataclass(frozen=True)
class TraySignalMenuItem:
    label: str
    signal_id: str


@dataclass(frozen=True)
class TrayMenuState:
    active_monitor: str | None
    current_volume: int | None
    routing_status: str
    monitors: tuple[TrayMonitorMenuItem, ...] = ()
    signals: tuple[TraySignalMenuItem, ...] = ()


def enumerate_windows_monitor_identities() -> list[WindowsMonitorIdentity]:
    """DDC identity correlation has no safe public macOS equivalent yet."""
    return []


def enumerate_windows_ddc_monitors() -> list[WindowsDdcMonitor]:
    return []


class SingleInstanceGuard:
    """Current-user advisory lock that prevents duplicate macOS processes."""

    def __init__(self) -> None:
        directory = Path.home() / "Library" / "Application Support" / "FenSoundSwitch"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._stream = (directory / "instance.lock").open("a+")
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._stream.close()
            raise InstanceAlreadyRunningError("FenSoundSwitch is already running.") from exc
        self._stream.write(str(os.getpid()))
        self._stream.flush()

    def close(self) -> None:
        stream = getattr(self, "_stream", None)
        if stream is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
            self._stream = None


def request_existing_instance_restore() -> None:
    # There is no cross-process restore protocol until a native macOS app shell
    # exists. A duplicate process still exits safely through the guard.
    return


class DisplayChangeListener:
    def __init__(self, on_change: Callable[[], None], on_error: Callable[[Exception], None], on_theme_change: Callable[[], None]) -> None:
        self.on_change = on_change
        self.on_error = on_error
        self.on_theme_change = on_theme_change
        self.is_active = False

    def start(self, timeout: float = 2.0) -> None:
        self.is_active = True

    def stop(self, timeout: float = 2.0) -> bool:
        self.is_active = False
        return True


class GlobalVolumeKeyListener:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.is_active = False

    def start(self, timeout: float = 2.0) -> None:
        raise PlatformError("Global Volume Up/Down interception is unavailable on macOS.")

    def stop(self, timeout: float = 2.0) -> bool:
        return True


class TrayIconController:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def update_menu_state(self, state: TrayMenuState) -> None:
        return

    def start(self, timeout: float = 2.0) -> None:
        raise PlatformError("The macOS menu-bar integration is not available in this build.")

    def show(self, timeout: float = 2.0) -> bool:
        return False

    def hide(self, timeout: float = 2.0) -> bool:
        return True

    def stop(self, timeout: float = 2.0) -> bool:
        return True


def get_toplevel_window_handle(window_id: int) -> int:
    return window_id


def get_window_dpi(_window_handle: int) -> int:
    return 96


def is_high_contrast_enabled() -> bool:
    return False


def set_window_dark_mode(_window_handle: int, _enabled: bool) -> bool:
    return True


def get_overlay_display_area(_preferred_display_device_name: str | None = None) -> DisplayArea:
    """Map Cocoa's primary visible frame into Tk's top-left coordinates."""
    try:
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is not None:
            frame = screen.frame()
            visible = screen.visibleFrame()
            # Cocoa measures Y from the bottom; Tk measures it from the top.
            top = round(frame.origin.y + frame.size.height - (visible.origin.y + visible.size.height))
            left = round(visible.origin.x)
            width = round(visible.size.width)
            height = round(visible.size.height)
            full_top = round(frame.origin.y)
            full_left = round(frame.origin.x)
            return DisplayArea(
                "primary",
                ScreenRect(full_left, full_top, full_left + round(frame.size.width), full_top + round(frame.size.height)),
                ScreenRect(left, top, left + width, top + height),
            )
    except Exception:
        pass
    # A minimal fallback keeps the overlay usable if PyObjC is unavailable.
    return DisplayArea("primary", ScreenRect(0, 0, 1440, 900), ScreenRect(0, 24, 1440, 876))


def configure_no_activate_window(_window_handle: int) -> bool:
    return True


def show_window_no_activate(_window_handle: int, _x: int, _y: int, _width: int, _height: int) -> bool:
    return True
