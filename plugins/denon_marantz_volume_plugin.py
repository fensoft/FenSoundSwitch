from __future__ import annotations

import json
import os
import socket
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from plugin_api import PLUGIN_API_VERSION, HotkeySpec, PluginHostContext, show_host_port_route_editor


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PORT = 23
CONNECT_TIMEOUT_SECONDS = 2.0
IO_TIMEOUT_SECONDS = 2.0
MAX_LINE_SIZE = 256


class DenonMarantzError(RuntimeError):
    """The configured AVR did not provide a usable main-zone response."""


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int = DEFAULT_PORT


@dataclass(frozen=True)
class MainZoneVolumeProfile:
    """Generic AVR `MV` scale: native 0.0 through 99.5 in 0.5 dB steps."""

    minimum_half_steps: int = 0
    maximum_half_steps: int = 199

    def to_percent(self, half_steps: int) -> int:
        if not self.minimum_half_steps <= half_steps <= self.maximum_half_steps:
            raise DenonMarantzError("The receiver returned an unsupported main-zone volume value.")
        return round((half_steps - self.minimum_half_steps) * 100 / (self.maximum_half_steps - self.minimum_half_steps))

    def from_percent(self, value: int) -> int:
        value = max(0, min(100, int(value)))
        return round(self.minimum_half_steps + value * (self.maximum_half_steps - self.minimum_half_steps) / 100)


GENERAL_MAIN_ZONE_PROFILE = MainZoneVolumeProfile()


class AvrLineParser:
    """Incrementally extracts CR-terminated AVR commands from a TCP stream."""

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
            line = bytes(self._buffer[:marker])
            del self._buffer[: marker + 1]
            if line.endswith(b"\n"):
                line = line[:-1]
            if 0 < len(line) <= MAX_LINE_SIZE:
                lines.append(line)
        return lines


def _valid_config(value: object) -> ReceiverConfig | None:
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return None
    if set(value) - {"schema_version", "host", "port"}:
        return None
    host = value.get("host")
    port = value.get("port", DEFAULT_PORT)
    if not isinstance(host, str) or not (host := host.strip()) or len(host) > 253:
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    return ReceiverConfig(host, port)


def _load_config(path: Path) -> ReceiverConfig | None:
    try:
        return _valid_config(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_config(path: Path, config: ReceiverConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps({"schema_version": CONFIG_SCHEMA_VERSION, "host": config.host, "port": config.port}, indent=2)
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _main_zone_volume(line: bytes) -> int | None:
    if not line.startswith(b"MV"):
        return None
    value = line[2:]
    if len(value) == 2 and value.isdigit():
        return int(value) * 2
    if len(value) == 3 and value[:2].isdigit() and value[2:] in (b"0", b"5"):
        return int(value[:2]) * 2 + (value[2] - ord("0")) // 5
    return None


def _encode_main_zone_volume(half_steps: int) -> bytes:
    if not GENERAL_MAIN_ZONE_PROFILE.minimum_half_steps <= half_steps <= GENERAL_MAIN_ZONE_PROFILE.maximum_half_steps:
        raise DenonMarantzError("The main-zone volume is outside the supported generic AVR range.")
    whole, fraction = divmod(half_steps, 2)
    return f"MV{whole:02d}".encode("ascii") if not fraction else f"MV{whole:02d}5".encode("ascii")


class DenonMarantzVolumePlugin:
    plugin_id = "denon-marantz-volume"
    name = "Denon/Marantz AVR volume"
    description = "Controls a configured generic Denon/Marantz AVR Ethernet main zone using its documented MV protocol."
    provider_name = "Denon/Marantz AVR main-zone volume"
    supports_fast_volume_write = True

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._config: ReceiverConfig | None = None
        self._socket: socket.socket | None = None
        self._parser = AvrLineParser()
        self._lock = threading.Lock()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "DenonMarantzVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("Denon/Marantz route parameters must be an object.")
        instance = DenonMarantzVolumePlugin()
        instance._config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return instance

    def route_output_form_values(self, parameters: dict[str, object]) -> dict[str, str]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, **parameters})
        return {"host": config.host if config is not None else "", "port": str(config.port if config is not None else DEFAULT_PORT)}

    def validate_route_output_form(self, host: str, port: str) -> dict[str, object]:
        config = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": host, "port": _parse_port(port)})
        if config is None:
            raise ValueError("Enter a non-empty host and a TCP port from 1 through 65535.")
        return {"host": config.host, "port": config.port}

    def configure_route_output(self, parent: tk.Misc, parameters: dict[str, object], on_save: callable) -> None:
        host = self._require_host()
        show_host_port_route_editor(parent, host.prepare_window, "Configure Denon/Marantz AVR route", parameters, self.route_output_form_values, self.validate_route_output_form, on_save)

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        values = self.route_output_form_values(parameters)
        return f"Configured: {values['host']}:{values['port']}" if values["host"] else "Not configured."

    def configure(self, parent: tk.Misc) -> None:
        host = self._require_host()
        window = tk.Toplevel(parent)
        window.title("Configure Denon/Marantz AVR volume")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        configured = self._config or ReceiverConfig("")
        host_value = tk.StringVar(value=configured.host)
        port_value = tk.StringVar(value=str(configured.port))
        status = tk.StringVar(value="Configure the AVR hostname or IP address. Only the main zone is controlled.")
        ttk.Label(frame, text="AVR host:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=host_value, width=40).grid(row=0, column=1, sticky="ew")
        ttk.Label(frame, text="Port:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=port_value, width=8).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=status, wraplength=460).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def save() -> None:
            candidate = _valid_config({"schema_version": CONFIG_SCHEMA_VERSION, "host": host_value.get(), "port": _parse_port(port_value.get())})
            if candidate is None:
                status.set("Enter a non-empty host and a port from 1 through 65535.")
                return
            try:
                _save_config(host.config_path, candidate)
            except OSError as exc:
                status.set(f"Could not save configuration: {exc}")
                return
            with self._lock:
                self._config = candidate
                self._close_transport_locked()
            host.request_volume_refresh()
            window.destroy()

        ttk.Button(frame, text="Save", command=save).grid(row=3, column=1, sticky="e", pady=(12, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()

    def get_hotkey(self) -> HotkeySpec | None:
        return None

    def trigger(self) -> None:
        return

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        if self._config is None:
            return False, "Configure a Denon/Marantz AVR in Routes."
        return True, None

    def activate_volume_provider(self) -> None:
        return

    def deactivate_volume_provider(self) -> None:
        return

    def on_volume_topology_changed(self) -> None:
        return

    def read_volume(self) -> int:
        with self._lock:
            return GENERAL_MAIN_ZONE_PROFILE.to_percent(self._request_volume_locked(b"MV?\r"))

    def write_volume(self, target_volume: int) -> int:
        command = _encode_main_zone_volume(GENERAL_MAIN_ZONE_PROFILE.from_percent(target_volume)) + b"\r"
        with self._lock:
            return GENERAL_MAIN_ZONE_PROFILE.to_percent(self._request_volume_locked(command))

    def write_volume_fast(self, target_volume: int) -> None:
        command = _encode_main_zone_volume(GENERAL_MAIN_ZONE_PROFILE.from_percent(target_volume)) + b"\r"
        with self._lock:
            self._send_unconfirmed_locked(command)

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
                    raise DenonMarantzError("Timed out waiting for the AVR main-zone volume.")
                connection.settimeout(remaining)
                received = connection.recv(MAX_LINE_SIZE)
                if not received:
                    raise DenonMarantzError("The AVR closed the Ethernet connection.")
                for line in self._parser.feed(received):
                    volume = _main_zone_volume(line)
                    if volume is not None:
                        return volume
        except (OSError, socket.timeout, DenonMarantzError) as exc:
            self._close_transport_locked()
            if isinstance(exc, DenonMarantzError):
                raise
            raise DenonMarantzError(f"Could not communicate with the configured AVR: {exc}") from exc

    def _send_unconfirmed_locked(self, command: bytes) -> None:
        # Use a short-lived connection so unsolicited set echoes cannot be read as
        # a later query response on the persistent confirmed-read connection.
        config = self._config
        if config is None:
            raise DenonMarantzError("Configure a Denon/Marantz AVR in Routes.")
        try:
            connection = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            try:
                connection.settimeout(IO_TIMEOUT_SECONDS)
                connection.sendall(command)
            finally:
                connection.close()
        except OSError as exc:
            raise DenonMarantzError(f"Could not communicate with the configured AVR: {exc}") from exc

    def _connection_locked(self) -> socket.socket:
        config = self._config
        if config is None:
            raise DenonMarantzError("Configure a Denon/Marantz AVR in Routes.")
        if self._socket is None:
            self._socket = socket.create_connection((config.host, config.port), timeout=CONNECT_TIMEOUT_SECONDS)
            self._socket.settimeout(IO_TIMEOUT_SECONDS)
            self._parser = AvrLineParser()
        return self._socket

    def _close_transport_locked(self) -> None:
        connection, self._socket = self._socket, None
        self._parser = AvrLineParser()
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("Denon/Marantz AVR volume plugin is not initialized.")
        return self._host


def _parse_port(value: str) -> object:
    try:
        return int(value.strip(), 10)
    except (AttributeError, ValueError):
        return None


def create_plugin() -> DenonMarantzVolumePlugin:
    return DenonMarantzVolumePlugin()
