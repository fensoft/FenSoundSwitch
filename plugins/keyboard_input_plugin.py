from __future__ import annotations

from typing import Mapping

from plugin_api import HotkeySpec, PLUGIN_API_VERSION, PluginHostContext, RouteHotkeyBinding, plugin_ui_document, plugin_ui_result


def _hotkey(value: object, label: str) -> HotkeySpec:
    try:
        hotkey = HotkeySpec.from_json(value)
    except ValueError as exc:
        raise ValueError(f"Keyboard {label} key is invalid: {exc}") from exc
    if hotkey is None:
        raise ValueError(f"Choose a keyboard {label} key.")
    return hotkey


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict) or not {"volume_down", "volume_up"} <= set(parameters) or set(parameters) - {"volume_down", "volume_up", "mute", "forward_keys"}:
        raise ValueError("Keyboard input requires decrease and increase keys with an optional mute key.")
    down = _hotkey(parameters["volume_down"], "volume down")
    up = _hotkey(parameters["volume_up"], "volume up")
    mute_value = parameters.get("mute")
    mute = _hotkey(mute_value, "mute") if mute_value is not None else None
    keys = [down, up] + ([mute] if mute is not None else [])
    if len(set(keys)) != len(keys):
        raise ValueError("Keyboard decrease, increase, and mute keys must differ.")
    # Pre-option routes were intentionally passive. Retain that behavior when
    # their parameters are loaded, then persist the explicit value on next save.
    forward_keys = parameters.get("forward_keys", True)
    if not isinstance(forward_keys, bool):
        raise ValueError("Keyboard forward_keys must be true or false.")
    result = {"volume_down": down.to_json(), "volume_up": up.to_json(), "forward_keys": forward_keys}
    if mute is not None:
        result["mute"] = mute.to_json()
    return result


class KeyboardInputPlugin:
    plugin_id = "keyboard-input"
    name = "Keyboard input"
    description = "Configurable keyboard keys for one route. Keys can remain forwarded or be consumed for that route."
    input_id = "keyboard-keys"
    input_name = "Custom keyboard keys"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_input(self, parameters: object) -> object:
        return validate_parameters(parameters)

    def route_hotkeys(self, parameters: object) -> dict[str, RouteHotkeyBinding]:
        values = validate_parameters(parameters)
        forward_keys = values["forward_keys"]
        assert isinstance(forward_keys, bool)
        result = {
            "down": RouteHotkeyBinding(_hotkey(values["volume_down"], "volume down"), consume=not forward_keys),
            "up": RouteHotkeyBinding(_hotkey(values["volume_up"], "volume up"), consume=not forward_keys),
        }
        if values.get("mute") is not None:
            result["mute"] = RouteHotkeyBinding(_hotkey(values["mute"], "mute"), consume=not forward_keys)
        return result

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        try:
            keys = self.route_hotkeys(parameters)
        except ValueError:
            return "Choose distinct decrease and increase keys."
        policy = "Keys remain forwarded to other apps." if not keys["down"].consume else "Configured keys are consumed while this route is active."
        mute = f"; Mute: {keys['mute'].hotkey.label}" if "mute" in keys else ""
        return f"Decrease: {keys['down'].hotkey.label}; Increase: {keys['up'].hotkey.label}{mute}. {policy}"

    def get_route_input_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        return plugin_ui_document("Configure keyboard route", [
            {"id": "volume_down", "type": "hotkey", "label": "Decrease", "value": parameters.get("volume_down"), "required": True},
            {"id": "volume_up", "type": "hotkey", "label": "Increase", "value": parameters.get("volume_up"), "required": True},
            {"id": "mute", "type": "hotkey", "label": "Mute (optional)", "value": parameters.get("mute")},
            {"id": "forward_keys", "type": "boolean", "label": "Forward keys to other applications", "value": parameters.get("forward_keys", True)},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Capture distinct decrease and increase keys, optionally add mute, and choose whether this route forwards them to the foreground application.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown keyboard UI action {action_id!r}.")
        return plugin_ui_result("save", values=validate_parameters(dict(values)))

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Keyboard route keys are route scoped.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> KeyboardInputPlugin:
    return KeyboardInputPlugin()
