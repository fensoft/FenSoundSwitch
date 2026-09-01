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


class KeyboardInputPlugin:
    plugin_id = "keyboard-input"
    name = "Keyboard input"
    description = "Configurable keyboard keys for one route. Keys can remain forwarded or be consumed for that route."
    input_id = "keyboard-keys"
    input_name = "Keyboard volume keys"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

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

    def get_route_input_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        return plugin_ui_document("Configure keyboard route", [
            {"id": "volume_down", "type": "hotkey", "label": "Volume down", "value": parameters.get("volume_down"), "required": True},
            {"id": "volume_up", "type": "hotkey", "label": "Volume up", "value": parameters.get("volume_up"), "required": True},
            {"id": "forward_keys", "type": "boolean", "label": "Forward keys to other applications", "value": parameters.get("forward_keys", True)},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Capture two distinct keys and choose whether this route forwards them to the foreground application.")

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
