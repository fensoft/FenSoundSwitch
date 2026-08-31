from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import ttk

from plugin_api import HotkeySpec, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MODIFIER_VIRTUAL_KEYS, PLUGIN_API_VERSION, PluginHostContext, RouteHotkeyBinding


def _hotkey(value: object, label: str) -> HotkeySpec:
    try:
        hotkey = HotkeySpec.from_json(value)
    except ValueError as exc:
        raise ValueError(f"Keyboard {label} key is invalid: {exc}") from exc
    if hotkey is None:
        raise ValueError(f"Choose a keyboard {label} key.")
    return hotkey


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict) or set(parameters) not in ({"volume_down", "volume_up"}, {"volume_down", "volume_up", "forward_keys"}):
        raise ValueError("Keyboard input requires exactly volume_down, volume_up, and forward_keys values.")
    down = _hotkey(parameters["volume_down"], "volume down")
    up = _hotkey(parameters["volume_up"], "volume up")
    if down == up:
        raise ValueError("Keyboard volume down and volume up keys must differ.")
    # Pre-option routes were intentionally passive. Retain that behavior when
    # their parameters are loaded, then persist the explicit value on next save.
    forward_keys = parameters.get("forward_keys", True)
    if not isinstance(forward_keys, bool):
        raise ValueError("Keyboard forward_keys must be true or false.")
    return {"volume_down": down.to_json(), "volume_up": up.to_json(), "forward_keys": forward_keys}


def _event_hotkey(event: Any) -> HotkeySpec:
    state = int(getattr(event, "state", 0))
    modifiers = (MOD_CONTROL if state & 0x0004 else 0) | (MOD_SHIFT if state & 0x0001 else 0)
    if state & 0x0008 or state & 0x20000:
        modifiers |= MOD_ALT
    if state & 0x0040 or state & 0x0080:
        modifiers |= MOD_WIN
    keysym = str(getattr(event, "keysym", "")).upper()
    keycode = getattr(event, "keycode", None)
    if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        virtual_key = ord(keysym)
    elif keysym.startswith("F") and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24:
        virtual_key = 0x6F + int(keysym[1:])
    elif isinstance(keycode, int) and not isinstance(keycode, bool):
        virtual_key = keycode
    else:
        raise ValueError("Press a Windows keyboard key.")
    if virtual_key in MODIFIER_VIRTUAL_KEYS:
        raise ValueError("Keep holding the modifier and press another key.")
    return HotkeySpec(modifiers, virtual_key)


class KeyboardInputPlugin:
    plugin_id = "keyboard-input"
    name = "Keyboard input"
    description = "Configurable keyboard keys for one route. Keys can remain forwarded or be consumed for that route."
    input_id = "keyboard-keys"
    input_name = "Keyboard volume keys"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def configure(self, parent: Any) -> None:
        return None

    def create_input(self, parameters: object) -> object:
        return validate_parameters(parameters)

    def route_hotkeys(self, parameters: object) -> dict[str, RouteHotkeyBinding]:
        values = validate_parameters(parameters)
        forward_keys = values["forward_keys"]
        assert isinstance(forward_keys, bool)
        return {
            "down": RouteHotkeyBinding(_hotkey(values["volume_down"], "volume down"), consume=not forward_keys),
            "up": RouteHotkeyBinding(_hotkey(values["volume_up"], "volume up"), consume=not forward_keys),
        }

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        try:
            keys = self.route_hotkeys(parameters)
        except ValueError:
            return "Choose distinct keyboard volume down and volume up keys."
        policy = "Keys remain forwarded to other apps." if not keys["down"].consume else "Configured keys are consumed while this route is active."
        return f"Down: {keys['down'].hotkey.label}; Up: {keys['up'].hotkey.label}. {policy}"

    def configure_route_input(self, parent: Any, parameters: dict[str, object], on_save: Any) -> None:
        host = self._host
        window = tk.Toplevel(parent)
        window.title("Configure keyboard route")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        values: dict[str, HotkeySpec | None] = {"volume_down": None, "volume_up": None}
        forward_keys = tk.BooleanVar(value=parameters.get("forward_keys", True) is not False)
        for name in values:
            try:
                values[name] = _hotkey(parameters.get(name), name.replace("_", " "))
            except ValueError:
                pass
        status = tk.StringVar(value="Capture two distinct keys. Choose whether this route forwards them to the foreground application.")
        labels = {name: tk.StringVar(value=value.label if value else "Not set") for name, value in values.items()}
        capturing: list[str | None] = [None]
        def capture(event: Any) -> str:
            if capturing[0] is None:
                return "break"
            try:
                values[capturing[0]] = _event_hotkey(event)
                labels[capturing[0]].set(values[capturing[0]].label)
                status.set("Key captured.")
                capturing[0] = None
            except ValueError as exc:
                status.set(str(exc))
            return "break"
        def begin(name: str) -> None:
            capturing[0] = name
            status.set(f"Press the {name.replace('_', ' ')} key now.")
            window.focus_set()
        for row, (name, title) in enumerate((("volume_down", "Volume down:"), ("volume_up", "Volume up:"))):
            ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w")
            ttk.Label(frame, textvariable=labels[name], width=24).grid(row=row, column=1, sticky="w")
            ttk.Button(frame, text="Capture", command=lambda key=name: begin(key)).grid(row=row, column=2, sticky="e")
        ttk.Checkbutton(frame, text="Forward keys to other applications", variable=forward_keys).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(frame, textvariable=status, wraplength=500).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        def save() -> None:
            try:
                on_save(validate_parameters({**{name: value.to_json() if value else None for name, value in values.items()}, "forward_keys": forward_keys.get()}))
            except ValueError as exc:
                status.set(str(exc)); return
            window.destroy()
        ttk.Button(frame, text="Save", command=save).grid(row=4, column=1, sticky="e", pady=(12, 0))
        ttk.Button(frame, text="Cancel", command=window.destroy).grid(row=4, column=2, sticky="e", padx=(8, 0), pady=(12, 0))
        window.bind("<KeyPress>", capture)
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Keyboard route keys are route scoped.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> KeyboardInputPlugin:
    return KeyboardInputPlugin()
