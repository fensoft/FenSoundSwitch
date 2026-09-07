from __future__ import annotations

from collections import Counter
from typing import Mapping

import core_audio
from plugin_api import PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


PARAMETER_KEYS = {"endpoint_id", "endpoint_name", "executable_path", "process_name", "display_name"}


def validate_parameters(parameters: object) -> dict[str, str]:
    if not isinstance(parameters, dict) or set(parameters) != PARAMETER_KEYS:
        raise ValueError("Windows application output requires an endpoint, executable identity, process name, and display name.")
    values = {key: parameters[key] for key in PARAMETER_KEYS}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("Select an active Windows application audio session.")
    return {key: str(value).strip() for key, value in values.items()}


def _document(selected: object, options: list[dict[str, object]]) -> dict[str, object]:
    return plugin_ui_document(
        "Configure Windows application volume",
        [{"id": "session", "type": "select", "label": "Application audio session", "value": selected, "options": options, "required": True}],
        [
            {"id": "discover", "label": "Refresh", "kind": "action", "async": True, "auto": True},
            {"id": "save", "label": "Save", "kind": "submit", "async": False},
        ],
        "Select one active application session. The route matches its executable on this output, never a transient process ID.",
    )


class WindowsApplicationVolumePlugin:
    plugin_id = "windows-application-volume"
    name = "Windows application volume"
    description = "Controls one active Windows application's audio-session volume on a selected render output."
    provider_name = "Windows application volume"
    supports_native_mute = True

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._host: PluginHostContext | None = None
        self._parameters = parameters

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "WindowsApplicationVolumePlugin":
        return WindowsApplicationVolumePlugin(validate_parameters(parameters))

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        try:
            selected: object = validate_parameters(dict(parameters))
        except ValueError:
            selected = None
        options = [{"label": str(selected["display_name"]), "value": selected}] if isinstance(selected, dict) else []
        return _document(selected, options)

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "discover":
            sessions = core_audio.enumerate_render_audio_sessions()
            identities = Counter((session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path)) for session in sessions)
            options = []
            for session in sessions:
                identity = (session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path))
                if identities[identity] != 1:
                    continue
                parameters = {
                    "endpoint_id": session.endpoint_id,
                    "endpoint_name": session.endpoint_name,
                    "executable_path": session.executable_path,
                    "process_name": session.process_name,
                    "display_name": session.display_name,
                }
                options.append({"label": f"{session.display_name} ({session.process_name}) - {session.endpoint_name}", "value": parameters})
            return plugin_ui_result(
                "update",
                document=_document(values.get("session"), options),
                message=f"{len(options)} unambiguous active application audio session(s) found.",
            )
        if action_id == "save":
            return plugin_ui_result("save", values=validate_parameters(values.get("session")))
        raise ValueError(f"Unknown Windows application volume UI action {action_id!r}.")

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            values = validate_parameters(parameters)
            return f"Selected application: {values['display_name']} ({values['process_name']})"
        except ValueError:
            return "No Windows application audio session selected for this route."

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        try:
            validate_parameters(self._parameters)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def read_volume(self) -> int:
        values = validate_parameters(self._parameters)
        return core_audio.read_application_session_volume(values["endpoint_id"], values["executable_path"])

    def write_volume(self, target_volume: int) -> int:
        values = validate_parameters(self._parameters)
        return core_audio.write_application_session_volume(values["endpoint_id"], values["executable_path"], max(0, min(100, int(target_volume))))

    def toggle_mute(self) -> bool:
        values = validate_parameters(self._parameters)
        return core_audio.toggle_application_session_mute(values["endpoint_id"], values["executable_path"])

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows application volume output has no actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsApplicationVolumePlugin:
    return WindowsApplicationVolumePlugin()
