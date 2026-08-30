from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


PLUGIN_API_VERSION = 1
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
ALLOWED_MODIFIERS = MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN
MODIFIER_VIRTUAL_KEYS = frozenset((0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5))


_VIRTUAL_KEY_LABELS = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x13: "Pause",
    0x14: "Caps Lock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "Page Up",
    0x22: "Page Down",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print Screen",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5D: "Menu",
    0x6A: "Numpad *",
    0x6B: "Numpad +",
    0x6D: "Numpad -",
    0x6E: "Numpad .",
    0x6F: "Numpad /",
    0x90: "Num Lock",
    0x91: "Scroll Lock",
    0xA6: "Browser Back",
    0xA7: "Browser Forward",
    0xA8: "Browser Refresh",
    0xA9: "Browser Stop",
    0xAA: "Browser Search",
    0xAB: "Browser Favorites",
    0xAC: "Browser Home",
    0xAD: "Mute",
    0xAE: "Volume Down",
    0xAF: "Volume Up",
    0xB0: "Next Track",
    0xB1: "Previous Track",
    0xB2: "Stop Media",
    0xB3: "Play/Pause",
    0xB4: "Mail",
    0xB5: "Select Media",
}


def _valid_virtual_key(virtual_key: int) -> bool:
    return 0x01 <= virtual_key <= 0xFE


def virtual_key_label(virtual_key: int) -> str:
    named = _VIRTUAL_KEY_LABELS.get(virtual_key)
    if named is not None:
        return named
    if 0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A:
        return chr(virtual_key)
    if 0x60 <= virtual_key <= 0x69:
        return f"Numpad {virtual_key - 0x60}"
    if 0x70 <= virtual_key <= 0x87:
        return f"F{virtual_key - 0x6F}"
    if _valid_virtual_key(virtual_key):
        return f"VK 0x{virtual_key:02X}"
    raise ValueError("Plugin shortcut key must be a valid Windows virtual key.")


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    virtual_key: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.modifiers, bool)
            or not isinstance(self.modifiers, int)
            or self.modifiers < 0
            or self.modifiers & ~ALLOWED_MODIFIERS
        ):
            raise ValueError("Plugin shortcut modifiers may use only Ctrl, Alt, Shift, or Win.")
        if (
            isinstance(self.virtual_key, bool)
            or not isinstance(self.virtual_key, int)
            or not _valid_virtual_key(self.virtual_key)
            or self.virtual_key in MODIFIER_VIRTUAL_KEYS
        ):
            raise ValueError(
                "Plugin shortcut key must be a non-modifier Windows virtual key."
            )

    @property
    def label(self) -> str:
        parts: list[str] = []
        for flag, label in (
            (MOD_CONTROL, "Ctrl"),
            (MOD_ALT, "Alt"),
            (MOD_SHIFT, "Shift"),
            (MOD_WIN, "Win"),
        ):
            if self.modifiers & flag:
                parts.append(label)
        parts.append(virtual_key_label(self.virtual_key))
        return "+".join(parts)

    def to_json(self) -> dict[str, int]:
        return {"modifiers": self.modifiers, "virtual_key": self.virtual_key}

    @classmethod
    def from_json(cls, value: object) -> HotkeySpec | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("The saved plugin shortcut is invalid.")
        modifiers = value.get("modifiers")
        virtual_key = value.get("virtual_key")
        return cls(modifiers=modifiers, virtual_key=virtual_key)  # type: ignore[arg-type]


@dataclass(frozen=True)
class PluginHostContext:
    plugin_id: str
    ui_parent: Any
    config_path: Path
    logger: logging.Logger
    post_to_ui: Callable[[Callable[[], None]], None]
    report_status: Callable[[str], None]
    prepare_window: Callable[[Any], None]
    request_volume_refresh: Callable[[], None] = lambda: None


@runtime_checkable
class RuntimePlugin(Protocol):
    plugin_id: str
    name: str
    description: str

    def initialize(self, host: PluginHostContext) -> None:
        ...

    def configure(self, parent: Any) -> None:
        ...

    def get_hotkey(self) -> HotkeySpec | None:
        ...

    def trigger(self) -> None:
        ...

    def shutdown(self, timeout: float) -> bool:
        ...


# Kept for API-v1 external plugins that imported the original protocol name.
WindowsDdcPlugin = RuntimePlugin


@runtime_checkable
class VolumeProvider(Protocol):
    """Optional capability supplied by a RuntimePlugin.

    Calls to read_volume and write_volume are always made by the host's worker
    threads. Values are normalized integer percentages in the inclusive 0-100
    range. The lifecycle hooks must be small and must not call Tk directly.
    """

    provider_name: str

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        ...

    def read_volume(self) -> int:
        ...

    def write_volume(self, target_volume: int) -> int:
        ...

    def activate_volume_provider(self) -> None:
        ...

    def deactivate_volume_provider(self) -> None:
        ...

    def on_volume_topology_changed(self) -> None:
        ...
