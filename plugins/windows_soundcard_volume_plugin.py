from __future__ import annotations

from typing import Mapping

import core_audio
from plugin_api import PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict) or set(parameters) != {"endpoint_id", "display_name"}:
        raise ValueError("Windows soundcard output requires an endpoint ID and display name.")
    endpoint_id, display_name = parameters["endpoint_id"], parameters["display_name"]
    if not isinstance(endpoint_id, str) or not endpoint_id.strip() or not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("Select a Windows render soundcard endpoint.")
    return {"endpoint_id": endpoint_id.strip(), "display_name": display_name.strip()}


class WindowsSoundcardVolumePlugin:
    plugin_id = "windows-soundcard-volume"
    name = "Windows soundcard volume"
    description = "Controls the master volume of one selected Windows render soundcard endpoint."
    provider_name = "Windows soundcard volume"
    supports_native_mute = True

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._host: PluginHostContext | None = None
        self._parameters = parameters

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "WindowsSoundcardVolumePlugin":
        return WindowsSoundcardVolumePlugin(validate_parameters(parameters))

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        selected = None
        if isinstance(parameters.get("endpoint_id"), str) and isinstance(parameters.get("display_name"), str):
            selected = {"endpoint_id": parameters["endpoint_id"], "display_name": parameters["display_name"]}
        options = [{"label": str(selected["display_name"]), "value": selected}] if selected is not None else []
        return plugin_ui_document("Configure Windows soundcard volume", [
            {"id": "endpoint", "type": "select", "label": "Render soundcard", "value": selected, "options": options, "required": True},
        ], [
            {"id": "discover", "label": "Refresh", "kind": "action", "async": True},
            {"id": "save", "label": "Save", "kind": "submit", "async": False},
        ], "Select the Windows render endpoint controlled by this route.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "discover":
            endpoints = [core_audio.RenderEndpoint(core_audio.DEFAULT_ENDPOINT_ID, "Default output"), core_audio.RenderEndpoint(core_audio.VOICE_ENDPOINT_ID, "Voice output"), *core_audio.enumerate_render_endpoints()]
            selected = values.get("endpoint")
            document = plugin_ui_document("Configure Windows soundcard volume", [
                {"id": "endpoint", "type": "select", "label": "Render soundcard", "value": selected, "options": [{"label": endpoint.display_name, "value": {"endpoint_id": endpoint.endpoint_id, "display_name": endpoint.display_name}} for endpoint in endpoints], "required": True},
            ], [{"id": "discover", "label": "Refresh", "kind": "action", "async": True}, {"id": "save", "label": "Save", "kind": "submit", "async": False}], "Select the Windows render endpoint controlled by this route.")
            return plugin_ui_result("update", document=document, message=f"{len(endpoints) - 2} render soundcard endpoint(s) found.")
        if action_id == "save":
            return plugin_ui_result("save", values=validate_parameters(values.get("endpoint")))
        raise ValueError(f"Unknown Windows soundcard UI action {action_id!r}.")

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            return f"Selected soundcard: {validate_parameters(parameters)['display_name']}"
        except ValueError:
            return "No Windows render soundcard selected for this route."

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        try:
            validate_parameters(self._parameters)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def read_volume(self) -> int:
        values = validate_parameters(self._parameters)
        endpoint_id = core_audio.resolve_route_endpoint_id(str(values["endpoint_id"]), core_audio.E_RENDER)
        return core_audio.read_endpoint_volume(endpoint_id)

    def write_volume(self, target_volume: int) -> int:
        values = validate_parameters(self._parameters)
        endpoint_id = core_audio.resolve_route_endpoint_id(str(values["endpoint_id"]), core_audio.E_RENDER)
        return core_audio.write_endpoint_volume(endpoint_id, max(0, min(100, int(target_volume))))

    def toggle_mute(self) -> bool:
        values = validate_parameters(self._parameters)
        endpoint_id = core_audio.resolve_route_endpoint_id(str(values["endpoint_id"]), core_audio.E_RENDER)
        return core_audio.toggle_endpoint_mute(endpoint_id)

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows soundcard output has no actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsSoundcardVolumePlugin:
    return WindowsSoundcardVolumePlugin()
