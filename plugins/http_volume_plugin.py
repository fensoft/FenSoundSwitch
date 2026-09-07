from __future__ import annotations

import http.client
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Mapping

from plugin_api import PLUGIN_API_VERSION, PluginHostContext, plugin_ui_document, plugin_ui_result


REQUEST_TIMEOUT_SECONDS = 2.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_URL_LENGTH = 2048
MAX_HEADERS_BYTES = 8 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_RESPONSE_FIELD_LENGTH = 128
_READ_METHODS = frozenset(("GET", "POST"))
_WRITE_METHODS = frozenset(("POST", "PUT", "PATCH"))
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HEADERS = frozenset((
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
))
_PARAMETER_KEYS = frozenset((
    "read_url",
    "read_method",
    "write_url",
    "write_method",
    "headers",
    "body_template",
    "response_field",
))


class HttpVolumeError(RuntimeError):
    """The configured HTTP endpoint did not return a confirmed volume."""


@dataclass(frozen=True)
class HttpVolumeConfig:
    read_url: str
    read_method: str
    write_url: str
    write_method: str
    headers: dict[str, str]
    body_template: str
    response_field: str


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not supported.")


def _strict_json(value: str, label: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate key.")
            result[key] = item
        return result

    try:
        return json.loads(value, parse_constant=_reject_constant, object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} must be valid finite JSON.") from exc


def _url(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError(f"{label} must be a non-empty HTTP or HTTPS URL of at most {MAX_URL_LENGTH} characters.")
    if any(ord(character) < 33 or character == "\\" for character in value):
        raise ValueError(f"{label} contains an invalid character.")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} is malformed.") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must use http:// or https:// and include a host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain URL credentials.")
    if "#" in value:
        raise ValueError(f"{label} must not contain a fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} contains an invalid TCP port.")
    return value


def _headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(name, str) or not isinstance(item, str) for name, item in value.items()):
        raise ValueError("HTTP headers must be a JSON object containing only text names and values.")
    if len(value) > 32:
        raise ValueError("HTTP headers may contain at most 32 fields.")
    result: dict[str, str] = {}
    seen: set[str] = set()
    for name, item in value.items():
        lowered = name.casefold()
        if not _HEADER_NAME.fullmatch(name) or lowered in _FORBIDDEN_HEADERS or lowered.startswith("proxy-"):
            raise ValueError(f"HTTP header {name!r} is invalid or controlled by the application.")
        if lowered in seen:
            raise ValueError("HTTP header names must be unique without regard to case.")
        if not item or any(ord(character) < 32 or ord(character) == 127 for character in item):
            raise ValueError(f"HTTP header {name!r} has an invalid value.")
        seen.add(lowered)
        result[name] = item
    try:
        compact = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("HTTP headers must contain valid finite JSON text.") from exc
    if len(compact) > MAX_HEADERS_BYTES:
        raise ValueError(f"HTTP headers may occupy at most {MAX_HEADERS_BYTES} UTF-8 bytes.")
    return result


def _body_template(value: object) -> str:
    if not isinstance(value, str) or value.count("{volume}") != 1:
        raise ValueError("HTTP request body must contain exactly one {volume} placeholder.")
    try:
        rendered = value.replace("{volume}", "100").encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("HTTP request body must be valid UTF-8 text.") from exc
    if len(rendered) > MAX_BODY_BYTES:
        raise ValueError(f"HTTP request body may occupy at most {MAX_BODY_BYTES} UTF-8 bytes.")
    _strict_json(rendered.decode("utf-8"), "HTTP request body")
    return value


def validate_parameters(parameters: object) -> HttpVolumeConfig:
    required = _PARAMETER_KEYS - {"headers"}
    if not isinstance(parameters, dict) or set(parameters) - _PARAMETER_KEYS or not required <= set(parameters):
        raise ValueError("HTTP volume route settings are incomplete or contain unknown fields.")
    read_method, write_method = parameters.get("read_method"), parameters.get("write_method")
    if read_method not in _READ_METHODS:
        raise ValueError("HTTP read method must be GET or POST.")
    if write_method not in _WRITE_METHODS:
        raise ValueError("HTTP write method must be POST, PUT, or PATCH.")
    response_field = parameters.get("response_field")
    if (
        not isinstance(response_field, str)
        or not response_field
        or len(response_field) > MAX_RESPONSE_FIELD_LENGTH
        or any(ord(character) < 32 for character in response_field)
    ):
        raise ValueError("HTTP response field must be a non-empty JSON key of at most 128 characters.")
    return HttpVolumeConfig(
        _url(parameters.get("read_url"), "HTTP read URL"),
        read_method,
        _url(parameters.get("write_url"), "HTTP write URL"),
        write_method,
        _headers(parameters.get("headers")),
        _body_template(parameters.get("body_template")),
        response_field,
    )


def _saved_parameters(config: HttpVolumeConfig) -> dict[str, object]:
    return {
        "read_url": config.read_url,
        "read_method": config.read_method,
        "write_url": config.write_url,
        "write_method": config.write_method,
        "headers": dict(config.headers),
        "body_template": config.body_template,
        "response_field": config.response_field,
    }


def _parse_headers_editor(value: object) -> dict[str, str]:
    if not isinstance(value, str):
        raise ValueError("HTTP headers must be entered as a compact JSON object.")
    if not value.strip():
        return {}
    decoded = _strict_json(value, "HTTP headers")
    return _headers(decoded)


class HttpVolumeOutput:
    provider_name = "Generic HTTP volume"
    supports_fast_volume_write = False
    supports_native_mute = False

    def __init__(self, config: HttpVolumeConfig) -> None:
        self._config = config
        self._lock = threading.Lock()

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        return True, None

    def read_volume(self) -> int:
        with self._lock:
            return self._read_volume_locked()

    def write_volume(self, target_volume: int) -> int:
        target = max(0, min(100, int(target_volume)))
        with self._lock:
            body = self._config.body_template.replace("{volume}", str(target)).encode("utf-8")
            self._request(self._config.write_url, self._config.write_method, body)
            confirmed = self._read_volume_locked()
            if confirmed != target:
                raise HttpVolumeError("The HTTP endpoint did not confirm the requested volume.")
            return confirmed

    def _read_volume_locked(self) -> int:
        payload = self._request(self._config.read_url, self._config.read_method, None)
        try:
            text = payload.decode("utf-8")
            decoded = _strict_json(text, "HTTP response")
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpVolumeError("The HTTP endpoint returned malformed JSON.") from exc
        if not isinstance(decoded, dict):
            raise HttpVolumeError("The HTTP endpoint response must be a JSON object.")
        volume = decoded.get(self._config.response_field)
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            raise HttpVolumeError("The HTTP endpoint returned an invalid volume; expected an integer from 0 through 100.")
        return volume

    def _request(self, url: str, method: str, body: bytes | None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        connection_type = http.client.HTTPSConnection if parsed.scheme.casefold() == "https" else http.client.HTTPConnection
        connection: http.client.HTTPConnection | None = None
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection = connection_type(parsed.hostname, parsed.port, timeout=REQUEST_TIMEOUT_SECONDS)
            connection.request(method, target, body=body, headers=dict(self._config.headers))
            response = connection.getresponse()
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise HttpVolumeError("The HTTP endpoint response was too large.")
            if not 200 <= response.status <= 299:
                if 300 <= response.status <= 399:
                    raise HttpVolumeError(f"The HTTP endpoint returned redirect status {response.status}; redirects are not allowed.")
                raise HttpVolumeError(f"The HTTP endpoint returned status {response.status}.")
            return payload
        except HttpVolumeError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise HttpVolumeError(f"Could not communicate with the configured HTTP endpoint: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None


class HttpVolumePlugin:
    plugin_id = "http-volume"
    name = "Generic HTTP volume"
    description = "Controls a route output through configurable HTTP JSON requests."
    provider_name = HttpVolumeOutput.provider_name

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> HttpVolumeOutput:
        return HttpVolumeOutput(validate_parameters(parameters))

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        try:
            config = validate_parameters(dict(parameters))
            values = _saved_parameters(config)
        except (TypeError, ValueError):
            values = {
                "read_url": "",
                "read_method": "GET",
                "write_url": "",
                "write_method": "POST",
                "headers": {},
                "body_template": '{"volume":{volume}}',
                "response_field": "volume",
            }
        headers = json.dumps(values["headers"], ensure_ascii=False, separators=(",", ":"))
        return plugin_ui_document(
            "Configure generic HTTP volume route",
            [
                {"id": "read_url", "type": "text", "label": "Read URL", "value": values["read_url"], "required": True},
                {"id": "read_method", "type": "choice", "label": "Read method", "value": values["read_method"], "options": [{"label": method, "value": method} for method in ("GET", "POST")]},
                {"id": "write_url", "type": "text", "label": "Write URL", "value": values["write_url"], "required": True},
                {"id": "write_method", "type": "choice", "label": "Write method", "value": values["write_method"], "options": [{"label": method, "value": method} for method in ("POST", "PUT", "PATCH")]},
                {"id": "headers", "type": "text", "label": "Headers (compact JSON object)", "value": headers, "description": "Optional headers are applied to both requests."},
                {"id": "body_template", "type": "text", "label": "Write JSON body template", "value": values["body_template"], "required": True, "description": "Include exactly one {volume} placeholder."},
                {"id": "response_field", "type": "text", "label": "Read response volume field", "value": values["response_field"], "required": True},
            ],
            [{"id": "save", "label": "Save", "kind": "submit", "async": False}],
            "All route parameters, including headers and request bodies, are stored in settings and included in configuration exports. Do not put secrets in this route.",
        )

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown HTTP volume UI action {action_id!r}.")
        parameters = dict(values)
        parameters["headers"] = _parse_headers_editor(parameters.get("headers", ""))
        return plugin_ui_result("save", values=_saved_parameters(validate_parameters(parameters)))

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            config = validate_parameters(parameters)
        except ValueError:
            return "No HTTP volume endpoint configured for this route."
        return f"Configured: {config.read_method} {config.read_url}"

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> HttpVolumePlugin:
    return HttpVolumePlugin()
