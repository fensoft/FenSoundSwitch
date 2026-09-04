from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from plugin_api import PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


DEFAULT_PORT = 1883
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_TOPIC_PREFIX = "fensoundswitch"
MAX_PROFILES = 32
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TOPIC_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROFILE_FIELDS = (
    "profile_id",
    "name",
    "host",
    "port",
    "username",
    "password",
    "discovery_prefix",
    "topic_prefix",
)


def _text(value: object, label: str, *, required: bool = True, maximum: int = 255) -> str:
    if not isinstance(value, str):
        raise ValueError(f"MQTT {label} must be text.")
    result = value.strip()
    if required and not result:
        raise ValueError(f"MQTT {label} is required.")
    if len(result) > maximum:
        raise ValueError(f"MQTT {label} is too long.")
    return result


def _topic(value: object, label: str) -> str:
    result = _text(value, label).strip("/")
    if not result or any(part in {"", "+", "#"} for part in result.split("/")):
        raise ValueError(f"MQTT {label} must be a concrete topic path.")
    return result


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError(f"MQTT {label} must be from {minimum} to {maximum}.") from exc
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"MQTT {label} must be from {minimum} to {maximum}.")
    return value


def _ha_metadata(parameters: Mapping[str, object]) -> tuple[str, str]:
    name = _text(parameters.get("ha_name"), "Home Assistant name", maximum=80)
    ha_id = _text(parameters.get("ha_id"), "Home Assistant ID", maximum=64)
    if _TOPIC_PART.fullmatch(ha_id) is None:
        raise ValueError("MQTT Home Assistant ID may contain only letters, numbers, underscores, and hyphens.")
    return name, ha_id


def _normalize_profile(values: Mapping[str, object], *, profile_id: str | None = None) -> dict[str, object]:
    if set(values) - set(_PROFILE_FIELDS):
        raise ValueError("MQTT profile has unknown settings.")
    selected_id = profile_id if profile_id is not None else values.get("profile_id")
    if not isinstance(selected_id, str) or _ID_PATTERN.fullmatch(selected_id) is None:
        raise ValueError("MQTT profile ID must match [a-z][a-z0-9-]{0,63}.")
    return {
        "profile_id": selected_id,
        "name": _text(values.get("name"), "profile name", maximum=80),
        "host": _text(values.get("host"), "broker host"),
        "port": _integer(values.get("port", DEFAULT_PORT), "broker port", 1, 65535),
        "username": _text(values.get("username", ""), "username", required=False),
        "password": _text(values.get("password", ""), "password", required=False),
        "discovery_prefix": _topic(values.get("discovery_prefix", DEFAULT_DISCOVERY_PREFIX), "discovery prefix"),
        "topic_prefix": _topic(values.get("topic_prefix", DEFAULT_TOPIC_PREFIX), "topic prefix"),
    }


def validate_parameters(parameters: object) -> dict[str, object]:
    if not isinstance(parameters, dict):
        raise ValueError("MQTT input settings must be an object.")
    if "profile_id" in parameters:
        allowed = {"profile_id", "ha_name", "ha_id", "max_value"}
        if set(parameters) - allowed:
            raise ValueError("MQTT input has unknown settings.")
        profile_id = parameters.get("profile_id")
        if not isinstance(profile_id, str) or _ID_PATTERN.fullmatch(profile_id) is None:
            raise ValueError("MQTT profile ID is invalid.")
        ha_name, ha_id = _ha_metadata(parameters)
        return {
            "profile_id": profile_id,
            "ha_name": ha_name,
            "ha_id": ha_id,
            "max_value": _integer(parameters.get("max_value", 100), "maximum volume", 1, 100),
        }

    allowed = {"host", "port", "username", "password", "discovery_prefix", "topic_prefix", "max_value", "button_step"}
    if set(parameters) - allowed:
        raise ValueError("MQTT input has unknown settings.")
    return {
        "host": _text(parameters.get("host"), "broker host"),
        "port": _integer(parameters.get("port", DEFAULT_PORT), "broker port", 1, 65535),
        "username": _text(parameters.get("username", ""), "username", required=False),
        "password": _text(parameters.get("password", ""), "password", required=False),
        "discovery_prefix": _topic(parameters.get("discovery_prefix", DEFAULT_DISCOVERY_PREFIX), "discovery prefix"),
        "topic_prefix": _topic(parameters.get("topic_prefix", DEFAULT_TOPIC_PREFIX), "topic prefix"),
        "max_value": _integer(parameters.get("max_value", 100), "maximum volume", 1, 100),
    }


def _route_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_-]", "_", value.casefold())
    return token[:64] or "route"


def _shutdown_client(client: Any, timeout: float) -> bool:
    completed = threading.Event()
    failed: list[bool] = []

    def stop() -> None:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            failed.append(True)
        finally:
            completed.set()

    threading.Thread(target=stop, name="mqtt-client-stop", daemon=True).start()
    return completed.wait(max(0.0, timeout)) and not failed


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
        entity_token = _route_token(str(self.parameters.get("profile_id", self.route_id)))
        ha_id = self.parameters.get("ha_id")
        self._entity_token = f"{entity_token}_{ha_id}" if isinstance(ha_id, str) else self._token
        command_token = ha_id if isinstance(ha_id, str) else self._token
        self._command_topic = f"{self.parameters['topic_prefix']}/{command_token}/command"

    def start(self) -> None:
        if self._started:
            return
        client = self.client_factory(client_id=f"fensoundswitch-{self._token}")
        username = str(self.parameters["username"])
        if username:
            client.username_pw_set(username, str(self.parameters["password"]))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client
        self._started = True
        try:
            client.connect_async(str(self.parameters["host"]), int(self.parameters["port"]), keepalive=30)
            client.loop_start()
        except Exception:
            self._client = None
            self._started = False
            raise

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, result_code: int, *_args: Any) -> None:
        if not self._started or client is not self._client:
            return
        if result_code != 0:
            self.logger.warning("MQTT route input connection refused: route=%s result=%s.", self.route_id, result_code)
            return
        client.subscribe(self._command_topic, qos=1)
        if "ha_id" not in self.parameters:
            self._clear_button_discovery(client)
        self._publish_volume_discovery(client)

    def _clear_button_discovery(self, client: Any) -> None:
        prefix = str(self.parameters["discovery_prefix"])
        for action in ("up", "down"):
            for suffix in (action, f"01_{action}" if action == "up" else f"02_{action}"):
                client.publish(f"{prefix}/button/fensoundswitch_{self._token}_{suffix}/config", "", qos=1, retain=True)

    def _publish_volume_discovery(self, client: Any) -> None:
        unique_id = f"fensoundswitch_{self._entity_token}_volume"
        name = str(self.parameters.get("ha_name", "Volume"))
        payload = {
            "name": name,
            "unique_id": unique_id,
            "command_topic": self._command_topic,
            "min": 0,
            "max": self.parameters["max_value"],
            "step": 1,
            "mode": "slider",
            "device": {
                "identifiers": [f"fensoundswitch_{self._entity_token}"],
                "name": name if "ha_name" in self.parameters else f"FenSoundSwitch {self._token}",
                "manufacturer": "FenSoundSwitch",
                "model": "MQTT route input",
            },
        }
        topic = f"{self.parameters['discovery_prefix']}/number/{unique_id}/config"
        client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)

    def _on_message(self, client: Any, _userdata: Any, message: Any) -> None:
        if not self._started or client is not self._client or getattr(message, "topic", None) != self._command_topic:
            return
        payload = getattr(message, "payload", b"")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")
        try:
            volume = int(str(payload).strip())
        except ValueError:
            volume = -1
        if 0 <= volume <= int(self.parameters["max_value"]):
            self.dispatch_volume(self.route_id, volume)
        else:
            self.logger.warning("MQTT route command ignored: route=%s unsupported payload.", self.route_id)

    def shutdown(self, timeout: float) -> bool:
        client, self._client = self._client, None
        self._started = False
        if client is None:
            return True
        if _shutdown_client(client, timeout):
            return True
        self.logger.warning("MQTT route input shutdown did not complete: route=%s.", self.route_id)
        return False

    def remove(self, timeout: float) -> bool:
        client = self._client
        if client is not None and self._started:
            topic = f"{self.parameters['discovery_prefix']}/number/fensoundswitch_{self._entity_token}_volume/config"
            try:
                client.publish(topic, "", qos=1, retain=True)
            except Exception:
                pass
        return self.shutdown(timeout)


@dataclass
class MqttSignalTrigger:
    signal_id: str
    parameters: dict[str, object]
    client_factory: Callable[..., Any]
    dispatch: Callable[[str], None]
    logger: logging.Logger = logging.getLogger(__name__)

    def __post_init__(self) -> None:
        self._client: Any | None = None
        self._started = False
        self._signal_token = _route_token(self.signal_id)
        self._profile_token = _route_token(str(self.parameters["profile_id"]))
        self._ha_id = str(self.parameters["ha_id"])
        self._command_topic = f"{self.parameters['topic_prefix']}/automation/{self._ha_id}/command"

    def start(self) -> None:
        if self._started:
            return
        client = self.client_factory(client_id=f"fensoundswitch-signal-{self._signal_token}")
        username = str(self.parameters["username"])
        if username:
            client.username_pw_set(username, str(self.parameters["password"]))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client
        self._started = True
        try:
            client.connect_async(str(self.parameters["host"]), int(self.parameters["port"]), keepalive=30)
            client.loop_start()
        except Exception:
            self._client = None
            self._started = False
            raise

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, result_code: int, *_args: Any) -> None:
        if not self._started or client is not self._client or result_code != 0:
            return
        client.subscribe(self._command_topic, qos=1)
        unique_id = f"fensoundswitch_{self._profile_token}_{self._ha_id}"
        topic = f"{self.parameters['discovery_prefix']}/button/{unique_id}/config"
        payload = {"name": self.parameters["ha_name"], "unique_id": unique_id, "command_topic": self._command_topic, "payload_press": "PRESS"}
        client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)

    def _on_message(self, client: Any, _userdata: Any, message: Any) -> None:
        if not self._started or client is not self._client or getattr(message, "topic", None) != self._command_topic:
            return
        payload = getattr(message, "payload", b"")
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                return
        if payload == "PRESS":
            self.dispatch(self.signal_id)

    def shutdown(self, timeout: float) -> bool:
        client, self._client = self._client, None
        self._started = False
        if client is None:
            return True
        if _shutdown_client(client, timeout):
            return True
        self.logger.warning("MQTT signal trigger shutdown did not complete: signal=%s.", self.signal_id)
        return False

    def remove(self, timeout: float) -> bool:
        """Remove retained discovery before permanently replacing this trigger."""
        client = self._client
        if client is not None and self._started:
            unique_id = f"fensoundswitch_{self._profile_token}_{self._ha_id}"
            topic = f"{self.parameters['discovery_prefix']}/button/{unique_id}/config"
            try:
                client.publish(topic, "", qos=1, retain=True)
            except Exception:
                pass
        return self.shutdown(timeout)


class MqttInputPlugin:
    plugin_id = "mqtt-input"
    name = "MQTT input"
    description = "Receives MQTT volume commands and Home Assistant automation button presses through reusable broker profiles."
    input_id = "mqtt"
    input_name = "MQTT / Home Assistant"

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._profiles: list[dict[str, object]] = []

    def initialize(self, host: PluginHostContext) -> None:
        settings = host.load_plugin_settings()
        profiles = settings.get("profiles", []) if isinstance(settings, dict) else []
        if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
            raise ValueError("MQTT profiles settings are invalid.")
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        seen_names: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, dict):
                raise ValueError("MQTT profile must be an object.")
            value = _normalize_profile(profile)
            profile_id = str(value["profile_id"])
            if profile_id in seen:
                raise ValueError("MQTT profile IDs must be unique.")
            profile_name = str(value["name"]).casefold()
            if profile_name in seen_names:
                raise ValueError("MQTT profile names must be unique.")
            seen.add(profile_id)
            seen_names.add(profile_name)
            normalized.append(value)
        self._host = host
        self._profiles = normalized

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("MQTT input plugin has not been initialized.")
        return self._host

    def _save_profiles(self) -> None:
        self._require_host().save_plugin_settings({"schema_version": 1, "profiles": [dict(profile) for profile in self._profiles]})

    def _profile(self, profile_id: object) -> dict[str, object]:
        if not isinstance(profile_id, str) or _ID_PATTERN.fullmatch(profile_id) is None:
            raise ValueError("MQTT profile ID is invalid.")
        profile = next((item for item in self._profiles if item["profile_id"] == profile_id), None)
        if profile is None:
            raise ValueError("The selected MQTT profile does not exist.")
        return profile

    def list_mqtt_profiles(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(profile) for profile in self._profiles)

    def mqtt_profile_options(self) -> tuple[dict[str, str], ...]:
        return tuple({"label": str(profile["name"]), "value": str(profile["profile_id"])} for profile in self._profiles)

    def get_mqtt_profile_ui(self, profile_id: str | None) -> dict[str, object]:
        profile = self._profile(profile_id) if profile_id is not None else {
            "name": "", "host": "", "port": DEFAULT_PORT, "username": "", "password": "",
            "discovery_prefix": DEFAULT_DISCOVERY_PREFIX, "topic_prefix": DEFAULT_TOPIC_PREFIX,
        }
        editing = profile_id is not None
        fields = [
            {"id": "name", "type": "text", "label": "Profile name", "value": profile["name"], "required": True},
            {"id": "host", "type": "text", "label": "Broker host", "value": profile["host"], "required": True},
            {"id": "port", "type": "integer", "label": "Broker port", "value": profile["port"], "minimum": 1, "maximum": 65535},
            {"id": "username", "type": "text", "label": "Username", "value": profile["username"]},
            {"id": "password", "type": "password", "label": "Password", "value": "" if editing else profile["password"], "write_only": editing},
            {"id": "discovery_prefix", "type": "text", "label": "Discovery prefix", "value": profile["discovery_prefix"], "required": True},
            {"id": "topic_prefix", "type": "text", "label": "Topic prefix", "value": profile["topic_prefix"], "required": True},
        ]
        if editing:
            fields.insert(5, {"id": "clear_password", "type": "boolean", "label": "Clear saved password", "value": False})
        return plugin_ui_document("Edit MQTT profile" if editing else "Add MQTT profile", fields, [{"id": "save", "label": "Save", "kind": "submit", "async": False}])

    def invoke_mqtt_profile_ui(self, profile_id: str | None, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown MQTT profile UI action {action_id!r}.")
        if profile_id is None and len(self._profiles) >= MAX_PROFILES:
            raise ValueError(f"MQTT supports at most {MAX_PROFILES} profiles.")
        existing = self._profile(profile_id) if profile_id is not None else None
        raw = dict(values)
        clear_password = raw.pop("clear_password", False)
        if not isinstance(clear_password, bool):
            raise ValueError("MQTT clear-password setting must be true or false.")
        if clear_password:
            raw["password"] = ""
        elif existing is not None and not str(raw.get("password", "")).strip():
            raw["password"] = existing["password"]
        selected_id = profile_id or f"p-{uuid.uuid4().hex}"
        raw["profile_id"] = selected_id
        normalized = _normalize_profile(raw, profile_id=selected_id)
        if any(item is not existing and str(item["name"]).casefold() == str(normalized["name"]).casefold() for item in self._profiles):
            raise ValueError("MQTT profile names must be unique.")
        updated = list(self._profiles)
        if existing is None:
            if any(item["profile_id"] == selected_id for item in updated):
                raise ValueError("MQTT profile IDs must be unique.")
            updated.append(normalized)
        else:
            updated[updated.index(existing)] = normalized
        previous, self._profiles = self._profiles, updated
        try:
            self._save_profiles()
        except Exception:
            self._profiles = previous
            raise
        return plugin_ui_result("save", values=normalized)

    def remove_mqtt_profile(self, profile_id: str) -> bool:
        profile = next((item for item in self._profiles if item["profile_id"] == profile_id), None)
        if profile is None:
            return False
        previous = self._profiles
        self._profiles = [item for item in self._profiles if item is not profile]
        try:
            self._save_profiles()
        except Exception:
            self._profiles = previous
            raise
        return True

    def create_input(self, parameters: object) -> object:
        return validate_parameters(parameters)

    def _runtime_route_parameters(self, parameters: object) -> dict[str, object]:
        values = validate_parameters(parameters)
        if "profile_id" not in values:
            return values
        profile = self._profile(values["profile_id"])
        return {**profile, **values}

    @staticmethod
    def _mqtt_client_factory() -> Callable[..., Any]:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise ValueError("MQTT support is not installed.") from exc
        return mqtt.Client

    def create_route_input(self, route_id: str, parameters: object) -> MqttRouteInput:
        if not isinstance(route_id, str) or not route_id:
            raise ValueError("MQTT route ID is invalid.")
        host = self._require_host()
        return MqttRouteInput(route_id, self._runtime_route_parameters(parameters), self._mqtt_client_factory(), host.logger, host.dispatch_route_volume)

    def create_signal_trigger(self, signal_id: str, parameters: object, dispatch: Callable[[str], None]) -> MqttSignalTrigger:
        if not isinstance(signal_id, str) or not signal_id or not callable(dispatch):
            raise ValueError("MQTT signal trigger is invalid.")
        if not isinstance(parameters, dict) or set(parameters) != {"profile_id", "ha_name", "ha_id"}:
            raise ValueError("MQTT signal trigger settings are invalid.")
        profile = self._profile(parameters.get("profile_id"))
        ha_name, ha_id = _ha_metadata(parameters)
        runtime = {**profile, "ha_name": ha_name, "ha_id": ha_id}
        return MqttSignalTrigger(signal_id, runtime, self._mqtt_client_factory(), dispatch, self._require_host().logger)

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        try:
            values = validate_parameters(parameters)
            if "profile_id" in values:
                profile = self._profile(values["profile_id"])
                return f"Profile: {profile['name']}; Home Assistant: {values['ha_name']}; slider max: {values['max_value']}."
        except ValueError:
            return "Choose an MQTT profile and Home Assistant identity."
        return f"Broker: {values['host']}:{values['port']}; Home Assistant discovery: {values['discovery_prefix']}; slider max: {values['max_value']}."

    def get_route_input_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        fields = [
            {"id": "profile_id", "type": "choice", "label": "MQTT profile", "value": parameters.get("profile_id", ""), "options": list(self.mqtt_profile_options()), "required": True},
            {"id": "ha_name", "type": "text", "label": "Home Assistant name", "value": parameters.get("ha_name", ""), "required": True},
            {"id": "ha_id", "type": "text", "label": "Home Assistant ID", "value": parameters.get("ha_id", ""), "required": True},
            {"id": "max_value", "type": "integer", "label": "Slider maximum", "value": parameters.get("max_value", 100), "minimum": 1, "maximum": 100},
        ]
        return plugin_ui_document("Configure MQTT route", fields, [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Reuse a broker profile and give this route its own Home Assistant identity.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown MQTT UI action {action_id!r}.")
        return plugin_ui_result("save", values=validate_parameters(dict(values)))

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("MQTT input does not expose shortcut actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> MqttInputPlugin:
    return MqttInputPlugin()
