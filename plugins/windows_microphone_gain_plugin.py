from __future__ import annotations

import threading
import tkinter as tk
from typing import Any
from tkinter import ttk

import core_audio
from plugin_api import PLUGIN_API_VERSION, PluginHostContext


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict) or set(parameters) != {"endpoint_id", "display_name"}:
        raise ValueError("Windows capture gain output requires an endpoint ID and display name.")
    endpoint_id, display_name = parameters["endpoint_id"], parameters["display_name"]
    if not isinstance(endpoint_id, str) or not endpoint_id.strip() or not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("Select a Windows capture endpoint.")
    return {"endpoint_id": endpoint_id.strip(), "display_name": display_name.strip()}


class WindowsMicrophoneGainPlugin:
    plugin_id = "windows-microphone-gain"
    name = "Windows capture gain"
    description = "Controls the gain of one selected Windows capture endpoint, such as a microphone or Line In."
    provider_name = "Windows capture gain"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._host: PluginHostContext | None = None
        self._parameters = parameters

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def configure(self, parent: Any) -> None:
        return None

    def create_output(self, parameters: object) -> "WindowsMicrophoneGainPlugin":
        return WindowsMicrophoneGainPlugin(validate_parameters(parameters))

    def configure_route_output(self, parent: Any, parameters: dict[str, object], on_save: Any) -> None:
        host = self._host
        if host is None:
            raise RuntimeError("Windows capture gain plugin is not initialized.")
        window = tk.Toplevel(parent)
        window.title("Configure Windows capture gain")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        status = tk.StringVar(value="Discovering capture endpoints...")
        selector = ttk.Combobox(frame, state="readonly", width=58)
        selector.grid(row=0, column=0, sticky="ew")
        ttk.Label(frame, textvariable=status, wraplength=520).grid(row=1, column=0, sticky="w", pady=(8, 0))
        discovered = [
            core_audio.RenderEndpoint(core_audio.DEFAULT_ENDPOINT_ID, "Default input"),
            core_audio.RenderEndpoint(core_audio.VOICE_ENDPOINT_ID, "Voice input"),
        ]
        selector["values"] = [endpoint.display_name for endpoint in discovered]

        def discover() -> None:
            try:
                endpoints = core_audio.enumerate_capture_endpoints()
            except Exception as exc:
                host.post_to_ui(lambda error=exc: status.set(f"Capture endpoint discovery failed: {error}"))
                return

            def finish() -> None:
                discovered[:] = [
                    core_audio.RenderEndpoint(core_audio.DEFAULT_ENDPOINT_ID, "Default input"),
                    core_audio.RenderEndpoint(core_audio.VOICE_ENDPOINT_ID, "Voice input"),
                    *endpoints,
                ]
                selector["values"] = [endpoint.display_name for endpoint in discovered]
                selected_id = parameters.get("endpoint_id")
                if isinstance(selected_id, str):
                    for index, endpoint in enumerate(discovered):
                        if endpoint.endpoint_id == selected_id:
                            selector.current(index)
                            break
                status.set(f"{len(endpoints)} capture endpoint(s) found.")

            host.post_to_ui(finish)

        def save() -> None:
            index = selector.current()
            if not 0 <= index < len(discovered):
                status.set("Select a Windows capture endpoint.")
                return
            endpoint = discovered[index]
            on_save({"endpoint_id": endpoint.endpoint_id, "display_name": endpoint.display_name})
            window.destroy()

        ttk.Button(frame, text="Save", command=save).grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(frame, text="Cancel", command=window.destroy).grid(row=2, column=0, sticky="w", pady=(12, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()
        threading.Thread(target=discover, name="microphone-route-discovery", daemon=True).start()

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            return f"Selected capture input: {validate_parameters(parameters)['display_name']}"
        except ValueError:
            return "No Windows capture endpoint selected for this route."

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        try:
            validate_parameters(self._parameters)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def read_volume(self) -> int:
        values = validate_parameters(self._parameters)
        endpoint_id = core_audio.resolve_route_endpoint_id(str(values["endpoint_id"]), core_audio.E_CAPTURE)
        return core_audio.read_endpoint_volume(endpoint_id)

    def write_volume(self, target_volume: int) -> int:
        values = validate_parameters(self._parameters)
        endpoint_id = core_audio.resolve_route_endpoint_id(str(values["endpoint_id"]), core_audio.E_CAPTURE)
        return core_audio.write_endpoint_volume(endpoint_id, max(0, min(100, int(target_volume))))

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows capture gain output has no actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsMicrophoneGainPlugin:
    return WindowsMicrophoneGainPlugin()
