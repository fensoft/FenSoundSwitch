from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

import tkinter as tk
from tkinter import ttk


PLUGIN_API_VERSION = 3
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHORTCUT_ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

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
class RouteHotkeyBinding:
    """A route input binding and its explicit foreground-input policy."""

    hotkey: HotkeySpec
    consume: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.hotkey, HotkeySpec):
            raise ValueError("Route keyboard binding must use a HotkeySpec.")
        if not isinstance(self.consume, bool):
            raise ValueError("Route keyboard consume setting must be true or false.")


@dataclass(frozen=True)
class ActionHotkeyBinding:
    """A host-owned action shortcut and its foreground-input policy."""

    hotkey: HotkeySpec | None
    forward_keys: bool = True

    def __post_init__(self) -> None:
        if self.hotkey is not None and not isinstance(self.hotkey, HotkeySpec):
            raise ValueError("Action shortcut must use a HotkeySpec or be unset.")
        if not isinstance(self.forward_keys, bool):
            raise ValueError("Action shortcut forward setting must be true or false.")

    @property
    def consume(self) -> bool:
        return not self.forward_keys

    def to_json(self) -> dict[str, object]:
        return {"hotkey": self.hotkey.to_json() if self.hotkey is not None else None, "forward_keys": self.forward_keys}

    @classmethod
    def from_json(cls, value: object) -> "ActionHotkeyBinding":
        if isinstance(value, dict) and ("hotkey" in value or "forward_keys" in value):
            forward_keys = value.get("forward_keys", True)
            return cls(HotkeySpec.from_json(value.get("hotkey")), forward_keys)  # type: ignore[arg-type]
        # Schema-v1 action bindings stored the HotkeySpec directly and were passive.
        return cls(HotkeySpec.from_json(value), True)


@dataclass(frozen=True)
class ShortcutAction:
    """A named shortcut action whose binding is owned by the host."""

    action_id: str
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or SHORTCUT_ACTION_ID_PATTERN.fullmatch(self.action_id) is None:
            raise ValueError("Shortcut action ID must match [a-z][a-z0-9-]{0,63}.")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Shortcut action label must be a non-empty string.")
        object.__setattr__(self, "label", self.label.strip())


@dataclass(frozen=True)
class VolumeStatus:
    """An immutable host-published view of a configured volume provider."""

    provider_id: str
    display_name: str
    confirmed_volume: int | None
    active: bool = False
    routed: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or PLUGIN_ID_PATTERN.fullmatch(self.provider_id) is None:
            raise ValueError("Volume status provider ID is invalid.")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("Volume status display name must be non-empty.")
        if self.confirmed_volume is not None and (
            isinstance(self.confirmed_volume, bool)
            or not isinstance(self.confirmed_volume, int)
            or not 0 <= self.confirmed_volume <= 100
        ):
            raise ValueError("Confirmed volume must be an integer from 0 to 100.")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("Volume status reason must be non-empty when supplied.")


@runtime_checkable
class OverlayRenderer(Protocol):
    """Host-owned Tk renderer for routed volume status and error feedback."""

    def apply_theme(self, dark_mode: bool, high_contrast: bool = False) -> None:
        ...

    def show(self, volume: int, preferred_display_device_name: str | None = None) -> None:
        ...

    def show_error(self, message: str, preferred_display_device_name: str | None = None) -> None:
        ...

    def show_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None, preferred_display_device_name: str | None = None) -> None:
        ...

    def select_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None) -> tuple[VolumeStatus, ...]:
        """Return the host snapshot this renderer wants to present."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class OverlayRendererDefinition(Protocol):
    """Optional capability for a plugin that creates one Tk-thread overlay renderer."""

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
        ...


@dataclass(frozen=True)
class PluginHostContext:
    plugin_id: str
    ui_parent: Any
    logger: logging.Logger
    post_to_ui: Callable[[Callable[[], None]], None]
    report_status: Callable[[str], None]
    prepare_window: Callable[[Any], None]
    request_volume_refresh: Callable[[], None] = lambda: None
    get_volume_statuses: Callable[[], tuple[VolumeStatus, ...]] = lambda: ()
    load_plugin_settings: Callable[[], dict[str, object]] = lambda: {}
    save_plugin_settings: Callable[[dict[str, object]], None] = lambda _settings: None
    load_legacy_overlay_mode: Callable[[], str | None] = lambda: None
    clear_legacy_overlay_mode: Callable[[], None] = lambda: None
    dispatch_route_input: Callable[[str, int], None] = lambda _route_id, _delta: None
    dispatch_route_volume: Callable[[str, int], None] = lambda _route_id, _volume: None
    show_overlay_text: Callable[[str], None] = lambda _text: None


@runtime_checkable
class RuntimePlugin(Protocol):
    plugin_id: str
    name: str
    description: str

    def initialize(self, host: PluginHostContext) -> None:
        ...

    def configure(self, parent: Any) -> None:
        ...

    def get_shortcut_actions(self) -> list[ShortcutAction]:
        ...

    def trigger_shortcut(self, action_id: str) -> None:
        ...

    def shutdown(self, timeout: float) -> bool:
        ...


@runtime_checkable
class VolumeProvider(Protocol):
    """Optional capability supplied by a RuntimePlugin.

    Calls to read_volume and write_volume are always made by the host's worker
    threads. Values are normalized integer percentages in the inclusive 0-100
    range. The lifecycle hooks must be small and must not call Tk directly.
    """

    provider_name: str
    supports_fast_volume_write: bool

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        ...

    def read_volume(self) -> int:
        ...

    def write_volume(self, target_volume: int) -> int:
        ...

    # Optional route-held-repeat capability.  It must issue one bounded absolute
    # command without treating an acknowledgement as a confirmed volume.
    def write_volume_fast(self, target_volume: int) -> None:
        ...

    def activate_volume_provider(self) -> None:
        ...

    def deactivate_volume_provider(self) -> None:
        ...

    def on_volume_topology_changed(self) -> None:
        ...


@runtime_checkable
class RouteInputDefinition(Protocol):
    """A route-scoped input definition. The host owns native input hooks."""

    plugin_id: str
    input_name: str

    def create_input(self, parameters: Mapping[str, Any]) -> object:
        ...


@runtime_checkable
class RouteInputInstance(Protocol):
    """Optional active route input, started and stopped by the host."""

    def start(self) -> None:
        ...

    def shutdown(self, timeout: float) -> bool:
        ...


@runtime_checkable
class RouteOutputDefinition(Protocol):
    """A factory for independent route output instances."""

    plugin_id: str
    provider_name: str

    def create_output(self, parameters: Mapping[str, Any]) -> VolumeProvider:
        ...


@runtime_checkable
class RouteInputEditor(Protocol):
    """Optional route-input editor capability.

    ``configure_route_input`` receives an isolated draft and calls ``on_save``
    only after the endpoint-specific dialog validates it. ``route_input_summary``
    returns concise text for the route editor.
    """

    def configure_route_input(self, parent: Any, parameters: dict[str, object], on_save: Callable[[dict[str, object]], None]) -> None:
        ...

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        ...


@runtime_checkable
class RouteOutputEditor(Protocol):
    """Optional route-output editor capability; mirrors RouteInputEditor."""

    def configure_route_output(self, parent: Any, parameters: dict[str, object], on_save: Callable[[dict[str, object]], None]) -> None:
        ...

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        ...


def show_host_port_route_editor(
    parent: Any,
    prepare_window: Callable[[Any], None],
    title: str,
    parameters: dict[str, object],
    form_values: Callable[[dict[str, object]], dict[str, str]],
    validate: Callable[[str, str], dict[str, object]],
    on_save: Callable[[dict[str, object]], None],
) -> None:
    """Present a route-scoped receiver editor without persisting endpoint state."""
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    prepare_window(window)
    frame = ttk.Frame(window, padding=20, style="Dialog.TFrame")
    frame.grid(sticky="nsew")
    window.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)
    values = form_values(parameters)
    host_value = tk.StringVar(value=values["host"])
    port_value = tk.StringVar(value=values["port"])
    status = tk.StringVar(value="Enter the receiver hostname or IP address and TCP port.")
    ttk.Label(frame, text=title, style="DialogTitle.TLabel").grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(
        frame,
        text="Connect this route to a receiver on your local network.",
        style="DialogSubtitle.TLabel",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 16))
    ttk.Label(frame, text="Host or IP address", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Entry(frame, textvariable=host_value, width=44).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 12))
    ttk.Label(frame, text="TCP port", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w")
    ttk.Entry(frame, textvariable=port_value, width=12).grid(row=5, column=0, sticky="w", pady=(5, 0))
    ttk.Label(frame, textvariable=status, wraplength=480, style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def save() -> None:
        try:
            on_save(validate(host_value.get(), port_value.get()))
        except ValueError as exc:
            status.set(str(exc))
            return
        window.destroy()

    ttk.Button(frame, text="Cancel", style="Quiet.TButton", command=window.destroy).grid(row=7, column=0, sticky="e", pady=(16, 0))
    ttk.Button(frame, text="Save", style="Accent.TButton", command=save).grid(row=7, column=1, sticky="e", padx=(8, 0), pady=(16, 0))
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    window.grab_set()


@runtime_checkable
class InputPlugin(Protocol):
    """Optional logical input source routed by the host to a volume provider.

    Input plugins declare sources only. They must not install global Volume Up or
    Volume Down hooks; the host owns that safety-sensitive native listener.
    """

    input_id: str
    input_name: str
