from __future__ import annotations

from typing import Mapping

import bluetooth_audio
import core_audio
from plugin_api import PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


PARAMETER_KEYS = {"bluetooth_instance_id", "bluetooth_name"}


def validate_parameters(parameters: object) -> dict[str, str]:
    if not isinstance(parameters, dict) or set(parameters) != PARAMETER_KEYS:
        raise ValueError("Windows Bluetooth output requires a Bluetooth device identity and name.")
    values = {key: parameters[key] for key in PARAMETER_KEYS}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("Select a connected Windows Bluetooth audio device.")
    return {key: str(value).strip() for key, value in values.items()}


def _document(selected: object, options: list[dict[str, object]]) -> dict[str, object]:
    return plugin_ui_document(
        "Configure Windows Bluetooth volume",
        [{
            "id": "endpoint",
            "type": "select",
            "label": "Bluetooth audio device",
            "value": selected,
            "options": options,
            "required": True,
        }],
        [
            {"id": "discover", "label": "Refresh", "kind": "action", "async": True, "auto": True},
            {"id": "save", "label": "Save", "kind": "submit", "async": False},
        ],
        "Select a paired Bluetooth device. Its playback endpoint must be active when volume is used.",
    )


class WindowsBluetoothVolumePlugin:
    plugin_id = "windows-bluetooth-volume"
    name = "Windows Bluetooth volume"
    description = "Controls only the volume of one paired Bluetooth device when its Windows audio endpoint is active."
    provider_name = "Windows Bluetooth volume"
    supports_native_mute = False

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._host: PluginHostContext | None = None
        self._parameters = parameters

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "WindowsBluetoothVolumePlugin":
        return WindowsBluetoothVolumePlugin(validate_parameters(parameters))

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        try:
            selected: object = validate_parameters(dict(parameters))
        except ValueError:
            selected = None
        options = [{"label": str(selected["bluetooth_name"]), "value": selected}] if isinstance(selected, dict) else []
        return _document(selected, options)

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "discover":
            selected = values.get("endpoint")
            options = []
            for device in bluetooth_audio.enumerate_bluetooth_devices():
                parameters = {
                    "bluetooth_instance_id": device.instance_id,
                    "bluetooth_name": device.name,
                }
                options.append({
                    "label": device.name,
                    "value": parameters,
                })
            return plugin_ui_result(
                "update",
                document=_document(selected, options),
                message=f"{len(options)} paired Bluetooth audio device(s) found.",
            )
        if action_id == "save":
            return plugin_ui_result("save", values=validate_parameters(values.get("endpoint")))
        raise ValueError(f"Unknown Windows Bluetooth volume UI action {action_id!r}.")

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            return f"Selected Bluetooth device: {validate_parameters(parameters)['bluetooth_name']}"
        except ValueError:
            return "No Windows Bluetooth audio device selected for this route."

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        try:
            validate_parameters(self._parameters)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def read_volume(self) -> int:
        values = validate_parameters(self._parameters)
        endpoint = bluetooth_audio.resolve_bluetooth_audio_endpoint(values["bluetooth_name"])
        return core_audio.read_endpoint_volume(endpoint.endpoint_id)

    def write_volume(self, target_volume: int) -> int:
        values = validate_parameters(self._parameters)
        endpoint = bluetooth_audio.resolve_bluetooth_audio_endpoint(values["bluetooth_name"])
        return core_audio.write_endpoint_volume(
            endpoint.endpoint_id, max(0, min(100, int(target_volume)))
        )

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows Bluetooth volume output has no actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsBluetoothVolumePlugin:
    return WindowsBluetoothVolumePlugin()
