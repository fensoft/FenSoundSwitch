from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

import tkinter as tk
from tkinter import ttk

from plugin_api import PLUGIN_API_VERSION, PluginHostContext


DEFAULT_PORT = 1883
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
_TOPIC_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _text(value: object, label: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"MQTT {label} must be text.")
    result = value.strip()
    if required and not result:
        raise ValueError(f"MQTT {label} is required.")
    if len(result) > 255:
        raise ValueError(f"MQTT {label} is too long.")
    return result


def _topic(value: object, label: str) -> str:
    result = _text(value, label).strip("/")
    if not result or any(part in {"", "+", "#"} for part in result.split("/")):
        raise ValueError(f"MQTT {label} must be a concrete topic path.")
    return result


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict):
        raise ValueError("MQTT input settings must be an object.")
    allowed = {"host", "port", "username", "password", "discovery_prefix", "topic_prefix", "max_value", "button_step"}
    if set(parameters) - allowed:
        raise ValueError("MQTT input has unknown settings.")
    host = _text(parameters.get("host"), "broker host")
    port = parameters.get("port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("MQTT broker port must be from 1 to 65535.")
    username = _text(parameters.get("username", ""), "username", required=False)
    password = _text(parameters.get("password", ""), "password", required=False)
    discovery_prefix = _topic(parameters.get("discovery_prefix", DEFAULT_DISCOVERY_PREFIX), "discovery prefix")
    topic_prefix = _topic(parameters.get("topic_prefix", "fensoundswitch"), "topic prefix")
    max_value = parameters.get("max_value", 100)
    if isinstance(max_value, bool) or not isinstance(max_value, int) or not 1 <= max_value <= 100:
        raise ValueError("MQTT maximum volume must be from 1 to 100.")
    # Ignore the former button setting so existing MQTT routes continue to load.
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "discovery_prefix": discovery_prefix,
        "topic_prefix": topic_prefix,
        "max_value": max_value,
    }


def _route_token(route_id: str) -> str:
    token = re.sub(r"[^a-z0-9_-]", "_", route_id.casefold())
    return token[:64] or "route"


@dataclass
class MqttRouteInput:
    route_id: str
    parameters: dict[str, object]
    client_factory: Callable[..., Any]
    logger: logging.Logger = logging.getLogger(__name__)
    dispatch_volume: Callable[[str, int], None] = lambda _route_id, _volume: None

    def __post_init__(self) -> None:
        self._client: Any | None = None
        self._started = False
        self._token = _route_token(self.route_id)
        prefix = str(self.parameters["topic_prefix"])
        self._command_topic = f"{prefix}/{self._token}/command"

    def start(self) -> None:
        if self._started:
            return
        self.logger.info("MQTT route input starting: route=%s broker=%s:%d topic=%s.", self.route_id, self.parameters["host"], self.parameters["port"], self._command_topic)
        client = self.client_factory(client_id=f"fensoundswitch-{self._token}")
        username = str(self.parameters["username"])
        if username:
            client.username_pw_set(username, str(self.parameters["password"]))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect_async(str(self.parameters["host"]), int(self.parameters["port"]), keepalive=30)
        client.loop_start()
        self._client = client
        self._started = True

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, result_code: int, *_args: Any) -> None:
        if result_code != 0:
            self.logger.warning("MQTT route input connection refused: route=%s result=%s.", self.route_id, result_code)
            return
        self.logger.info("MQTT route input connected: route=%s.", self.route_id)
        client.subscribe(self._command_topic, qos=1)
        self._clear_button_discovery(client)
        self._publish_volume_discovery(client)

    def _clear_button_discovery(self, client: Any) -> None:
        prefix = str(self.parameters["discovery_prefix"])
        for action in ("up", "down"):
            for suffix in (action, f"01_{action}" if action == "up" else f"02_{action}"):
                topic = f"{prefix}/button/fensoundswitch_{self._token}_{suffix}/config"
                client.publish(topic, "", qos=1, retain=True)

    def _publish_volume_discovery(self, client: Any) -> None:
        prefix = str(self.parameters["discovery_prefix"])
        unique_id = f"fensoundswitch_{self._token}_volume"
        topic = f"{prefix}/number/{unique_id}/config"
        payload = {
            "name": "Volume",
            "unique_id": unique_id,
            "command_topic": self._command_topic,
            "min": 0,
            "max": self.parameters["max_value"],
            "step": 1,
            "mode": "slider",
            "device": {
                "identifiers": [f"fensoundswitch_{self._token}"],
                "name": f"FenSoundSwitch {self._token}",
                "manufacturer": "FenSoundSwitch",
                "model": "MQTT route input",
            },
        }
        client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)
        self.logger.info("MQTT Home Assistant volume slider discovery published: route=%s max=%d.", self.route_id, self.parameters["max_value"])

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        if getattr(message, "topic", None) != self._command_topic:
            return
        payload = getattr(message, "payload", b"")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")
        value = str(payload).strip().casefold()
        try:
            volume = int(value)
        except ValueError:
            volume = -1
        if 0 <= volume <= int(self.parameters["max_value"]):
            self.logger.info("MQTT route volume accepted: route=%s target=%d.", self.route_id, volume)
            self.dispatch_volume(self.route_id, volume)
        else:
            self.logger.warning("MQTT route command ignored: route=%s unsupported payload.", self.route_id)

    def shutdown(self, timeout: float) -> bool:
        client, self._client = self._client, None
        self._started = False
        if client is None:
            return True
        try:
            client.disconnect()
            client.loop_stop()
            self.logger.info("MQTT route input stopped: route=%s.", self.route_id)
            return True
        except Exception as exc:
            self.logger.warning("MQTT route input shutdown failed: route=%s error=%s.", self.route_id, exc.__class__.__name__)
            return False


class MqttInputPlugin:
    plugin_id = "mqtt-input"
    name = "MQTT input"
    description = "Receives MQTT volume commands and publishes a Home Assistant MQTT volume slider for each route."
    input_id = "mqtt"
    input_name = "MQTT / Home Assistant"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def configure(self, parent: Any) -> None:
        return None

    def create_input(self, parameters: object) -> object:
        return validate_parameters(parameters)

    def create_route_input(self, route_id: str, parameters: object) -> MqttRouteInput:
        if not isinstance(route_id, str) or not route_id:
            raise ValueError("MQTT route ID is invalid.")
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise ValueError("MQTT support is not installed.") from exc
        return MqttRouteInput(route_id, validate_parameters(parameters), mqtt.Client, self._host.logger, self._host.dispatch_route_volume)

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        try:
            values = validate_parameters(parameters)
        except ValueError:
            return "Configure an MQTT broker and topic prefix."
        return f"Broker: {values['host']}:{values['port']}; Home Assistant discovery: {values['discovery_prefix']}; slider max: {values['max_value']}."

    def configure_route_input(self, parent: Any, parameters: dict[str, object], on_save: Callable[[dict[str, object]], None]) -> None:
        host = self._host
        window = tk.Toplevel(parent)
        window.title("Configure MQTT route")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        defaults = {"host": "", "port": str(DEFAULT_PORT), "username": "", "password": "", "discovery_prefix": DEFAULT_DISCOVERY_PREFIX, "topic_prefix": "fensoundswitch", "max_value": "100"}
        values = {name: tk.StringVar(value=str(parameters.get(name, default))) for name, default in defaults.items()}
        labels = (("host", "Broker host:"), ("port", "Broker port:"), ("username", "Username:"), ("password", "Password:"), ("discovery_prefix", "Discovery prefix:"), ("topic_prefix", "Topic prefix:"), ("max_value", "Slider maximum (1-100):"))
        for row, (name, label) in enumerate(labels):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=(0, 6))
            ttk.Entry(frame, textvariable=values[name], show="*" if name == "password" else "").grid(row=row, column=1, sticky="ew", pady=(0, 6))
        status = tk.StringVar(value="Home Assistant discovers a retained 0-100 volume slider after the broker connection succeeds.")
        ttk.Label(frame, textvariable=status, wraplength=500).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        def save() -> None:
            try:
                raw = {name: value.get() for name, value in values.items()}
                raw["port"] = int(raw["port"])
                raw["max_value"] = int(raw["max_value"])
                on_save(validate_parameters(raw))
            except (ValueError, TypeError) as exc:
                status.set(str(exc))
                return
            window.destroy()
        ttk.Button(frame, text="Save", command=save).grid(row=8, column=0, sticky="w", pady=(12, 0))
        ttk.Button(frame, text="Cancel", command=window.destroy).grid(row=8, column=1, sticky="e", pady=(12, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("MQTT input does not expose shortcut actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> MqttInputPlugin:
    return MqttInputPlugin()
