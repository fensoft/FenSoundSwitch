from __future__ import annotations

import json
from pathlib import Path
import zipfile

from config_archive import DEFAULT_ARCHIVE_NAME, configuration_directory, write_configuration_archive


_CTRL_ALT = 0x0001 | 0x0002


def _hotkey(function_key: int) -> dict[str, int]:
    return {"modifiers": _CTRL_ALT, "virtual_key": 0x6F + function_key}


def _has_broken_keyboard_route(path: Path) -> bool:
    """Recognize only the first generated archive with the wrong capability ID."""
    try:
        with zipfile.ZipFile(path) as archive:
            settings = json.loads(archive.read("settings.json").decode("utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
        return False
    routes = settings.get("volume_routes") if isinstance(settings, dict) else None
    return isinstance(routes, list) and any(
        isinstance(route, dict)
        and route.get("route_id") == "voice"
        and isinstance(route.get("input"), dict)
        and route["input"].get("plugin_id") == "keyboard-input"
        for route in routes
    )


def ensure_default_configuration(settings_path: Path) -> Path:
    """Create the bundled non-secret default archive once for this user."""
    destination = configuration_directory(settings_path) / DEFAULT_ARCHIVE_NAME
    if destination.is_file() and not _has_broken_keyboard_route(destination):
        return destination
    settings_payload: dict[str, object] = {
        "schema_version": 9,
        "volume_routes": [
            {
                "route_id": "output",
                "name": "Output",
                "input": {"plugin_id": "windows-volume-keys", "parameters": {}},
                "output": {
                    "plugin_id": "windows-soundcard-volume",
                    "parameters": {
                        "endpoint_id": "fensoundswitch:default-endpoint",
                        "display_name": "Default output",
                    },
                },
            },
            {
                "route_id": "voice",
                "name": "Voice",
                "input": {
                    "plugin_id": "keyboard-keys",
                    "parameters": {
                        "volume_down": _hotkey(9),
                        "volume_up": _hotkey(10),
                        "forward_keys": True,
                    },
                },
                "output": {
                    "plugin_id": "windows-soundcard-volume",
                    "parameters": {
                        "endpoint_id": "fensoundswitch:voice-endpoint",
                        "display_name": "Voice output",
                    },
                },
            },
        ],
        "action_signals": [
            {
                "signal_id": "cycle-playback",
                "name": "Cycle Windows playback",
                "hotkey": _hotkey(11),
                "forward_keys": True,
                "tray_label": None,
                "slots": [{"kind": "action", "plugin_id": "windows-default-device", "action_id": "cycle-playback", "parameters": {}}],
            },
            {
                "signal_id": "cycle-input",
                "name": "Cycle Windows input",
                "hotkey": _hotkey(7),
                "forward_keys": True,
                "tray_label": None,
                "slots": [{"kind": "action", "plugin_id": "windows-default-device", "action_id": "cycle-input", "parameters": {}}],
            },
        ],
    }
    plugin_payloads = {
        "action-plugin-state.json": {
            "schema_version": 1,
            "disabled_plugin_ids": ["discord-output"],
        },
        "audio-keepalive.json": {
            "schema_version": 1,
            "playback_output": True,
            "voice_output": True,
            "mode": "recent-mouse",
            "mouse_seconds": 60,
        },
    }
    write_configuration_archive(destination, settings_payload, plugin_payloads)
    return destination
