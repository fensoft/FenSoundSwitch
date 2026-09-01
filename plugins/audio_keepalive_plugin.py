from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Mapping

import core_audio
from plugin_api import PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


PLUGIN_API_VERSION = HOST_PLUGIN_API_VERSION
SETTINGS_VERSION = 1
MODE_ALWAYS = "always"
MODE_RECENT_MOUSE = "recent-mouse"
_POLL_SECONDS = 0.25


def validate_settings(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Audio keep-alive settings must be an object.")
    allowed = {"schema_version", "playback_output", "voice_output", "mode", "mouse_seconds"}
    if set(value) - allowed:
        raise ValueError("Audio keep-alive settings contain unknown values.")
    playback = value.get("playback_output", False)
    voice = value.get("voice_output", False)
    mode = value.get("mode", MODE_ALWAYS)
    seconds = value.get("mouse_seconds", 60)
    if not isinstance(playback, bool) or not isinstance(voice, bool):
        raise ValueError("Audio keep-alive output selections must be true or false.")
    if mode not in (MODE_ALWAYS, MODE_RECENT_MOUSE):
        raise ValueError("Audio keep-alive mode is invalid.")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
        raise ValueError("Mouse activity interval must be from 1 to 3600 seconds.")
    return {
        "schema_version": SETTINGS_VERSION,
        "playback_output": playback,
        "voice_output": voice,
        "mode": mode,
        "mouse_seconds": seconds,
    }


def _cursor_position() -> tuple[int, int] | None:
    point = wintypes.POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return point.x, point.y
    return None


class AudioKeepalivePlugin:
    plugin_id = "audio-keepalive"
    name = "Audio output keep-alive"
    description = "Keeps the selected Windows default playback and/or voice output active by rendering silence."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._settings = validate_settings({})
        self._settings_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._changed = threading.Event()
        self._worker: threading.Thread | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        try:
            self._settings = validate_settings(host.load_plugin_settings())
        except ValueError:
            host.report_status("Settings were invalid; keep-alive is disabled")
        self._worker = threading.Thread(target=self._run, name="audio-keepalive", daemon=True)
        self._worker.start()
        self._report_status()

    def _settings_snapshot(self) -> dict[str, object]:
        with self._settings_lock:
            return dict(self._settings)

    def _save_settings(self, settings: dict[str, object]) -> None:
        values = validate_settings(settings)
        host = self._host
        if host is None:
            raise RuntimeError("Audio keep-alive plugin is not initialized.")
        host.save_plugin_settings(values)
        with self._settings_lock:
            self._settings = values
        self._changed.set()
        self._report_status()

    def _report_status(self) -> None:
        host = self._host
        if host is None:
            return
        settings = self._settings_snapshot()
        targets = [label for key, label in (("playback_output", "Playback"), ("voice_output", "Voice output")) if settings[key]]
        if not targets:
            host.report_status("Disabled")
        elif settings["mode"] == MODE_ALWAYS:
            host.report_status(f"Active: {', '.join(targets)}")
        else:
            host.report_status(f"Active after mouse movement: {', '.join(targets)}")

    def _run(self) -> None:
        last_position = _cursor_position()
        last_move = time.monotonic() if last_position is not None else float("-inf")
        sessions: dict[int, tuple[threading.Event, threading.Thread]] = {}
        try:
            while not self._shutdown.is_set():
                settings = self._settings_snapshot()
                position = _cursor_position()
                if position is not None and position != last_position:
                    last_position = position
                    last_move = time.monotonic()
                active = settings["mode"] == MODE_ALWAYS or time.monotonic() - last_move <= int(settings["mouse_seconds"])
                desired = set()
                if active and settings["playback_output"]:
                    desired.add(core_audio.ROLE_CONSOLE)
                if active and settings["voice_output"]:
                    desired.add(core_audio.ROLE_COMMUNICATIONS)
                for role in tuple(sessions):
                    if role not in desired:
                        sessions.pop(role)[0].set()
                for role in desired - sessions.keys():
                    stop = threading.Event()
                    thread = threading.Thread(target=self._keep_role_active, args=(role, stop), name=f"audio-keepalive-{role}", daemon=True)
                    sessions[role] = (stop, thread)
                    thread.start()
                self._changed.wait(_POLL_SECONDS)
                self._changed.clear()
        finally:
            for stop, _thread in sessions.values():
                stop.set()

    def _keep_role_active(self, role: int, stop: threading.Event) -> None:
        try:
            endpoint_id = core_audio.get_default_audio_endpoint_id(core_audio.E_RENDER, role)
            core_audio.keep_endpoint_active(endpoint_id, stop)
        except Exception as exc:
            host = self._host
            if host is not None and not self._shutdown.is_set():
                label = "Playback" if role == core_audio.ROLE_CONSOLE else "Voice output"
                # Keep endpoint identifiers out of diagnostics, but retain the
                # traceback so native ctypes failures can be diagnosed.
                host.logger.exception("%s keep-alive worker failed.", label)
                host.report_status(f"{label} keep-alive failed: {str(exc).strip() or exc.__class__.__name__}")

    def get_plugin_ui(self) -> dict[str, object]:
        settings = self._settings_snapshot()
        return plugin_ui_document("Configure audio output keep-alive", [
            {"id": "playback_output", "type": "boolean", "label": "Default playback output", "value": settings["playback_output"]},
            {"id": "voice_output", "type": "boolean", "label": "Default voice output", "value": settings["voice_output"]},
            {"id": "mode", "type": "choice", "label": "Mode", "value": settings["mode"], "options": [{"label": "Always", "value": MODE_ALWAYS}, {"label": "After recent mouse movement", "value": MODE_RECENT_MOUSE}]},
            {"id": "mouse_seconds", "type": "integer", "label": "Mouse activity interval (seconds)", "value": settings["mouse_seconds"], "minimum": 1, "maximum": 3600},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Rendering silence keeps an audio endpoint active without changing its volume.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown audio keep-alive UI action {action_id!r}.")
        candidate = dict(values)
        seconds = candidate.get("mouse_seconds")
        if isinstance(seconds, str):
            candidate["mouse_seconds"] = int(seconds)
        normalized = validate_settings(candidate)
        self._save_settings(normalized)
        return plugin_ui_result("save", values=normalized)

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Audio output keep-alive has no shortcut actions.")

    def shutdown(self, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("Audio keep-alive shutdown timeout cannot be negative.")
        self._shutdown.set()
        self._changed.set()
        worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()


def create_plugin() -> AudioKeepalivePlugin:
    return AudioKeepalivePlugin()
