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


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PORT = 60128
CONNECT_TIMEOUT_SECONDS = 2.0
IO_TIMEOUT_SECONDS = 2.0
MAX_PACKET_SIZE = 4096
INPUT_OPTIONS = (
    ("Do not change input", ""),
    ("Video 1 / VCR-DVR", "00"),
    ("Video 2 / CBL-SAT", "01"),
    ("Video 3 / Game-TV", "02"),
    ("Video 4 / AUX 1", "03"),
    ("Video 5 / AUX 2", "04"),
    ("Video 6 / PC", "05"),
    ("Video 7", "06"),
    ("Extra 1", "07"),
    ("Extra 2", "08"),
    ("Extra 3", "09"),
    ("BD/DVD", "10"),
    ("Tape / TV-Tape", "20"),
    ("Tape 2", "21"),
    ("Phono", "22"),
    ("CD", "23"),
    ("FM", "24"),
    ("AM", "25"),
    ("Tuner", "26"),
    ("Music Server / DLNA", "27"),
    ("Internet Radio", "28"),
    ("USB Front", "29"),
    ("USB Rear", "2A"),
    ("Network", "2B"),
    ("USB toggle", "2C"),
    ("AirPlay", "2D"),
    ("Bluetooth", "2E"),
    ("Multi Channel", "30"),
    ("XM", "31"),
    ("Sirius", "32"),
    ("DAB", "33"),
    ("Universal Port", "40"),
    ("Line", "41"),
    ("Line 2", "42"),
    ("Optical", "44"),
    ("Coaxial", "45"),
    ("HDMI 5", "55"),
    ("HDMI 6", "56"),
    ("HDMI 7", "57"),
    ("Source", "80"),
)
INPUT_VALUES = frozenset(value for _label, value in INPUT_OPTIONS)


class OnkyoError(RuntimeError):
    """The configured receiver did not provide a usable main-zone response."""


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int = DEFAULT_PORT
    power_on: bool = False
    startup_input: str = ""


@dataclass(frozen=True)
class MainZoneVolumeProfile:
    """Generic eISCP main-zone scale, where 0x00 through 0x64 maps to 0-100%."""

    minimum: int = 0x00
    maximum: int = 0x64

    def to_percent(self, value: int) -> int:
        if not self.minimum <= value <= self.maximum:
            raise OnkyoError("The receiver returned an unsupported main-zone volume value.")
        return round((value - self.minimum) * 100 / (self.maximum - self.minimum))

    def from_percent(self, value: int) -> int:
        value = max(0, min(100, int(value)))
        return round(self.minimum + value * (self.maximum - self.minimum) / 100)


GENERAL_MAIN_ZONE_PROFILE = MainZoneVolumeProfile()


def encode_eiscp(payload: bytes) -> bytes:
    """Encode one eISCP payload, including its fixed 16-byte binary header."""

    if not isinstance(payload, bytes) or not payload:
        raise ValueError("An eISCP payload must be non-empty bytes.")
    return b"ISCP" + (16).to_bytes(4, "big") + len(payload).to_bytes(4, "big") + b"\x01\x00\x00\x00" + payload


class EiscpParser:
    """Incrementally extracts payloads from a TCP eISCP byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        messages: list[bytes] = []
        while True:
            if len(self._buffer) < 16:
                break
            if self._buffer[:4] != b"ISCP":
                marker = self._buffer.find(b"ISCP", 1)
                if marker < 0:
                    del self._buffer[:-3]
                else:
                    del self._buffer[:marker]
                continue
            header_size = int.from_bytes(self._buffer[4:8], "big")
            data_size = int.from_bytes(self._buffer[8:12], "big")
            if header_size < 16 or header_size > MAX_PACKET_SIZE or data_size > MAX_PACKET_SIZE:
                del self._buffer[:4]
                continue
            packet_size = header_size + data_size
            if len(self._buffer) < packet_size:
                break
            messages.append(bytes(self._buffer[header_size:packet_size]))
            del self._buffer[:packet_size]
        return messages


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
    payload = json.dumps(
        {"schema_version": CONFIG_SCHEMA_VERSION, "host": config.host, "port": config.port, "power_on": config.power_on, "startup_input": config.startup_input},
        indent=2,
    )
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _main_zone_volume(payload: bytes) -> int | None:
    if not payload.startswith(b"!1MVL"):
        return None
    # eISCP receivers commonly terminate payloads with EOF (0x1A)
    # before CR/LF, including the NR696 main-zone response.
    value = payload[5:].rstrip(b"\x1a\r\n")
    if len(value) != 2:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _main_zone_mute(payload: bytes) -> bool | None:
    value = payload.rstrip(b"\x1a\r\n")
    if value == b"!1AMT01":
        return True
    if value == b"!1AMT00":
        return False
    return None


class OnkyoVolumePlugin:
    plugin_id = "onkyo-volume"
    name = "Onkyo eISCP volume"
    description = (
        "Controls a configured Onkyo/Integra eISCP receiver main zone. "
        "The NR696 is protocol-compatible, but is not automatically verified."
    )
    provider_name = "Onkyo eISCP main-zone volume"
    supports_fast_volume_write = True
    supports_native_mute = True

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._config: ReceiverConfig | None = None
        self._socket: socket.socket | None = None
        self._parser = EiscpParser()
        self._lock = threading.Lock()
        self._activation_complete = False

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "OnkyoVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("Onkyo route parameters must be an object.")
        instance = OnkyoVolumePlugin()
        instance._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return instance

    def route_output_form_values(self, parameters: dict[str, object]) -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return {
            "host": config.host if config is not None else "",
            "port": str(config.port if config is not None else DEFAULT_PORT),
            "power_on": config.power_on if config is not None else False,
            "startup_input": config.startup_input if config is not None else "",
        }

    def validate_route_output_form(self, host: str, port: str, power_on: object = False, startup_input: object = "") -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": host, "port": _parse_port(port), "power_on": power_on, "startup_input": startup_input})
        if config is None:
            raise ValueError("Enter a valid host, TCP port, power option, and Onkyo input.")
        return {"host": config.host, "port": config.port, "power_on": config.power_on, "startup_input": config.startup_input}

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        values = self.route_output_form_values(dict(parameters))
        return plugin_ui_document("Configure Onkyo eISCP route", [
            {"id": "host", "type": "text", "label": "Host or IP address", "value": values["host"], "required": True},
            {"id": "port", "type": "integer", "label": "TCP port", "value": int(values["port"]), "minimum": 1, "maximum": 65535, "required": True},
            {"id": "power_on", "type": "boolean", "label": "Turn on when route activates", "value": values["power_on"]},
            {"id": "startup_input", "type": "select", "label": "Input on activation", "value": values["startup_input"], "options": [{"label": label, "value": value} for label, value in INPUT_OPTIONS], "description": "The list spans known eISCP models; the receiver must support the selected input code."},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Connect this route to a receiver on your local network.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown Onkyo UI action {action_id!r}.")
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
            return False, "Configure an Onkyo eISCP receiver in Routes."
        return True, None

    def activate_volume_provider(self) -> None:
        config = self._config
        if config is None or self._activation_complete or not (config.power_on or config.startup_input):
            return
        commands = []
        if config.power_on:
            commands.append(b"!1PWR01\r")
        if config.startup_input:
            commands.append(f"!1SLI{config.startup_input}\r".encode("ascii"))
        with self._lock:
            if self._activation_complete:
                return
            self._send_unconfirmed_locked(*commands)
            self._activation_complete = True

    def deactivate_volume_provider(self) -> None:
        return

    def on_volume_topology_changed(self) -> None:
        return

    def read_volume(self) -> int:
        with self._lock:
            return GENERAL_MAIN_ZONE_PROFILE.to_percent(self._request_volume_locked(b"!1MVLQSTN\r"))

    def write_volume(self, target_volume: int) -> int:
        raw_value = GENERAL_MAIN_ZONE_PROFILE.from_percent(target_volume)
        with self._lock:
            response = self._request_volume_locked(f"!1MVL{raw_value:02X}\r".encode("ascii"))
            return GENERAL_MAIN_ZONE_PROFILE.to_percent(response)

    def write_volume_fast(self, target_volume: int) -> None:
        raw_value = GENERAL_MAIN_ZONE_PROFILE.from_percent(target_volume)
        with self._lock:
            self._send_unconfirmed_locked(f"!1MVL{raw_value:02X}\r".encode("ascii"))

    def toggle_mute(self) -> bool:
        with self._lock:
            return self._request_mute_locked(b"!1AMTTG\r")

    def shutdown(self, timeout: float) -> bool:
        with self._lock:
            self._close_transport_locked()
        return True

    def _request_volume_locked(self, command: bytes) -> int:
        try:
            connection = self._connection_locked()
            connection.sendall(encode_eiscp(command))
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OnkyoError("Timed out waiting for the receiver main-zone volume.")
                connection.settimeout(remaining)
                received = connection.recv(MAX_PACKET_SIZE)
                if not received:
                    raise OnkyoError("The receiver closed the eISCP connection.")
                for payload in self._parser.feed(received):
                    volume = _main_zone_volume(payload)
                    if volume is not None:
                        return volume
        except (OSError, socket.timeout, OnkyoError) as exc:
            self._close_transport_locked()
            if isinstance(exc, OnkyoError):
                raise
            raise OnkyoError(f"Could not communicate with the configured Onkyo receiver: {exc}") from exc

    def _request_mute_locked(self, command: bytes) -> bool:
        try:
            connection = self._connection_locked()
            connection.sendall(encode_eiscp(command))
            deadline = time.monotonic() + IO_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OnkyoError("Timed out waiting for the receiver main-zone mute state.")
                connection.settimeout(remaining)
                received = connection.recv(MAX_PACKET_SIZE)
                if not received:
                    raise OnkyoError("The receiver closed the eISCP connection.")
                for payload in self._parser.feed(received):
                    muted = _main_zone_mute(payload)
                    if muted is not None:
                        return muted
        except (OSError, socket.timeout, OnkyoError) as exc:
            self._close_transport_locked()
            if isinstance(exc, OnkyoError):
                raise
            raise OnkyoError(f"Could not communicate with the configured Onkyo receiver: {exc}") from exc

    def _connection_locked(self) -> socket.socket:
        config = self._config
        if config is None:
            raise OnkyoError("Configure an Onkyo eISCP receiver in Routes.")
        if self._socket is None:
            self._socket = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            self._socket.settimeout(IO_TIMEOUT_SECONDS)
            self._parser = EiscpParser()
        return self._socket

    def _send_unconfirmed_locked(self, *commands: bytes) -> None:
        config = self._config
        if config is None:
            raise OnkyoError("Configure an Onkyo eISCP receiver in Routes.")
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                connection.settimeout(IO_TIMEOUT_SECONDS)
                for command in commands:
                    connection.sendall(encode_eiscp(command))
            finally:
                connection.close()
        except OSError as exc:
            raise OnkyoError(f"Could not communicate with the configured Onkyo receiver: {exc}") from exc

    def _close_transport_locked(self) -> None:
        connection, self._socket = self._socket, None
        self._parser = EiscpParser()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("Onkyo volume plugin is not initialized.")
        return self._host


def _parse_port(value: str) -> object:
    try:
        return int(value.strip(), 10)
    except (AttributeError, ValueError):
        return None


def create_plugin() -> OnkyoVolumePlugin:
    return OnkyoVolumePlugin()
