from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from plugin_api import PLUGIN_API_VERSION, HotkeySpec, PluginHostContext, plugin_ui_document, plugin_ui_result


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PORT = 50000
CONNECT_TIMEOUT_SECONDS = 2.0
IO_TIMEOUT_SECONDS = 2.0
MAX_LINE_SIZE = 1024
RECEIVE_SIZE = 1024
MAIN_ZONE_MINIMUM_DB = -80.5
MAIN_ZONE_MAXIMUM_DB = 16.5
_HOSTNAME_PATTERN = re.compile(r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_VOLUME_LINE_PATTERN = re.compile(r"@MAIN:VOL=(-?(?:[0-9]|[1-9][0-9])\.[05])\Z")
INPUT_OPTIONS = (("Do not change input", ""),) + tuple((value, value) for value in (
    "PHONO", "TUNER", "MULTI CH", "DOCK", "iPod", "Bluetooth", "UAW", "HD Radio", "SIRIUS", "SiriusXM",
    "Rhapsody", "Napster", "Pandora", "Spotify", "Deezer", "Qobuz", "PC", "NET", "NET RADIO", "SERVER",
    "USB", "AirPlay", "MusicCast Link", "Alexa", "V-AUX", "AUDIO1", "AUDIO2", "AUDIO3", "AUDIO4", "AUDIO5",
    "AUDIO6", "AUDIO7", "AV1", "AV2", "AV3", "AV4", "AV5", "AV6", "AV7", "HDMI1", "HDMI2", "HDMI3",
    "HDMI4", "HDMI5", "HDMI6", "HDMI7",
))
INPUT_VALUES = frozenset(value for _label, value in INPUT_OPTIONS)


class YamahaError(RuntimeError):
    """The configured receiver did not provide a usable YNCA main-zone response."""


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int = DEFAULT_PORT
    power_on: bool = False
    startup_input: str = ""


@dataclass(frozen=True)
class MainZoneVolumeProfile:
    """Maps YNCA's -80.5 to +16.5 dB, 0.5 dB steps to integer percentages."""

    minimum_db: float = MAIN_ZONE_MINIMUM_DB
    maximum_db: float = MAIN_ZONE_MAXIMUM_DB

    def to_percent(self, value_db: float) -> int:
        if not self.minimum_db <= value_db <= self.maximum_db or value_db * 2 != round(value_db * 2):
            raise YamahaError("The receiver returned an unsupported main-zone volume value.")
        return round((value_db - self.minimum_db) * 100 / (self.maximum_db - self.minimum_db))

    def from_percent(self, value: int) -> float:
        if isinstance(value, bool):
            raise YamahaError("The requested volume must be an integer percentage.")
        try:
            percentage = max(0, min(100, int(value)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise YamahaError("The requested volume must be an integer percentage.") from exc
        return round((self.minimum_db + percentage * (self.maximum_db - self.minimum_db) / 100) * 2) / 2


YNCA_MAIN_ZONE_PROFILE = MainZoneVolumeProfile()


class YncaLineParser:
    """Incrementally extracts CRLF-terminated YNCA ASCII lines."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        if len(self._buffer) > MAX_LINE_SIZE and b"\r\n" not in self._buffer:
            raise YamahaError("The receiver returned an oversized YNCA line.")
        lines: list[bytes] = []
        while (end := self._buffer.find(b"\r\n")) >= 0:
            if end > MAX_LINE_SIZE:
                raise YamahaError("The receiver returned an oversized YNCA line.")
            lines.append(bytes(self._buffer[:end]))
            del self._buffer[:end + 2]
        return lines


def _valid_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    host = value.strip()
    if not host or len(host) > 253 or any(character.isspace() for character in host):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host if _HOSTNAME_PATTERN.fullmatch(host) else None
    return host


def _valid_config(value: object) -> ReceiverConfig | None:
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return None
    if set(value) - {"schema_version", "host", "port", "power_on", "startup_input"}:
        return None
    host = _valid_host(value.get("host"))
    port = value.get("port", DEFAULT_PORT)
    power_on = value.get("power_on", False)
    startup_input = value.get("startup_input", "")
    if host is None or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
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


def _volume_command(value_db: float | None = None) -> bytes:
    """Build a documented YNCA GET or absolute MAIN-zone VOL command."""
    if value_db is None:
        return b"@MAIN:VOL=?\r\n"
    if value_db * 2 != round(value_db * 2):
        raise ValueError("YNCA volume commands require a half-dB value.")
    return f"@MAIN:VOL={value_db:.1f}\r\n".encode("ascii")


def _parse_volume_line(line: bytes) -> float | None:
    try:
        response = line.decode("ascii")
    except UnicodeDecodeError:
        return None
    match = _VOLUME_LINE_PATTERN.fullmatch(response)
    return float(match.group(1)) if match is not None else None


def _parse_mute_line(line: bytes) -> bool | None:
    if line == b"@MAIN:MUTE=On":
        return True
    if line == b"@MAIN:MUTE=Off":
        return False
    return None


class YamahaVolumePlugin:
    plugin_id = "yamaha-volume"
    name = "Yamaha YNCA volume"
    description = "Controls a configured Yamaha network receiver main zone through its YNCA TCP interface."
    provider_name = "Yamaha YNCA main-zone volume"
    supports_fast_volume_write = True
    supports_native_mute = True

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._config: ReceiverConfig | None = None
        self._lock = threading.Lock()
        self._activation_complete = False

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "YamahaVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("Yamaha route parameters must be an object.")
        instance = YamahaVolumePlugin()
        instance._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return instance

    def route_output_form_values(self, parameters: dict[str, object]) -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return {"host": config.host if config is not None else "", "port": str(config.port if config is not None else DEFAULT_PORT), "power_on": config.power_on if config is not None else False, "startup_input": config.startup_input if config is not None else ""}

    def validate_route_output_form(self, host: str, port: str, power_on: object = False, startup_input: object = "") -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": host, "port": _parse_port(port), "power_on": power_on, "startup_input": startup_input})
        if config is None:
            raise ValueError("Enter a valid Yamaha host, TCP port, power option, and input.")
        return {"host": config.host, "port": config.port, "power_on": config.power_on, "startup_input": config.startup_input}

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        values = self.route_output_form_values(dict(parameters))
        return plugin_ui_document("Configure Yamaha YNCA route", [
            {"id": "host", "type": "text", "label": "Host or IP address", "value": values["host"], "required": True},
            {"id": "port", "type": "integer", "label": "TCP port", "value": int(values["port"]), "minimum": 1, "maximum": 65535, "required": True},
            {"id": "power_on", "type": "boolean", "label": "Turn on when route activates", "value": values["power_on"]},
            {"id": "startup_input", "type": "select", "label": "Input on activation", "value": values["startup_input"], "options": [{"label": label, "value": value} for label, value in INPUT_OPTIONS], "description": "The list spans known YNCA models; the receiver must support the selected input name."},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Connect this route to a receiver on your local network.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown Yamaha UI action {action_id!r}.")
        return plugin_ui_result("save", values=self.validate_route_output_form(str(values.get("host", "")), str(values.get("port", "")), values.get("power_on", False), values.get("startup_input", "")))

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        values = self.route_output_form_values(parameters)
        return f"Configured: {values['host']}:{values['port']}" if values["host"] else "Not configured."

    def get_hotkey(self) -> HotkeySpec | None:
        return None

    def trigger(self) -> None:
        return

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        if self._config is None:
            return False, "Configure a Yamaha YNCA receiver in Routes."
        return True, None

    def activate_volume_provider(self) -> None:
        config = self._config
        if config is None or self._activation_complete or not (config.power_on or config.startup_input):
            return
        commands = []
        if config.power_on:
            commands.append(b"@MAIN:PWR=On\r\n")
        if config.startup_input:
            commands.append(f"@MAIN:INP={config.startup_input}\r\n".encode("ascii"))
        with self._lock:
            if self._activation_complete:
                return
            self._send_unconfirmed(*commands)
            self._activation_complete = True

    def deactivate_volume_provider(self) -> None:
        return

    def on_volume_topology_changed(self) -> None:
        return

    def read_volume(self) -> int:
        with self._lock:
            return YNCA_MAIN_ZONE_PROFILE.to_percent(self._request_volume_locked((_volume_command(),)))

    def write_volume(self, target_volume: int) -> int:
        value_db = YNCA_MAIN_ZONE_PROFILE.from_percent(target_volume)
        with self._lock:
            # A YNCA PUT need not notify when the value is unchanged, so read back explicitly.
            return YNCA_MAIN_ZONE_PROFILE.to_percent(self._request_volume_locked((_volume_command(value_db), _volume_command())))

    def write_volume_fast(self, target_volume: int) -> None:
        value_db = YNCA_MAIN_ZONE_PROFILE.from_percent(target_volume)
        self._send_unconfirmed(_volume_command(value_db))

    def toggle_mute(self) -> bool:
        with self._lock:
            muted = self._request_mute_locked((b"@MAIN:MUTE=?\r\n",))
            target = not muted
            command = b"@MAIN:MUTE=On\r\n" if target else b"@MAIN:MUTE=Off\r\n"
            self._send_unconfirmed(command)
            confirmed = self._request_mute_locked((b"@MAIN:MUTE=?\r\n",))
            if confirmed != target:
                raise YamahaError("The receiver did not confirm its main-zone mute state.")
            return confirmed

    def shutdown(self, timeout: float) -> bool:
        return True

    def _request_volume_locked(self, commands: tuple[bytes, ...]) -> float:
        config = self._config
        if config is None:
            raise YamahaError("Configure a Yamaha YNCA receiver in Routes.")
        connection: socket.socket | None = None
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            connection.settimeout(IO_TIMEOUT_SECONDS)
            for command in commands:
                connection.sendall(command)
            parser = YncaLineParser()
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise YamahaError("Timed out waiting for the receiver main-zone volume.")
                connection.settimeout(remaining)
                received = connection.recv(RECEIVE_SIZE)
                if not received:
                    raise YamahaError("The receiver closed the YNCA connection.")
                for line in parser.feed(received):
                    volume = _parse_volume_line(line)
                    if volume is not None:
                        return volume
        except (OSError, socket.timeout, YamahaError) as exc:
            if isinstance(exc, YamahaError):
                raise
            raise YamahaError(f"Could not communicate with the configured Yamaha receiver: {exc}") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def _request_mute_locked(self, commands: tuple[bytes, ...]) -> bool:
        config = self._config
        if config is None:
            raise YamahaError("Configure a Yamaha YNCA receiver in Routes.")
        connection: socket.socket | None = None
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            connection.settimeout(IO_TIMEOUT_SECONDS)
            for command in commands:
                connection.sendall(command)
            parser = YncaLineParser()
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise YamahaError("Timed out waiting for the receiver main-zone mute state.")
                connection.settimeout(remaining)
                received = connection.recv(RECEIVE_SIZE)
                if not received:
                    raise YamahaError("The receiver closed the YNCA connection.")
                for line in parser.feed(received):
                    muted = _parse_mute_line(line)
                    if muted is not None:
                        return muted
        except (OSError, socket.timeout, YamahaError) as exc:
            if isinstance(exc, YamahaError):
                raise
            raise YamahaError(f"Could not communicate with the configured Yamaha receiver: {exc}") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def _send_unconfirmed(self, *commands: bytes) -> None:
        config = self._config
        if config is None:
            raise YamahaError("Configure a Yamaha YNCA receiver in Routes.")
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                connection.settimeout(IO_TIMEOUT_SECONDS)
                for command in commands:
                    connection.sendall(command)
            finally:
                connection.close()
        except OSError as exc:
            raise YamahaError(f"Could not communicate with the configured Yamaha receiver: {exc}") from exc

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("Yamaha volume plugin is not initialized.")
        return self._host


def _parse_port(value: str) -> object:
    try:
        return int(value.strip(), 10)
    except (AttributeError, ValueError):
        return None


def create_plugin() -> YamahaVolumePlugin:
    return YamahaVolumePlugin()
