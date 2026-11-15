from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from plugin_api import PLUGIN_API_VERSION, HotkeySpec, PluginHostContext, plugin_ui_document, plugin_ui_result
from plugins.avr_profiles import AVR_INPUT_ACTION, AVR_POWER_OFF_ACTION, AVR_POWER_ON_ACTION, load_profiles, new_profile_id, normalize_slot_parameters, profile_config, profile_options, save_payload, slot_actions, slot_summary, slot_ui


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PORT = 8102
CONNECT_TIMEOUT_SECONDS = 2.0
IO_TIMEOUT_SECONDS = 2.0
MAX_LINE_SIZE = 256
RECEIVE_SIZE = 1024
MAIN_ZONE_MINIMUM = 0
MAIN_ZONE_MAXIMUM = 185
INPUT_OPTIONS = (
    ("Do not change input", ""), ("Phono", "00"), ("CD", "01"), ("Tuner", "02"),
    ("CD-R / Tape", "03"), ("DVD", "04"), ("TV / Sat", "05"), ("Sat / CBL", "06"),
    ("Video 1", "10"), ("Multi Channel In", "12"), ("USB-DAC", "13"), ("Video 2", "14"),
    ("DVR / BDR", "15"), ("iPod / USB", "17"), ("HDMI 1", "19"), ("HDMI 2", "20"),
    ("HDMI 3", "21"), ("HDMI 4", "22"), ("HDMI 5", "23"), ("HDMI 6", "24"),
    ("BD", "25"), ("Home Media Gallery", "26"), ("Sirius", "27"), ("HDMI 7", "31"),
    ("Adapter Port", "33"), ("HDMI 8", "34"), ("Internet Radio", "38"), ("SiriusXM", "40"),
    ("Pandora", "41"), ("Media Server", "44"), ("Favorites", "45"), ("MHL", "48"), ("Game", "49"),
)
INPUT_VALUES = frozenset(value for _label, value in INPUT_OPTIONS)


class PioneerEliteError(RuntimeError):
    """The configured Pioneer/Elite receiver did not provide a usable response."""


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int = DEFAULT_PORT
    power_on: bool = False
    startup_input: str = ""


class PioneerLineParser:
    """Incrementally extracts CR-terminated Pioneer/Elite protocol lines."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        lines: list[bytes] = []
        while True:
            marker = self._buffer.find(b"\r")
            if marker < 0:
                if len(self._buffer) > MAX_LINE_SIZE:
                    self._buffer.clear()
                break
            line = bytes(self._buffer[:marker]).rstrip(b"\n")
            del self._buffer[: marker + 1]
            if self._buffer[:1] == b"\n":
                del self._buffer[:1]
            if 0 < len(line) <= MAX_LINE_SIZE:
                lines.append(line)
        return lines


def _valid_config(value: object) -> ReceiverConfig | None:
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return None
    if set(value) - {"schema_version", "host", "port", "power_on", "startup_input"}:
        return None
    host = value.get("host")
    port = value.get("port", DEFAULT_PORT)
    power_on = value.get("power_on", False)
    startup_input = value.get("startup_input", "")
    if not isinstance(host, str) or not (host := host.strip()) or len(host) > 253:
        return None
    if any(character.isspace() or ord(character) < 32 for character in host):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    if not isinstance(power_on, bool) or not isinstance(startup_input, str) or startup_input not in INPUT_VALUES:
        return None
    return ReceiverConfig(host, port, power_on, startup_input)


def _load_config(path: Path) -> ReceiverConfig | None:
    try:
        return _valid_config(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_config(path: Path, config: ReceiverConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps({"schema_version": CONFIG_SCHEMA_VERSION, "host": config.host, "port": config.port, "power_on": config.power_on, "startup_input": config.startup_input}, indent=2)
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _main_zone_volume(line: bytes) -> int | None:
    if len(line) != 6 or not line.startswith(b"VOL") or not line[3:].isdigit():
        return None
    value = int(line[3:])
    if not MAIN_ZONE_MINIMUM <= value <= MAIN_ZONE_MAXIMUM:
        return None
    return value


def _main_zone_mute(line: bytes) -> bool | None:
    if line == b"MUT0":
        return True
    if line == b"MUT1":
        return False
    return None


def _to_percent(value: int) -> int:
    if not MAIN_ZONE_MINIMUM <= value <= MAIN_ZONE_MAXIMUM:
        raise PioneerEliteError("The receiver returned an unsupported main-zone volume value.")
    return round(value * 100 / MAIN_ZONE_MAXIMUM)


def _from_percent(value: int) -> int:
    value = max(0, min(100, int(value)))
    return round(value * MAIN_ZONE_MAXIMUM / 100)


class PioneerEliteVolumePlugin:
    plugin_id = "pioneer-elite-volume"
    name = "Pioneer/Elite network volume"
    description = "Controls a configured Pioneer or Elite receiver main zone using its documented network control protocol."
    provider_name = "Pioneer/Elite main-zone volume"
    supports_fast_volume_write = True
    supports_native_mute = True

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._config: ReceiverConfig | None = None
        self._socket: socket.socket | None = None
        self._parser = PioneerLineParser()
        self._lock = threading.Lock()
        self._activation_complete = False
        self._profiles: dict[str, dict[str, object]] = {}

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        try:
            saved = host.load_plugin_settings()
            self._profiles = load_profiles(saved, lambda value: _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **value}) if isinstance(value, dict) else None)
            if self._profiles: self._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **next(iter(self._profiles.values()))["config"]})
        except (OSError, ValueError, TypeError):
            self._config = None

    def create_output(self, parameters: object) -> "PioneerEliteVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("Pioneer/Elite route parameters must be an object.")
        instance = PioneerEliteVolumePlugin()
        profile_id = parameters.get("profile_id")
        profile = self._profiles.get(profile_id) if isinstance(profile_id, str) else None
        legacy = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        instance._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **profile["config"]}) if profile else (self._config if self._config is not None else legacy)
        return instance

    def route_output_form_values(self, parameters: dict[str, object]) -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        profile = self._profiles.get(parameters.get("profile_id")) if isinstance(parameters.get("profile_id"), str) else None
        if profile is not None:
            config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **profile["config"]})
        return {"host": config.host if config is not None else "", "port": str(config.port if config is not None else DEFAULT_PORT), "power_on": config.power_on if config is not None else False, "startup_input": config.startup_input if config is not None else ""}

    def get_plugin_ui(self) -> dict[str, object]:
        selected = next(iter(self._profiles), "")
        profile = self._profiles.get(selected)
        values = self.route_output_form_values(dict(profile["config"]) if profile else {})
        return plugin_ui_document("Configure Pioneer/Elite AVR volume", [{"id": "profile_id", "type": "select", "label": "Profile", "value": selected, "options": profile_options(self._profiles, include_new=True)}, {"id": "name", "type": "text", "label": "Profile name", "value": str(profile["name"]) if profile else "", "required": True}, {"id": "host", "type": "text", "label": "Host or IP address", "value": values["host"], "required": True}, {"id": "port", "type": "integer", "label": "TCP port", "value": int(values["port"]), "minimum": 1, "maximum": 65535, "required": True}], [{"id": "save", "label": "Save", "kind": "submit", "async": False}, {"id": "delete", "label": "Delete profile", "kind": "action", "async": False}], "Configure named receivers once under AVR. Routes select a profile.")

    def invoke_plugin_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "delete":
            profile_id = values.get("profile_id")
            if not isinstance(profile_id, str) or profile_id not in self._profiles: raise ValueError("Select an AVR profile to delete.")
            self._profiles.pop(profile_id)
            if self._host is None: raise RuntimeError("Pioneer/Elite integration is not initialized.")
            self._host.save_plugin_settings(save_payload(self._profiles))
            self._config = None if not self._profiles else _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **next(iter(self._profiles.values()))["config"]})
            return plugin_ui_result("complete", message="Pioneer/Elite AVR profile deleted.")
        if action_id != "save": raise ValueError(f"Unknown Pioneer/Elite integration action {action_id!r}.")
        profile_id = values.get("profile_id") if isinstance(values.get("profile_id"), str) else ""
        profile_id = profile_id or new_profile_id()
        name = str(values.get("name", "")).strip()
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": str(values.get("host", "")), "port": _parse_port(str(values.get("port", "")))})
        if not name or config is None: raise ValueError("Enter a profile name, valid host, and TCP port.")
        self._profiles[profile_id] = {"id": profile_id, "name": name, "config": {"host": config.host, "port": config.port}}
        if self._host is None: raise RuntimeError("Pioneer/Elite integration is not initialized.")
        self._host.save_plugin_settings(save_payload(self._profiles))
        self._config = config
        return plugin_ui_result("save", values={"profile_id": profile_id, "name": name, "host": config.host, "port": config.port}, message="Pioneer/Elite AVR profile saved.")

    def validate_route_output_form(self, host: str, port: str, power_on: object = False, startup_input: object = "") -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": host, "port": _parse_port(port), "power_on": power_on, "startup_input": startup_input})
        if config is None:
            raise ValueError("Enter a valid host, TCP port, power option, and Pioneer/Elite input.")
        return {"host": config.host, "port": config.port, "power_on": config.power_on, "startup_input": config.startup_input}

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        return plugin_ui_document("Configure Pioneer/Elite route", [{"id": "profile_id", "type": "select", "label": "AVR profile", "value": parameters.get("profile_id", ""), "options": profile_options(self._profiles), "required": True}], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Select an AVR profile configured under Integrations.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown Pioneer/Elite UI action {action_id!r}.")
        profile_id = values.get("profile_id")
        if not isinstance(profile_id, str) or profile_id not in self._profiles: raise ValueError("Select a configured AVR profile.")
        return plugin_ui_result("save", values={"profile_id": profile_id})

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        values = self.route_output_form_values(parameters)
        return f"Configured: {values['host']}:{values['port']}" if values["host"] else "Not configured."

    def get_hotkey(self) -> HotkeySpec | None:
        return None

    def trigger(self) -> None:
        return

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        if self._config is None:
            return False, "Configure a Pioneer or Elite receiver in Integrations."
        return True, None

    def activate_volume_provider(self) -> None:
        return

    def deactivate_volume_provider(self) -> None:
        return

    def on_volume_topology_changed(self) -> None:
        return

    def get_slot_actions(self):
        return slot_actions()

    def get_slot_ui(self, action_id, parameters):
        return slot_ui(action_id, parameters, self._profiles, [{"label": label, "value": value} for label, value in INPUT_OPTIONS if value])

    def invoke_slot_ui_action(self, action_id, ui_action_id, values):
        if ui_action_id != "save":
            raise ValueError("Unknown AVR action configuration action.")
        normalized = normalize_slot_parameters(action_id, values, self._profiles, set(INPUT_VALUES) - {""})
        return plugin_ui_result("save", values=normalized)

    def slot_summary(self, action_id, parameters):
        return slot_summary(action_id, parameters, self._profiles)

    def run_slot(self, action_id, parameters):
        values = normalize_slot_parameters(action_id, parameters, self._profiles, set(INPUT_VALUES) - {""})
        with self._lock:
            previous = self._config
            self._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **profile_config(values, self._profiles)})
            try:
                if action_id == AVR_INPUT_ACTION:
                    self._send_unconfirmed_locked(f"{values['input']}FN\r".encode("ascii"))
                elif action_id == AVR_POWER_ON_ACTION:
                    self._send_unconfirmed_locked(b"PO\r")
                else:
                    self._send_unconfirmed_locked(b"PF\r")
            finally:
                self._config = previous

    def read_volume(self) -> int:
        with self._lock:
            return _to_percent(self._request_volume_locked(b"?V\r"))

    def write_volume(self, target_volume: int) -> int:
        command = f"{_from_percent(target_volume):03d}V\r".encode("ascii")
        with self._lock:
            return _to_percent(self._request_volume_locked(command))

    def write_volume_fast(self, target_volume: int) -> None:
        command = f"{_from_percent(target_volume):03d}V\r".encode("ascii")
        with self._lock:
            self._send_unconfirmed_locked(command)

    def toggle_mute(self) -> bool:
        with self._lock:
            muted = self._request_mute_locked(b"?M\r")
            target = not muted
            confirmed = self._request_mute_locked(b"MO\r" if target else b"MF\r")
            if confirmed != target:
                raise PioneerEliteError("The receiver did not confirm its main-zone mute state.")
            return confirmed

    def shutdown(self, timeout: float) -> bool:
        with self._lock:
            self._close_transport_locked()
        return True

    def _request_volume_locked(self, command: bytes) -> int:
        try:
            connection = self._connection_locked()
            connection.sendall(command)
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PioneerEliteError("Timed out waiting for the receiver main-zone volume.")
                connection.settimeout(remaining)
                received = connection.recv(RECEIVE_SIZE)
                if not received:
                    raise PioneerEliteError("The receiver closed the network control connection.")
                for line in self._parser.feed(received):
                    volume = _main_zone_volume(line)
                    if volume is not None:
                        return volume
        except (OSError, socket.timeout, PioneerEliteError) as exc:
            self._close_transport_locked()
            if isinstance(exc, PioneerEliteError):
                raise
            raise PioneerEliteError(f"Could not communicate with the configured Pioneer/Elite receiver: {exc}") from exc

    def _request_mute_locked(self, command: bytes) -> bool:
        try:
            connection = self._connection_locked()
            connection.sendall(command)
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PioneerEliteError("Timed out waiting for the receiver main-zone mute state.")
                connection.settimeout(remaining)
                received = connection.recv(RECEIVE_SIZE)
                if not received:
                    raise PioneerEliteError("The receiver closed the network control connection.")
                for line in self._parser.feed(received):
                    muted = _main_zone_mute(line)
                    if muted is not None:
                        return muted
        except (OSError, socket.timeout, PioneerEliteError) as exc:
            self._close_transport_locked()
            if isinstance(exc, PioneerEliteError):
                raise
            raise PioneerEliteError(f"Could not communicate with the configured Pioneer/Elite receiver: {exc}") from exc

    def _connection_locked(self) -> socket.socket:
        config = self._config
        if config is None:
            raise PioneerEliteError("Configure a Pioneer or Elite receiver in Integrations.")
        if self._socket is None:
            self._socket = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            self._socket.settimeout(IO_TIMEOUT_SECONDS)
            self._parser = PioneerLineParser()
        return self._socket

    def _send_unconfirmed_locked(self, *commands: bytes) -> None:
        config = self._config
        if config is None:
            raise PioneerEliteError("Configure a Pioneer or Elite receiver in Integrations.")
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                connection.settimeout(IO_TIMEOUT_SECONDS)
                for command in commands:
                    connection.sendall(command)
            finally:
                connection.close()
        except OSError as exc:
            raise PioneerEliteError(f"Could not communicate with the configured Pioneer/Elite receiver: {exc}") from exc

    def _close_transport_locked(self) -> None:
        connection, self._socket = self._socket, None
        self._parser = PioneerLineParser()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("Pioneer/Elite volume plugin is not initialized.")
        return self._host


def _parse_port(value: str) -> object:
    try:
        return int(value.strip(), 10)
    except (AttributeError, ValueError):
        return None


def create_plugin() -> PioneerEliteVolumePlugin:
    return PioneerEliteVolumePlugin()
