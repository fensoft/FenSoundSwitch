from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping

import ddc
from ddc import DDCError, SavedMonitorSelection, SelectionMatchStatus, enumerate_monitors, match_selected_monitor, saved_monitor_selection_from_json, saved_monitor_selection_to_json
from plugin_api import PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION, PluginHostContext, SlotAction, plugin_ui_document, plugin_ui_result


PLUGIN_API_VERSION = HOST_PLUGIN_API_VERSION
BRIGHTNESS_ACTION = "set-brightness"
CONTRAST_ACTION = "set-contrast"
HTTP_ACTION = "http-request"
POWER_ACTION = "set-power-plan"
HTTP_TIMEOUT_MAX = 30
HTTP_HEADERS_MAX = 32
HTTP_BODY_MAX = 65536
POWERCFG_TIMEOUT = 10.0
_GUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_POWER_LINE = re.compile(r"Power Scheme GUID:\s*([0-9a-fA-F-]{36})\s*\(([^\r\n)]*)\)(?:\s*\*)?", re.IGNORECASE)
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_FORBIDDEN_HEADERS = {
    "connection", "content-length", "host", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request: object, _file_pointer: object, _code: int, _message: str, _headers: object, _new_url: str) -> None:
        return None


def _open_http(request: urllib.request.Request, timeout: float) -> object:
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _percentage(value: object) -> int:
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError("DDC value must be from 0 to 100.") from exc
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("DDC value must be from 0 to 100.")
    return value


def _ddc_parameters(parameters: Mapping[str, object]) -> tuple[SavedMonitorSelection, int] | None:
    if set(parameters) != {"selected_monitor", "value"}:
        return None
    selection = saved_monitor_selection_from_json(parameters.get("selected_monitor"))
    if selection is None or selection.identity is None:
        return None
    try:
        value = _percentage(parameters.get("value"))
    except ValueError:
        return None
    return selection, value


def _guid(value: object) -> str:
    if not isinstance(value, str) or _GUID_PATTERN.fullmatch(value.strip()) is None:
        raise ValueError("Windows power plan must be a valid GUID.")
    return value.strip().lower()


def _http_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    if set(parameters) != {"url", "method", "timeout", "headers", "body"}:
        raise ValueError("HTTP request settings are invalid.")
    raw_url = parameters.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip() or len(raw_url) > 2048 or any(ord(char) < 32 for char in raw_url):
        raise ValueError("HTTP URL is invalid.")
    url = raw_url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("HTTP URL is invalid.") from exc
    hostname = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(char.isspace() for char in url)
        or "\\" in url
        or any(ord(char) > 127 for char in url)
        or (":" not in hostname and any(
            not label or len(label) > 63 or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) is None
            for label in hostname.removesuffix(".").split(".")
        ))
    ):
        raise ValueError("HTTP URL must be an http or https URL without credentials or a fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("HTTP URL port is invalid.")
    method = parameters.get("method")
    if method not in _METHODS:
        raise ValueError("HTTP method is invalid.")
    timeout = parameters.get("timeout")
    if isinstance(timeout, str):
        try:
            timeout = int(timeout)
        except ValueError as exc:
            raise ValueError("HTTP timeout must be from 1 to 30 seconds.") from exc
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= HTTP_TIMEOUT_MAX:
        raise ValueError("HTTP timeout must be from 1 to 30 seconds.")
    headers = parameters.get("headers")
    if not isinstance(headers, dict) or len(headers) > HTTP_HEADERS_MAX:
        raise ValueError("HTTP headers must be an object with at most 32 entries.")
    normalized_headers: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name or len(name) > 128 or len(value) > 4096 or any(char in name + value for char in "\r\n"):
            raise ValueError("HTTP header names and values are invalid.")
        if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None:
            raise ValueError("HTTP header name is invalid.")
        if name.casefold() in _FORBIDDEN_HEADERS:
            raise ValueError("HTTP header is controlled by the application and cannot be overridden.")
        normalized_headers[name] = value
    body = parameters.get("body")
    if not isinstance(body, str) or len(body.encode("utf-8")) > HTTP_BODY_MAX:
        raise ValueError("HTTP body must be text up to 65536 UTF-8 bytes.")
    return {"url": url, "method": method, "timeout": timeout, "headers": normalized_headers, "body": body}


def _power_parameters(parameters: Mapping[str, object]) -> tuple[str, str] | None:
    if set(parameters) != {"scheme_guid", "scheme_name"}:
        return None
    try:
        guid = _guid(parameters.get("scheme_guid"))
    except ValueError:
        return None
    name = parameters.get("scheme_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return None
    return guid, name.strip()


class SystemAutomationPlugin:
    plugin_id = "system-automation"
    name = "System automation"
    description = "Runs bounded DDC, HTTP, and Windows power-plan signal actions."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._monitors: tuple[tuple[dict[str, object], str], ...] = ()
        self._power_plans: tuple[tuple[str, str], ...] = ()
        self._state_lock = threading.Lock()
        self._power_lock = threading.Lock()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        host.report_status("Ready")

    def get_slot_actions(self) -> list[SlotAction]:
        return [
            SlotAction(BRIGHTNESS_ACTION, "Set monitor brightness", "Sets and confirms DDC/CI brightness on one exact monitor."),
            SlotAction(CONTRAST_ACTION, "Set monitor contrast", "Sets and confirms DDC/CI contrast on one exact monitor."),
            SlotAction(HTTP_ACTION, "Send HTTP request", "Sends one bounded HTTP or HTTPS request."),
            SlotAction(POWER_ACTION, "Change Windows power plan", "Activates and confirms one Windows power scheme."),
        ]

    def get_slot_ui(self, action_id: str, parameters: Mapping[str, object]) -> dict[str, object]:
        if action_id in {BRIGHTNESS_ACTION, CONTRAST_ACTION}:
            configured = _ddc_parameters(parameters)
            selected = saved_monitor_selection_to_json(configured[0]) if configured else parameters.get("selected_monitor")
            with self._state_lock:
                monitors = self._monitors
            options = [{"value": value, "label": label} for value, label in monitors]
            if isinstance(selected, dict) and not any(option["value"] == selected for option in options):
                selection = saved_monitor_selection_from_json(selected)
                if selection is not None and selection.identity is not None:
                    options.append({"value": selected, "label": selection.description})
            noun = "brightness" if action_id == BRIGHTNESS_ACTION else "contrast"
            return plugin_ui_document(
                f"Configure monitor {noun}",
                [
                    {"id": "selected_monitor", "type": "select", "label": "Monitor", "value": selected, "options": options, "required": True},
                    {"id": "value", "type": "integer", "label": noun.title(), "value": configured[1] if configured else parameters.get("value", 50), "minimum": 0, "maximum": 100},
                ],
                [{"id": "refresh", "label": "Refresh monitors", "kind": "action", "async": True}, {"id": "save", "label": "Save", "kind": "submit", "async": False}],
                "Monitor enumeration occurs only when Refresh monitors is selected.",
            )
        if action_id == HTTP_ACTION:
            headers = parameters.get("headers", {})
            headers_text = json.dumps(headers, separators=(",", ":")) if isinstance(headers, dict) else "{}"
            return plugin_ui_document("Configure HTTP request", [
                {"id": "url", "type": "text", "label": "URL", "value": parameters.get("url", ""), "required": True},
                {"id": "method", "type": "choice", "label": "Method", "value": parameters.get("method", "GET"), "options": [{"label": item, "value": item} for item in _METHODS], "required": True},
                {"id": "timeout", "type": "integer", "label": "Timeout (seconds)", "value": parameters.get("timeout", 10), "minimum": 1, "maximum": HTTP_TIMEOUT_MAX},
                {"id": "headers", "type": "text", "label": "Headers (JSON object)", "value": headers_text},
                {"id": "body", "type": "text", "label": "Body", "value": parameters.get("body", "")},
            ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}])
        if action_id == POWER_ACTION:
            configured = _power_parameters(parameters)
            with self._state_lock:
                plans = self._power_plans
            options = [{"value": guid, "label": name} for guid, name in plans]
            if configured and not any(option["value"] == configured[0] for option in options):
                options.append({"value": configured[0], "label": configured[1]})
            return plugin_ui_document("Configure Windows power plan", [{"id": "scheme_guid", "type": "choice", "label": "Power plan", "value": configured[0] if configured else parameters.get("scheme_guid", ""), "options": options, "required": True}], [{"id": "refresh", "label": "Refresh power plans", "kind": "action", "async": True}, {"id": "save", "label": "Save", "kind": "submit", "async": False}], "Power plans are enumerated only when Refresh power plans is selected.")
        raise ValueError(f"Unknown system automation action {action_id!r}.")

    def invoke_slot_ui_action(self, action_id: str, ui_action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if ui_action_id == "refresh" and action_id in {BRIGHTNESS_ACTION, CONTRAST_ACTION}:
            threading.Thread(target=self._refresh_monitors, name="system-automation-monitor-refresh", daemon=True).start()
            return plugin_ui_result("complete", message="Monitor refresh started.")
        if ui_action_id == "refresh" and action_id == POWER_ACTION:
            threading.Thread(target=self._refresh_power_plans, name="system-automation-power-refresh", daemon=True).start()
            return plugin_ui_result("complete", message="Power-plan refresh started.")
        if ui_action_id != "save":
            raise ValueError("Unknown system automation step configuration action.")
        if action_id in {BRIGHTNESS_ACTION, CONTRAST_ACTION}:
            selection = saved_monitor_selection_from_json(values.get("selected_monitor"))
            if set(values) != {"selected_monitor", "value"} or selection is None or selection.identity is None:
                raise ValueError("Select a monitor with a stable identity.")
            normalized = {"selected_monitor": saved_monitor_selection_to_json(selection), "value": _percentage(values.get("value"))}
        elif action_id == HTTP_ACTION:
            raw = dict(values)
            if set(raw) != {"url", "method", "timeout", "headers", "body"} or not isinstance(raw.get("headers"), str):
                raise ValueError("HTTP request settings are invalid.")
            try:
                raw["headers"] = json.loads(raw["headers"])
            except (TypeError, ValueError) as exc:
                raise ValueError("HTTP headers must be a valid JSON object.") from exc
            normalized = _http_parameters(raw)
        elif action_id == POWER_ACTION:
            if set(values) != {"scheme_guid"}:
                raise ValueError("Windows power plan settings are invalid.")
            guid = _guid(values.get("scheme_guid"))
            with self._state_lock:
                name = next((name for candidate, name in self._power_plans if candidate == guid), None)
            if name is None:
                raise ValueError("Refresh power plans and select an available plan.")
            normalized = {"scheme_guid": guid, "scheme_name": name}
        else:
            raise ValueError(f"Unknown system automation action {action_id!r}.")
        return plugin_ui_result("save", values=normalized)

    def slot_summary(self, action_id: str, parameters: Mapping[str, object]) -> str:
        if action_id in {BRIGHTNESS_ACTION, CONTRAST_ACTION}:
            configured = _ddc_parameters(parameters)
            return f"{configured[0].description}: {configured[1]}%" if configured else "Not configured"
        if action_id == HTTP_ACTION:
            try:
                values = _http_parameters(parameters)
                return f"{values['method']} {values['url']}"
            except ValueError:
                return "Not configured"
        if action_id == POWER_ACTION:
            configured = _power_parameters(parameters)
            return configured[1] if configured else "Not configured"
        return ""

    def run_slot(self, action_id: str, parameters: Mapping[str, object]) -> None:
        if action_id in {BRIGHTNESS_ACTION, CONTRAST_ACTION}:
            configured = _ddc_parameters(parameters)
            if configured is None:
                raise ValueError("Configure this DDC automation step first.")
            selection, value = configured
            monitors = enumerate_monitors()
            match = match_selected_monitor(monitors, selection)
            if match.status != SelectionMatchStatus.FOUND or match.index is None:
                raise DDCError("The selected monitor is unavailable or its identity is ambiguous.")
            monitor_ref = monitors[match.index]
            getter_name, setter_name = (("get_luminance", "set_luminance") if action_id == BRIGHTNESS_ACTION else ("get_contrast", "set_contrast"))
            try:
                with ddc._OPERATION_LOCK:
                    with monitor_ref.monitor:
                        current = _percentage(getattr(monitor_ref.monitor, getter_name)())
                        if current != value:
                            getattr(monitor_ref.monitor, setter_name)(value)
                        confirmed = _percentage(getattr(monitor_ref.monitor, getter_name)())
            except Exception as exc:
                raise DDCError(f"Failed to set monitor {action_id.removeprefix('set-')}: {exc}") from exc
            if confirmed != value:
                raise DDCError("The monitor did not confirm the requested value.")
            return
        if action_id == HTTP_ACTION:
            values = _http_parameters(parameters)
            data = str(values["body"]).encode("utf-8") if values["body"] or values["method"] in {"POST", "PUT", "PATCH"} else None
            request = urllib.request.Request(str(values["url"]), data=data, headers=dict(values["headers"]), method=str(values["method"]))
            try:
                with _open_http(request, float(values["timeout"])) as response:
                    raw_status = getattr(response, "status", None)
                    status = int(raw_status if raw_status is not None else response.getcode())
                    response.read(1)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"HTTP request failed with status {exc.code}.") from exc
            if not 200 <= status < 300:
                raise RuntimeError(f"HTTP request failed with status {status}.")
            return
        if action_id == POWER_ACTION:
            configured = _power_parameters(parameters)
            if configured is None:
                raise ValueError("Configure this Windows power-plan step first.")
            guid, _name = configured
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with self._power_lock:
                subprocess.run(["powercfg", "/setactive", guid], check=True, capture_output=True, text=True, timeout=POWERCFG_TIMEOUT, shell=False, creationflags=flags)
                active = subprocess.run(["powercfg", "/getactivescheme"], check=True, capture_output=True, text=True, timeout=POWERCFG_TIMEOUT, shell=False, creationflags=flags)
            match = _POWER_LINE.search(active.stdout)
            if match is None or match.group(1).lower() != guid:
                raise RuntimeError("Windows did not confirm the requested active power plan.")
            return
        raise ValueError(f"Unknown system automation action {action_id!r}.")

    def _refresh_monitors(self) -> None:
        monitors = []
        for monitor in enumerate_monitors():
            if monitor.selection_key is not None:
                monitors.append((saved_monitor_selection_to_json(monitor.selection_key), monitor.display_name))
        with self._state_lock:
            self._monitors = tuple(monitors)

    def _refresh_power_plans(self) -> None:
        result = subprocess.run(["powercfg", "/list"], check=True, capture_output=True, text=True, timeout=POWERCFG_TIMEOUT, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        plans = tuple((match.group(1).lower(), match.group(2).strip()) for match in _POWER_LINE.finditer(result.stdout))
        with self._state_lock:
            self._power_plans = plans

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("System automation does not expose shortcut actions.")

    def shutdown(self, timeout: float) -> bool:
        acquired = self._power_lock.acquire(timeout=max(0.0, timeout))
        if acquired:
            self._power_lock.release()
        return acquired


def create_plugin() -> SystemAutomationPlugin:
    return SystemAutomationPlugin()
