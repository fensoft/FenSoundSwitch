from __future__ import annotations

from typing import Any

from plugin_api import PLUGIN_API_VERSION, PluginHostContext


class WindowsVolumeInputPlugin:
    """Logical source for the host-owned Windows Volume Up/Down hook."""

    plugin_id = "windows-volume-input"
    name = "Windows Volume keys"
    description = "Routes host-owned Windows Volume Up and Down events to a selected volume provider."
    input_id = "windows-volume-keys"
    input_name = "Windows Volume Up/Down"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def configure(self, parent: Any) -> None:
        return None

    def create_input(self, parameters: object) -> object:
        if not isinstance(parameters, dict) or parameters:
            raise ValueError("Windows Volume input does not accept parameters.")
        return object()

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        return "No settings."

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows Volume keys do not expose shortcut actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsVolumeInputPlugin:
    return WindowsVolumeInputPlugin()
