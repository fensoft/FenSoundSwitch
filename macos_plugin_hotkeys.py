"""macOS shortcut placeholder until an accessibility-approved event tap exists."""
from __future__ import annotations

from typing import Callable

from native_platform import PlatformError


class HotkeyRegistrationError(PlatformError):
    pass


class HotkeyConflictError(HotkeyRegistrationError):
    pass


class PluginHotkeyController:
    def __init__(self, on_hotkey: Callable[[str], None], on_error: Callable[[Exception], None], on_route_key: Callable[[str, bool], None] | None = None) -> None:
        self.on_hotkey = on_hotkey
        self.on_error = on_error
        self.on_route_key = on_route_key or (lambda _binding_id, _pressed: None)
        self.is_active = False

    def start(self, timeout: float = 2.0) -> None:
        raise PlatformError("Global plugin shortcuts are unavailable on macOS.")

    def set_binding(self, *args: object, **kwargs: object) -> None:
        raise HotkeyRegistrationError("Global plugin shortcuts are unavailable on macOS.")

    def set_route_binding(self, *args: object, **kwargs: object) -> None:
        raise HotkeyRegistrationError("Global plugin shortcuts are unavailable on macOS.")

    def stop(self, timeout: float = 2.0) -> bool:
        return True
