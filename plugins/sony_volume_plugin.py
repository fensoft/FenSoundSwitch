from __future__ import annotations

import http.client
import json
import os
import threading
import tkinter as tk
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from tkinter import ttk

from plugin_api import PLUGIN_API_VERSION, HotkeySpec, PluginHostContext, show_host_port_route_editor


CONFIG_SCHEMA_VERSION = 1
DEFAULT_PORT = 10000
CONNECT_TIMEOUT_SECONDS = 2.0
IO_TIMEOUT_SECONDS = 2.0
MAX_RESPONSE_BYTES = 64 * 1024
MAIN_ZONE_OUTPUT = "extOutput:zone:1"


class SonyError(RuntimeError):
    """The configured Sony network receiver did not provide usable volume data."""


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int = DEFAULT_PORT


@dataclass(frozen=True)
class VolumeRange:
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        if not self.minimum < self.maximum or self.maximum - self.minimum > Decimal("1000"):
            raise SonyError("The receiver returned an unsupported main-zone volume range.")

    def to_percent(self, value: Decimal) -> int:
        if not self.minimum <= value <= self.maximum:
            raise SonyError("The receiver returned a main-zone volume outside its advertised range.")
        return int(((value - self.minimum) * 100 / (self.maximum - self.minimum)).quantize(Decimal("1"), ROUND_HALF_UP))

    def from_percent(self, value: int) -> Decimal:
        bounded = max(0, min(100, int(value)))
        return self.minimum + (self.maximum - self.minimum) * Decimal(bounded) / 100


def _valid_config(value: object) -> ReceiverConfig | None:
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_SCHEMA_VERSION:
        return None
    if set(value) != {"schema_version", "host", "port"}:
        return None
    host = value.get("host")
    port = value.get("port")
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


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 32:
        raise SonyError(f"The receiver returned an invalid {field} value.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SonyError(f"The receiver returned an invalid {field} value.") from exc
    if not parsed.is_finite() or abs(parsed) > Decimal("1000"):
        raise SonyError(f"The receiver returned an invalid {field} value.")
    return parsed


def _format_volume(value: Decimal) -> str:
    result = format(value.normalize(), "f")
    return "0" if result in {"-0", ""} else result


class SonyVolumePlugin:
    plugin_id = "sony-volume"
    name = "Sony network AVR volume"
    description = (
        "Controls the generic Sony network-control API main zone. "
        "Protocol compatibility is not verified for any specific receiver model."
    )
    provider_name = "Sony network AVR main-zone volume"
    # A held repeat reuses the range confirmed by its initial normal write and
    # performs only the bounded JSON-RPC set request, never an extra readback.
    supports_fast_volume_write = True

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._config: ReceiverConfig | None = None
        self._lock = threading.Lock()
        self._next_request_id = 1
        self._volume_range: VolumeRange | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "SonyVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("Sony route parameters must be an object.")
        instance = SonyVolumePlugin()
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
        show_host_port_route_editor(parent, host.prepare_window, "Configure Sony route", parameters, self.route_output_form_values, self.validate_route_output_form, on_save)

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        values = self.route_output_form_values(parameters)
        return f"Configured: {values['host']}:{values['port']}" if values["host"] else "Not configured."

    def configure(self, parent: tk.Misc) -> None:
        host = self._require_host()
        window = tk.Toplevel(parent)
        window.title("Configure Sony network AVR volume")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        configured = self._config or ReceiverConfig("")
        host_value = tk.StringVar(value=configured.host)
        port_value = tk.StringVar(value=str(configured.port))
        status = tk.StringVar(value="Enter the receiver hostname or IP address. Only main zone 1 is controlled.")
        ttk.Label(frame, text="Receiver host:").grid(row=0, column=0, sticky="w")
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
            return False, "Configure a Sony network AVR in Routes."
        return True, None

    def activate_volume_provider(self) -> None:
        return

    def deactivate_volume_provider(self) -> None:
        return

    def on_volume_topology_changed(self) -> None:
        return

    def read_volume(self) -> int:
        with self._lock:
            value, volume_range = self._read_volume_locked()
            return volume_range.to_percent(value)

    def write_volume(self, target_volume: int) -> int:
        with self._lock:
            _, volume_range = self._read_volume_locked()
            result = self._request_locked("setAudioVolume", [{"output": MAIN_ZONE_OUTPUT, "volume": _format_volume(volume_range.from_percent(target_volume))}])
            if not isinstance(result, list):
                raise SonyError("The receiver returned an invalid set-volume response.")
            value, confirmed_range = self._read_volume_locked()
            return confirmed_range.to_percent(value)

    def write_volume_fast(self, target_volume: int) -> None:
        with self._lock:
            volume_range = self._volume_range
            if volume_range is None:
                raise SonyError("The receiver volume range has not been confirmed.")
            result = self._request_locked(
                "setAudioVolume",
                [{"output": MAIN_ZONE_OUTPUT, "volume": _format_volume(volume_range.from_percent(target_volume))}],
            )
            if not isinstance(result, list):
                raise SonyError("The receiver returned an invalid set-volume response.")

    def shutdown(self, timeout: float) -> bool:
        return True

    def _read_volume_locked(self) -> tuple[Decimal, VolumeRange]:
        result = self._request_locked("getVolumeInformation", [])
        if not isinstance(result, list):
            raise SonyError("The receiver returned an invalid volume-information response.")
        for item in result:
            if not isinstance(item, dict) or item.get("output") != MAIN_ZONE_OUTPUT:
                continue
            volume_range = VolumeRange(
                _decimal(item.get("minVolume"), "minimum volume"),
                _decimal(item.get("maxVolume"), "maximum volume"),
            )
            self._volume_range = volume_range
            return _decimal(item.get("volume"), "volume"), volume_range
        raise SonyError("The receiver did not return main-zone volume information.")

    def _request_locked(self, method: str, params: list[object]) -> object:
        config = self._config
        if config is None:
            raise SonyError("Configure a Sony network AVR in Routes.")
        request_id = self._next_request_id
        self._next_request_id += 1
        body = json.dumps({"method": method, "params": params, "id": request_id, "version": "1.0"}, separators=(",", ":")).encode("ascii")
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(config.host, config.port, timeout=CONNECT_TIMEOUT_SECONDS)
            connection.request("POST", "/sony/audio", body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            response = connection.getresponse()
            if response.status != 200:
                raise SonyError(f"The receiver returned HTTP status {response.status}.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise SonyError("The receiver response was too large.")
            try:
                envelope = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SonyError("The receiver returned invalid JSON.") from exc
            if not isinstance(envelope, dict) or envelope.get("id") != request_id or "error" in envelope or "result" not in envelope:
                raise SonyError("The receiver returned an invalid JSON-RPC response.")
            return envelope["result"]
        except (OSError, http.client.HTTPException) as exc:
            raise SonyError(f"Could not communicate with the configured Sony receiver: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("Sony volume plugin is not initialized.")
        return self._host


def _parse_port(value: str) -> object:
    try:
        return int(value.strip(), 10)
    except (AttributeError, ValueError):
        return None


def create_plugin() -> SonyVolumePlugin:
    return SonyVolumePlugin()
