from __future__ import annotations

import logging
import threading
from typing import Mapping
import core_audio
from plugin_api import PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION, PluginHostContext, SlotAction


PLUGIN_API_VERSION = HOST_PLUGIN_API_VERSION
_PLAYBACK_ROLES = (core_audio.ROLE_CONSOLE, core_audio.ROLE_MULTIMEDIA)
_VOICE_ROLES = (core_audio.ROLE_COMMUNICATIONS,)
_ACTIONS = {
    "cycle-playback": ("Playback", core_audio.E_RENDER, _PLAYBACK_ROLES),
    "cycle-voice-output": ("Voice output", core_audio.E_RENDER, _VOICE_ROLES),
    "cycle-input": ("Input", core_audio.E_CAPTURE, _PLAYBACK_ROLES),
    "cycle-microphone": ("Microphone", core_audio.E_CAPTURE, _VOICE_ROLES),
}
_ROLE_NAMES = {
    core_audio.ROLE_CONSOLE: "console",
    core_audio.ROLE_MULTIMEDIA: "multimedia",
    core_audio.ROLE_COMMUNICATIONS: "communications",
}


def cycle_default_endpoint(
    data_flow: int,
    roles: tuple[int, ...],
    logger: logging.Logger | None = None,
) -> str:
    """Select the next active endpoint and apply it to the specified roles."""
    if data_flow not in (core_audio.E_RENDER, core_audio.E_CAPTURE):
        raise ValueError("Audio endpoint flow is invalid.")
    if not roles or any(role not in (core_audio.ROLE_CONSOLE, core_audio.ROLE_MULTIMEDIA, core_audio.ROLE_COMMUNICATIONS) for role in roles):
        raise ValueError("At least one valid audio endpoint role is required.")
    endpoints = core_audio.enumerate_render_endpoints() if data_flow == core_audio.E_RENDER else core_audio.enumerate_capture_endpoints()
    if len(endpoints) < 2:
        raise RuntimeError("At least two active devices are required to switch.")
    current_id = core_audio.get_default_audio_endpoint_id(data_flow, roles[0])
    current_index = next((index for index, item in enumerate(endpoints) if item.endpoint_id == current_id), None)
    if current_index is None:
        raise RuntimeError("The current default device is not an active device.")
    next_endpoint = endpoints[(current_index + 1) % len(endpoints)]
    if logger is not None:
        current_endpoint = endpoints[current_index]
        logger.info(
            "Default-device switch requested: flow=%s, roles=%s, current=%s, target=%s.",
            "playback" if data_flow == core_audio.E_RENDER else "capture",
            ",".join(_ROLE_NAMES[role] for role in roles),
            current_endpoint.display_name,
            next_endpoint.display_name,
        )
    for role in roles:
        core_audio.set_default_audio_endpoint(next_endpoint.endpoint_id, role)
        confirmed_id = core_audio.get_default_audio_endpoint_id(data_flow, role)
        if confirmed_id != next_endpoint.endpoint_id:
            raise RuntimeError(
                f"Windows did not confirm the {_ROLE_NAMES[role]} default device change."
            )
        if logger is not None:
            logger.info(
                "Default-device output confirmed: role=%s, device=%s.",
                _ROLE_NAMES[role],
                next_endpoint.display_name,
            )
    return next_endpoint.display_name


class WindowsDefaultDevicePlugin:
    plugin_id = "windows-default-device"
    name = "Windows default device switch"
    description = "Cycles Windows playback, voice, input, and microphone defaults from automation steps."
    has_configuration = False

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._operation_lock = threading.Lock()
        self._shutdown = threading.Event()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        host.report_status("Ready")

    def get_slot_actions(self) -> list[SlotAction]:
        return [
            SlotAction("cycle-playback", "Cycle Windows playback", "Selects the next active Windows playback device for console and multimedia audio."),
            SlotAction("cycle-voice-output", "Cycle Windows voice output", "Selects the next active Windows communications playback device."),
            SlotAction("cycle-input", "Cycle Windows input", "Selects the next active Windows recording device for console and multimedia audio."),
            SlotAction("cycle-microphone", "Cycle Windows microphone", "Selects the next active Windows communications recording device."),
        ]

    def run_slot(self, action_id: str, parameters: Mapping[str, object]) -> None:
        if action_id not in _ACTIONS:
            raise ValueError(f"Unknown default-device slot action {action_id!r}.")
        if parameters:
            raise ValueError("Windows default-device slots do not accept parameters.")
        if self._shutdown.is_set() or not self._operation_lock.acquire(blocking=False):
            if self._host is not None:
                self._host.logger.info(
                    "Default-device shortcut ignored: action=%s, reason=%s.",
                    action_id,
                    "shutdown" if self._shutdown.is_set() else "switch already in progress",
                )
            return
        if self._host is not None:
            self._host.logger.info("Default-device shortcut input triggered: action=%s.", action_id)
        self._cycle(action_id)

    def _cycle(self, action_id: str) -> None:
        try:
            label, data_flow, roles = _ACTIONS[action_id]
            change = cycle_default_endpoint(
                data_flow,
                roles,
                self._host.logger if self._host is not None else None,
            )
            if self._host is not None:
                message = f"{label}: {change}"
                self._host.logger.info(
                    "Default-device output changed: category=%s, device=%s.",
                    label,
                    change,
                )
                self._host.report_status(message)
                self._host.show_overlay_text(message)
        except (core_audio.CoreAudioError, OSError, RuntimeError) as exc:
            if self._host is not None:
                self._host.logger.error(
                    "Default-device switch failed: action=%s, reason=%s.",
                    action_id,
                    str(exc).strip() or exc.__class__.__name__,
                )
                self._host.report_status(f"Switch failed: {str(exc).strip() or exc.__class__.__name__}")
        except Exception as exc:
            if self._host is not None:
                self._host.logger.error("Unexpected default-device switch failure (%s).", exc.__class__.__name__)
                self._host.report_status(f"Switch failed: {exc.__class__.__name__}")
        finally:
            self._operation_lock.release()

    def shutdown(self, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("Default-device plugin shutdown timeout cannot be negative")
        self._shutdown.set()
        acquired = self._operation_lock.acquire(timeout=timeout)
        if acquired:
            self._operation_lock.release()
        return acquired


def create_plugin() -> WindowsDefaultDevicePlugin:
    return WindowsDefaultDevicePlugin()
