from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

import core_audio
from plugin_api import PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION, PluginHostContext


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

    def configure(self, parent: Any) -> None:
        host = self._host
        if host is None:
            raise RuntimeError("Audio keep-alive plugin is not initialized.")
        settings = self._settings_snapshot()
        window = tk.Toplevel(parent)
        window.title("Configure audio output keep-alive")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=20, style="Dialog.TFrame")
        frame.grid(sticky="nsew")
        playback = tk.BooleanVar(window, value=bool(settings["playback_output"]))
        voice = tk.BooleanVar(window, value=bool(settings["voice_output"]))
        mode = tk.StringVar(window, value=str(settings["mode"]))
        mouse_seconds = tk.StringVar(window, value=str(settings["mouse_seconds"]))
        status = tk.StringVar(window, value="Rendering silence keeps an audio endpoint active without changing its volume.")
        ttk.Checkbutton(frame, text="Default playback output", variable=playback).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Default voice output", variable=voice).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Radiobutton(frame, text="Keep selected outputs active all the time", variable=mode, value=MODE_ALWAYS).grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Radiobutton(frame, text="Keep active only after recent mouse movement", variable=mode, value=MODE_RECENT_MOUSE).grid(row=3, column=0, sticky="w", pady=(6, 0))
        interval = ttk.Frame(frame)
        interval.grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(interval, text="Keep active for").grid(row=0, column=0, sticky="w")
        ttk.Entry(interval, textvariable=mouse_seconds, width=7).grid(row=0, column=1, padx=6)
        ttk.Label(interval, text="seconds after the pointer last moved.").grid(row=0, column=2, sticky="w")
        ttk.Label(frame, textvariable=status, wraplength=520).grid(row=5, column=0, sticky="w", pady=(10, 0))

        def save() -> None:
            try:
                self._save_settings({
                    "schema_version": SETTINGS_VERSION,
                    "playback_output": playback.get(),
                    "voice_output": voice.get(),
                    "mode": mode.get(),
                    "mouse_seconds": int(mouse_seconds.get()),
                })
            except (ValueError, OSError) as exc:
                status.set(str(exc))
                return
            window.destroy()

        ttk.Button(frame, text="Save", style="Accent.TButton", command=save).grid(row=6, column=0, sticky="w", pady=(16, 0))
        ttk.Button(frame, text="Cancel", style="Quiet.TButton", command=window.destroy).grid(row=6, column=0, sticky="e", pady=(16, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()

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
