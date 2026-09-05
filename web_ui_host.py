"""Isolated pywebview child for the FenSoundSwitch command center.

The parent owns application state and passes an authenticated AF_PIPE endpoint.
This module deliberately imports pywebview only inside ``run`` so protocol and
asset tests never initialize a browser runtime.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Client, Connection
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import unquote, urlsplit


MAX_MESSAGE_BYTES = 64 * 1024
MAX_MESSAGE_DEPTH = 12
MAX_COLLECTION_ITEMS = 256
MAX_STRING_LENGTH = 16 * 1024
MAX_PENDING_REQUESTS = 32
REQUEST_TIMEOUT_SECONDS = 8.0
PIPE_PATTERN = re.compile(r"^\\\\\.\\pipe\\[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
METHOD_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,7}$")
ASSET_ROOT = Path(__file__).resolve().with_name("web")
ASSET_NAMES = frozenset(("index.html", "app.css", "app.js"))
WINDOW_ICON_PATH = Path(__file__).resolve().with_name("FenSoundSwitch.ico")
AUTHKEY_ENVIRONMENT_VARIABLE = "FENSOUNDSWITCH_PRESENTATION_AUTHKEY"


class BootstrapError(ValueError):
    """Raised when untrusted child bootstrap arguments are invalid."""


class ProtocolError(RuntimeError):
    """Raised when the authenticated peer violates the child protocol."""


class RemoteError(ProtocolError):
    """A well-formed application error returned by the parent."""


@dataclass(frozen=True)
class Bootstrap:
    pipe: str
    authkey: bytes


def parse_bootstrap(
    argv: Sequence[str],
    environment: dict[str, str] | None = None,
) -> Bootstrap:
    """Parse one pipe argument and inherit the secret outside the command line."""
    if len(argv) != 2 or argv[0] != "--pipe" or not argv[1]:
        raise BootstrapError("Expected exactly --pipe NAME.")
    pipe = argv[1]
    encoded_key = (environment or os.environ).get(AUTHKEY_ENVIRONMENT_VARIABLE, "")
    if PIPE_PATTERN.fullmatch(pipe) is not None:
        valid_pipe = True
    else:
        candidate = Path(pipe)
        valid_pipe = candidate.is_absolute() and candidate.parent.name in {"fensoundswitch", "fss"}
    if not valid_pipe:
        raise BootstrapError("Pipe name is invalid.")
    if re.fullmatch(r"[A-Za-z0-9_-]{22,86}", encoded_key) is None:
        raise BootstrapError("Authentication key encoding is invalid.")
    try:
        padding = "=" * (-len(encoded_key) % 4)
        authkey = base64.b64decode(encoded_key + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BootstrapError("Authentication key encoding is invalid.") from exc
    if not 16 <= len(authkey) <= 64:
        raise BootstrapError("Authentication key length is invalid.")
    return Bootstrap(pipe=pipe, authkey=authkey)


def resolve_asset(name: str) -> Path:
    if name not in ASSET_NAMES:
        raise ValueError("Unknown web asset.")
    path = (ASSET_ROOT / name).resolve()
    if path.parent != ASSET_ROOT.resolve() or not path.is_file():
        raise FileNotFoundError(f"Required web asset is missing: {name}")
    return path


def render_document(*, allow_native_bridge: bool = False) -> str:
    """Inline trusted local assets so pywebview never starts its HTTP server."""
    html = resolve_asset("index.html").read_text(encoding="utf-8")
    css = resolve_asset("app.css").read_text(encoding="utf-8")
    script = resolve_asset("app.js").read_text(encoding="utf-8")

    def digest(value: str) -> str:
        return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")

    html = html.replace("style-src 'self'", f"style-src 'sha256-{digest(css)}'")
    html = html.replace("script-src 'self'", f"script-src 'sha256-{digest(script)}'")
    html = html.replace('  <link rel="stylesheet" href="app.css">', f"  <style>{css}</style>")
    html = html.replace('  <script src="app.js" defer></script>', "")
    html = html.replace("</body>", f"  <script>{script}</script>\n</body>")
    if allow_native_bridge:
        # WebKit injects pywebview's local message bridge at runtime. Its script
        # is not hash-addressable, unlike the bundled application script.
        html = html.replace("script-src ", "script-src 'unsafe-inline' 'unsafe-eval' ")
    return html


def is_allowed_navigation(url: str) -> bool:
    """Allow navigation only to one of the bundled files."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "file" or parsed.netloc not in ("", "localhost"):
            return False
        candidate = Path(unquote(parsed.path).lstrip("/") if sys.platform == "win32" else unquote(parsed.path)).resolve()
    except (OSError, ValueError):
        return False
    return candidate in {resolve_asset(name) for name in ASSET_NAMES}


def apply_window_icon(window: object, icon_factory: Callable[[str], object] | None = None) -> object | None:
    """Apply the bundled icon to the native WinForms presentation window."""
    native = getattr(window, "native", None)
    if native is None or not WINDOW_ICON_PATH.is_file():
        return None
    try:
        if icon_factory is None:
            from System.Drawing import Icon  # type: ignore[import-not-found]

            icon_factory = Icon
        icon = icon_factory(str(WINDOW_ICON_PATH))
        native.Icon = icon
    except Exception:
        return None
    return icon


def _json_bytes(value: object) -> bytes:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError("Message is not finite JSON.") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the protocol limit.")
    return encoded


def _decode_json(payload: bytes) -> object:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the protocol limit.")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ProtocolError("Message number must be finite.")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Peer returned invalid JSON.") from exc
    _validate_json_value(value)
    return value


def _validate_json_value(value: object, depth: int = 0) -> None:
    if depth > MAX_MESSAGE_DEPTH:
        raise ProtocolError("Message nesting is too deep.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 2**63 - 1:
            raise ProtocolError("Message integer is out of range.")
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ProtocolError("Message number must be finite.")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ProtocolError("Message string is too long.")
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProtocolError("Message list has too many items.")
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProtocolError("Message object has too many fields.")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_STRING_LENGTH:
                raise ProtocolError("Message object keys must be bounded strings.")
            _validate_json_value(item, depth + 1)
        return
    raise ProtocolError("Message contains a non-JSON value.")


def make_request(request_id: int, method: str, params: object) -> bytes:
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
        raise ProtocolError("Request ID is invalid.")
    if not isinstance(method, str) or len(method) > 96 or METHOD_PATTERN.fullmatch(method) is None:
        raise ProtocolError("Method name is invalid.")
    if not isinstance(params, dict):
        raise ProtocolError("Request parameters must be an object.")
    return _json_bytes({"id": request_id, "method": method, "params": params})


def parse_response(payload: bytes, request_id: int) -> object:
    message = _decode_json(payload)
    if not isinstance(message, dict) or set(message) - {"id", "ok", "result", "error"}:
        raise ProtocolError("Response envelope is invalid.")
    if message.get("id") != request_id or not isinstance(message.get("ok"), bool):
        raise ProtocolError("Response correlation is invalid.")
    if message["ok"]:
        if "error" in message:
            raise ProtocolError("Successful response contains an error.")
        return message.get("result")
    error = message.get("error")
    if not isinstance(error, dict) or set(error) != {"code", "message"}:
        raise ProtocolError("Error response is invalid.")
    code, detail = error.get("code"), error.get("message")
    if not isinstance(code, str) or not code or len(code) > 64:
        raise ProtocolError("Error code is invalid.")
    if not isinstance(detail, str) or not detail or len(detail) > 1000:
        raise ProtocolError("Error message is invalid.")
    raise RemoteError(f"{code}: {detail}")


@dataclass
class _Pending:
    method: str
    params: dict[str, object]
    event: threading.Event
    result: object = None
    error: BaseException | None = None


class PipeBridge:
    """Serialize connection traffic and watch for parent EOF while idle."""

    def __init__(self, connection: Connection, on_disconnect: Callable[[], None]) -> None:
        self._connection = connection
        self._on_disconnect = on_disconnect
        self._pending: queue.Queue[_Pending | None] = queue.Queue(MAX_PENDING_REQUESTS)
        self._closed = threading.Event()
        self._next_id = 1
        self._thread = threading.Thread(target=self._run, name="web-ui-pipe", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def request(self, method: str, params: object) -> object:
        if not isinstance(params, dict):
            raise ProtocolError("Request parameters must be an object.")
        if self._closed.is_set():
            raise ProtocolError("The application connection is closed.")
        item = _Pending(method, params, threading.Event())
        try:
            self._pending.put_nowait(item)
        except queue.Full as exc:
            raise ProtocolError("Too many pending requests.") from exc
        if not item.event.wait(REQUEST_TIMEOUT_SECONDS + 0.5):
            raise ProtocolError("The application did not respond in time.")
        if item.error is not None:
            raise item.error
        return item.result

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._pending.put_nowait(None)
        except queue.Full:
            pass
        self._connection.close()

    def _disconnect(self, error: BaseException) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        while True:
            try:
                item = self._pending.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                item.error = error
                item.event.set()
        try:
            self._connection.close()
        finally:
            self._on_disconnect()

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                item = self._pending.get(timeout=0.1)
            except queue.Empty:
                try:
                    if self._connection.poll(0):
                        self._connection.recv_bytes(MAX_MESSAGE_BYTES)
                        raise ProtocolError("Unsolicited parent message.")
                except (EOFError, OSError, ProtocolError) as exc:
                    self._disconnect(exc)
                continue
            if item is None:
                break
            request_id = self._next_id
            self._next_id += 1
            try:
                self._connection.send_bytes(make_request(request_id, item.method, item.params))
                deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
                while not self._connection.poll(max(0.0, deadline - time.monotonic())):
                    if time.monotonic() >= deadline:
                        raise ProtocolError("The application did not respond in time.")
                item.result = parse_response(self._connection.recv_bytes(MAX_MESSAGE_BYTES), request_id)
            except RemoteError as exc:
                item.error = exc
                item.event.set()
                continue
            except (EOFError, OSError, ProtocolError) as exc:
                item.error = exc
                item.event.set()
                self._disconnect(exc)
                return
            item.event.set()


class WebApi:
    """Small API exposed by pywebview; values must remain JSON-compatible."""

    def __init__(self, bridge: PipeBridge) -> None:
        self._bridge = bridge
        self._window: Any = None
        self._webview: Any = None
        self._ready = False
        self._ready_lock = threading.Lock()
        self._last_visible_request: bool | None = None

    def attach_window(self, window: object, webview_module: object) -> None:
        self._window = window
        self._webview = webview_module

    def request(self, method: str, params: object | None = None) -> dict[str, object]:
        try:
            arguments = {} if params is None else params
            if not isinstance(arguments, dict):
                raise ProtocolError("Request parameters must be an object.")
            if method == "snapshot.get":
                result = self._snapshot(arguments)
            else:
                result = self._bridge.request(
                    "dispatch_action",
                    {"action": method, "arguments": arguments},
                )
            return {"ok": True, "result": result}
        except (ProtocolError, TypeError, ValueError) as exc:
            return {"ok": False, "error": {"code": "bridge_error", "message": str(exc)}}

    def _snapshot(self, params: dict[str, object]) -> object:
        if set(params) - {"revision"}:
            raise ProtocolError("Snapshot parameters are invalid.")
        revision = params.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ProtocolError("Snapshot revision must be an integer.")
        with self._ready_lock:
            if not self._ready:
                result = self._bridge.request("ready", {})
                self._ready = True
            else:
                result = self._bridge.request("get_snapshot", {"revision": revision})
        if not isinstance(result, dict):
            raise ProtocolError("Snapshot envelope is invalid.")
        changed = result.get("changed")
        response_revision = result.get("revision")
        if not isinstance(changed, bool) or isinstance(response_revision, bool) or not isinstance(response_revision, int):
            raise ProtocolError("Snapshot envelope is invalid.")
        if not changed:
            return {"revision": response_revision, "changed": False}
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ProtocolError("Snapshot value must be an object.")
        presentation = snapshot.get("presentation")
        requested_visible = presentation.get("visible") if isinstance(presentation, dict) else None
        if requested_visible is True and self._last_visible_request is not True and self._window is not None:
            try:
                self._window.restore()
                self._window.show()
            except Exception:
                pass
        if isinstance(requested_visible, bool):
            self._last_visible_request = requested_visible
        return {**snapshot, "revision": response_revision, "changed": True}

    def pick_open_file(self, options: object | None = None) -> dict[str, object]:
        return self._pick_file(False, options)

    def pick_save_file(self, options: object | None = None) -> dict[str, object]:
        return self._pick_file(True, options)

    def _pick_file(self, save: bool, options: object | None) -> dict[str, object]:
        if self._window is None or self._webview is None or (options is not None and not isinstance(options, dict)):
            return {"ok": False, "error": {"code": "dialog_error", "message": "File dialog options are invalid."}}
        supplied = options or {}
        title = supplied.get("title", "Select a file")
        directory = supplied.get("directory", "")
        filename = supplied.get("filename", "")
        file_types = supplied.get("file_types", ())
        if not isinstance(title, str) or len(title) > 120 or not isinstance(directory, str) or len(directory) > 1024 or not isinstance(filename, str) or len(filename) > 255:
            return {"ok": False, "error": {"code": "dialog_error", "message": "File dialog options are invalid."}}
        if not isinstance(file_types, list) or len(file_types) > 8 or not all(isinstance(value, str) and len(value) <= 120 for value in file_types):
            return {"ok": False, "error": {"code": "dialog_error", "message": "File type options are invalid."}}
        try:
            kind = self._webview.FileDialog.SAVE if save else self._webview.FileDialog.OPEN
            self._window.restore()
            self._window.show()
            result = self._window.create_file_dialog(
                kind,
                directory=directory,
                save_filename=filename if save else "",
                file_types=tuple(file_types),
            )
        except Exception:
            return {"ok": False, "error": {"code": "dialog_error", "message": "The native file dialog could not be opened."}}
        selected = result[0] if isinstance(result, (list, tuple)) and result else result
        return {"ok": True, "result": selected if isinstance(selected, str) else None}


def run(bootstrap: Bootstrap) -> int:
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        return 2
    try:
        connection = Client(
            bootstrap.pipe,
            family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX",
            authkey=bootstrap.authkey,
        )
    except (OSError, ValueError):
        return 3

    window_holder: list[Any] = []

    def close_window() -> None:
        if window_holder:
            try:
                window_holder[0].destroy()
            except Exception:
                pass

    bridge = PipeBridge(connection, close_window)
    api = WebApi(bridge)
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["REMOTE_DEBUGGING_PORT"] = None
    window = webview.create_window(
        "FenSoundSwitch Command Center",
        html=render_document(allow_native_bridge=sys.platform != "win32"),
        js_api=api,
        width=1456,
        height=760,
        min_size=(760, 560),
        text_select=True,
    )
    window_holder.append(window)
    api.attach_window(window, webview)

    native_navigation_handlers: list[object] = []
    native_window_icons: list[object] = []

    def require_webview2(renderer: str) -> bool:
        return renderer == "edgechromium"

    def install_navigation_guard(window: object) -> None:
        if native_navigation_handlers:
            return

        icon = apply_window_icon(window)
        if icon is not None:
            native_window_icons.append(icon)

        def reject_navigation(_sender: object, event: object) -> None:
            uri = str(getattr(event, "Uri", ""))
            if uri and not uri.startswith("about:blank"):
                event.Cancel = True

        native = getattr(getattr(window, "native", None), "webview", None)
        core = getattr(native, "CoreWebView2", None)
        if core is None:
            raise RuntimeError("WebView2 navigation guard is unavailable.")
        native_navigation_handlers.append(reject_navigation)
        core.NavigationStarting += reject_navigation

    if sys.platform == "win32":
        window.events.initialized += require_webview2
        window.events.before_load += install_navigation_guard

    def notify_parent(method: str) -> None:
        try:
            bridge.request(method, {})
        except ProtocolError:
            pass

    window.events.minimized += lambda: notify_parent("minimize")
    window.events.closing += lambda: notify_parent("close")
    bridge.start()
    try:
        webview.start(
            gui="edgechromium" if sys.platform == "win32" else "cocoa",
            debug=False,
            http_server=False,
            private_mode=True,
        )
    finally:
        bridge.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        bootstrap = parse_bootstrap(sys.argv[1:] if argv is None else argv)
    except BootstrapError:
        return 2
    return run(bootstrap)


if __name__ == "__main__":
    raise SystemExit(main())
