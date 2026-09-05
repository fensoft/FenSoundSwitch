from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import os
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any, Callable, Collection, Mapping


PIPE_FAMILY = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
CHILD_MODE_ARGUMENT = "--fensoundswitch-presentation-child"
PIPE_ARGUMENT = "--pipe"
AUTHKEY_ENVIRONMENT_VARIABLE = "FENSOUNDSWITCH_PRESENTATION_AUTHKEY"
MAX_MESSAGE_BYTES = 64 * 1024
MAX_MESSAGE_DEPTH = 12
MAX_COLLECTION_ITEMS = 256
MAX_STRING_LENGTH = 16 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 2.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 2.0
_SOURCE_BOOTSTRAP = (
    "import runpy,sys;"
    "p=sys.argv[1];n=sys.argv[2];"
    "sys.argv=[p,'--pipe',n];"
    "runpy.run_path(p,run_name='__main__')"
)


class PresentationState(str, Enum):
    STOPPED = "stopped"
    LAUNCHING = "launching"
    LISTENING = "listening"
    CONNECTED = "connected"
    READY = "ready"
    CLOSING = "closing"
    EXITED = "exited"
    CRASHED = "crashed"
    FAILED = "failed"


class PresentationError(RuntimeError):
    pass


class InvalidPresentationMessage(PresentationError):
    pass


class UserActionError(ValueError):
    """A bounded validation message that is safe to return to the local UI."""

    pass


@dataclass(frozen=True)
class ChildCommand:
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PresentationSnapshot:
    revision: int
    value: Any


def read_presentation_authkey(
    environment: Mapping[str, str] | None = None,
) -> bytes:
    """Read and validate the inherited child authkey without logging its value."""
    encoded = (environment or os.environ).get(AUTHKEY_ENVIRONMENT_VARIABLE, "")
    if not encoded or len(encoded) > 86:
        raise ValueError("The presentation authkey environment value is invalid.")
    try:
        authkey = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("The presentation authkey environment value is invalid.") from exc
    if not 16 <= len(authkey) <= 64:
        raise ValueError("The presentation authkey environment value is invalid.")
    return authkey


def build_presentation_child_command(
    source_entrypoint: str | os.PathLike[str],
    pipe_name: str,
    authkey: bytes,
    *,
    executable: str | os.PathLike[str] | None = None,
    packaged: bool | None = None,
) -> ChildCommand:
    """Build a child command without exposing the authentication key in argv."""
    if pipe_name.startswith("\\\\.\\pipe\\"):
        valid_address = True
    else:
        candidate = Path(pipe_name)
        valid_address = candidate.is_absolute() and candidate.parent.name in {"fensoundswitch", "fss"}
    if not valid_address or not pipe_name.strip():
        raise ValueError("A private presentation IPC address is required.")
    if not isinstance(authkey, bytes) or len(authkey) < 16:
        raise ValueError("The presentation authkey must contain at least 16 bytes.")

    child_executable = os.fspath(executable or sys.executable)
    is_packaged = (
        bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
        if packaged is None
        else packaged
    )
    if is_packaged:
        argv = (child_executable, CHILD_MODE_ARGUMENT, PIPE_ARGUMENT, pipe_name)
    else:
        argv = (
            child_executable,
            "-c",
            _SOURCE_BOOTSTRAP,
            str(Path(source_entrypoint).resolve()),
            pipe_name,
        )
    return ChildCommand(
        argv=argv,
        environment={
            AUTHKEY_ENVIRONMENT_VARIABLE: base64.urlsafe_b64encode(authkey).decode("ascii").rstrip("=")
        },
    )


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_MESSAGE_DEPTH:
        raise InvalidPresentationMessage("Message nesting is too deep.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 2**63 - 1:
            raise InvalidPresentationMessage("Message integer is out of range.")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidPresentationMessage("Message number must be finite.")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise InvalidPresentationMessage("Message string is too long.")
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise InvalidPresentationMessage("Message list has too many items.")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise InvalidPresentationMessage("Message object has too many fields.")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_STRING_LENGTH:
                raise InvalidPresentationMessage("Message object keys must be bounded strings.")
            _validate_json_value(item, depth=depth + 1)
        return
    raise InvalidPresentationMessage("Message contains a non-JSON value.")


def validate_request(message: Any) -> tuple[str | int, str, dict[str, Any]]:
    _validate_json_value(message)
    if not isinstance(message, dict) or set(message) - {"id", "method", "params"}:
        raise InvalidPresentationMessage("Request fields are invalid.")
    request_id = message.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise InvalidPresentationMessage("Request id must be a string or integer.")
    if isinstance(request_id, str) and (not request_id or len(request_id) > 128):
        raise InvalidPresentationMessage("Request id is invalid.")
    method = message.get("method")
    if not isinstance(method, str) or not method or len(method) > 64:
        raise InvalidPresentationMessage("Request method is invalid.")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise InvalidPresentationMessage("Request params must be an object.")
    return request_id, method, params


class WebPresentationController:
    """Owns the authenticated named-pipe presentation child and its UI dispatch."""

    _METHODS = frozenset(
        {"ready", "get_snapshot", "dispatch_action", "show", "minimize", "close", "recover_tray"}
    )

    def __init__(
        self,
        source_entrypoint: str | os.PathLike[str],
        *,
        post_to_ui: Callable[[Callable[[], None]], None],
        get_snapshot: Callable[[], PresentationSnapshot | tuple[int, Any]],
        dispatch_action: Callable[[str, Mapping[str, Any]], Any],
        allowed_actions: Collection[str],
        on_exit: Callable[[], None],
        on_minimize: Callable[[], None],
        on_visibility: Callable[[bool], None],
        on_tray_recovery: Callable[[], None],
        on_child_failure: Callable[[str], None] | None = None,
        logger: logging.Logger | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        listener_factory: Callable[..., Any] | None = None,
        popen_factory: Callable[..., Any] | None = None,
        executable: str | os.PathLike[str] | None = None,
        packaged: bool | None = None,
    ) -> None:
        self._source_entrypoint = source_entrypoint
        self._post_to_ui = post_to_ui
        self._get_snapshot = get_snapshot
        self._dispatch_action = dispatch_action
        self._allowed_actions = frozenset(allowed_actions)
        self._on_exit = on_exit
        self._on_minimize = on_minimize
        self._on_visibility = on_visibility
        self._on_tray_recovery = on_tray_recovery
        self._on_child_failure = on_child_failure or (lambda _reason: None)
        self._logger = logger or logging.getLogger(__name__)
        self._request_timeout = max(0.01, request_timeout)
        self._shutdown_timeout = max(0.01, shutdown_timeout)
        self._listener_factory = listener_factory or Listener
        self._popen_factory = popen_factory or subprocess.Popen
        self._executable = executable
        self._packaged = packaged

        self._lock = threading.RLock()
        self._state = PresentationState.STOPPED
        self._intentional_exit = False
        self._listener: Any = None
        self._connection: Any = None
        self._process: Any = None
        self._reader_thread: threading.Thread | None = None
        self._process_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._failure_reported = False
        self._pipe_name: str | None = None

    @property
    def state(self) -> PresentationState:
        with self._lock:
            return self._state

    @property
    def pipe_name(self) -> str | None:
        with self._lock:
            return self._pipe_name

    def launch(self) -> bool:
        with self._lock:
            if self._state not in {
                PresentationState.STOPPED,
                PresentationState.EXITED,
                PresentationState.CRASHED,
                PresentationState.FAILED,
            }:
                return False
            self._state = PresentationState.LAUNCHING
            self._intentional_exit = False
            self._failure_reported = False
            self._stop_event = threading.Event()
            if sys.platform == "win32":
                self._pipe_name = rf"\\.\pipe\fensoundswitch-presentation-{os.getpid()}-{secrets.token_hex(16)}"
            else:
                # macOS limits AF_UNIX paths to roughly 104 bytes. Its per-user
                # temporary directory can exceed that limit, so keep this owned
                # endpoint under a deliberately short runtime root.
                runtime_directory = Path("/tmp/fss")
                runtime_directory.mkdir(mode=0o700, exist_ok=True)
                runtime_directory.chmod(0o700)
                self._pipe_name = str(runtime_directory / f"p-{os.getpid()}-{secrets.token_hex(8)}.sock")
                Path(self._pipe_name).unlink(missing_ok=True)
            authkey = secrets.token_bytes(32)

        listener = None
        process = None
        try:
            listener = self._listener_factory(
                address=self._pipe_name,
                family=PIPE_FAMILY,
                authkey=authkey,
            )
            command = build_presentation_child_command(
                self._source_entrypoint,
                self._pipe_name,
                authkey,
                executable=self._executable,
                packaged=self._packaged,
            )
            environment = os.environ.copy()
            environment.update(command.environment)
            process = self._popen_factory(
                list(command.argv),
                env=environment,
                close_fds=True,
            )
        except Exception as exc:
            if process is not None:
                self._stop_process(process, 0.05)
            if listener is not None:
                self._safe_close(listener)
            with self._lock:
                self._listener = None
                self._process = None
                self._state = PresentationState.FAILED
            self._logger.warning("Presentation child launch failed (%s).", exc.__class__.__name__)
            return False

        with self._lock:
            self._listener = listener
            self._process = process
            self._state = PresentationState.LISTENING
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="presentation-pipe-reader",
                daemon=True,
            )
            self._process_thread = threading.Thread(
                target=self._watch_process,
                args=(process,),
                name="presentation-child-watch",
                daemon=True,
            )
            self._reader_thread.start()
            self._process_thread.start()
        self._logger.info("Presentation child launched.")
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._state in {PresentationState.STOPPED, PresentationState.EXITED}:
                return
            self._intentional_exit = True
            self._state = PresentationState.CLOSING
            self._stop_event.set()
            connection, listener, process = self._connection, self._listener, self._process
            reader = self._reader_thread
            watcher = self._process_thread
        self._safe_close(connection)
        self._safe_close(listener)
        self._stop_process(process, self._shutdown_timeout)
        current = threading.current_thread()
        if reader is not None and reader is not current:
            reader.join(self._shutdown_timeout)
        if watcher is not None and watcher is not current:
            watcher.join(self._shutdown_timeout)
        with self._lock:
            self._connection = None
            self._listener = None
            self._process = None
            self._state = PresentationState.EXITED
        self._unlink_unix_socket()
        self._logger.info("Presentation child stopped.")

    def _reader_loop(self) -> None:
        try:
            listener = self._listener
            connection = listener.accept()
            with self._lock:
                if self._intentional_exit:
                    self._safe_close(connection)
                    return
                self._connection = connection
                self._state = PresentationState.CONNECTED
            while not self._stop_event.is_set():
                try:
                    payload = connection.recv_bytes(MAX_MESSAGE_BYTES)
                except (EOFError, OSError):
                    break
                response = self._handle_payload(payload)
                try:
                    connection.send_bytes(self._encode_response(response))
                except (EOFError, OSError):
                    break
        except (EOFError, OSError) as exc:
            if not self._stop_event.is_set():
                self._logger.warning("Presentation pipe failed (%s).", exc.__class__.__name__)
        except Exception as exc:
            if not self._stop_event.is_set():
                self._logger.warning("Presentation reader failed (%s).", exc.__class__.__name__)
        finally:
            with self._lock:
                connection = self._connection
                self._connection = None
                unexpected = not self._intentional_exit and not self._stop_event.is_set()
            self._safe_close(connection)
            if unexpected:
                self._report_failure("pipe-eof")

    def _watch_process(self, process: Any) -> None:
        try:
            returncode = process.wait()
        except Exception as exc:
            if not self._stop_event.is_set():
                self._logger.warning("Presentation child wait failed (%s).", exc.__class__.__name__)
            return
        with self._lock:
            unexpected = not self._intentional_exit and not self._stop_event.is_set()
            listener = self._listener
        if unexpected:
            self._report_failure(f"child-exit:{returncode}")
            self._safe_close(listener)

    def _handle_payload(self, payload: bytes) -> dict[str, Any]:
        request_id: str | int | None = None
        try:
            if len(payload) > MAX_MESSAGE_BYTES:
                raise InvalidPresentationMessage("Message exceeds the size limit.")
            message = json.loads(
                payload.decode("utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    InvalidPresentationMessage("Message number must be finite.")
                ),
            )
            request_id, method, params = validate_request(message)
            if method not in self._METHODS:
                raise InvalidPresentationMessage("Method is not allowed.")
            result = self._run_on_ui(method, params)
            _validate_json_value(result)
            return {"id": request_id, "ok": True, "result": result}
        except (InvalidPresentationMessage, UnicodeError, json.JSONDecodeError) as exc:
            return self._error_response(request_id, "invalid_request", str(exc))
        except TimeoutError:
            return self._error_response(request_id, "ui_timeout", "UI dispatch timed out.")
        except UserActionError as exc:
            return self._error_response(request_id, "action_invalid", str(exc))
        except ValueError as exc:
            # Route/plugin validators use bounded, user-facing ValueErrors.
            # Preserve the explanation instead of reducing it to a generic
            # transport failure in the WebKit command center.
            return self._error_response(request_id, "action_invalid", str(exc)[:1000])
        except Exception as exc:
            self._logger.warning("Presentation request failed (%s).", exc.__class__.__name__)
            return self._error_response(request_id, "request_failed", "Request failed.")

    def _run_on_ui(self, method: str, params: dict[str, Any]) -> Any:
        completed = threading.Event()
        cancelled = threading.Event()
        result: list[Any] = []
        failure: list[Exception] = []

        def invoke() -> None:
            if cancelled.is_set():
                return
            try:
                result.append(self._dispatch_on_ui(method, params))
            except Exception as exc:
                failure.append(exc)
            finally:
                completed.set()

        self._post_to_ui(invoke)
        if not completed.wait(self._request_timeout):
            cancelled.set()
            raise TimeoutError
        if failure:
            raise failure[0]
        return result[0] if result else None

    def _dispatch_on_ui(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ready":
            self._require_params(params, set())
            with self._lock:
                if self._state is not PresentationState.CONNECTED:
                    raise InvalidPresentationMessage("Child is not awaiting ready.")
            result = self._snapshot_result(None)
            with self._lock:
                self._state = PresentationState.READY
            self._on_visibility(True)
            return result

        with self._lock:
            if self._state is not PresentationState.READY:
                raise InvalidPresentationMessage("Child is not ready.")
        if method == "get_snapshot":
            self._require_params(params, {"revision"})
            revision = params.get("revision")
            if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
                raise InvalidPresentationMessage("Snapshot revision must be an integer.")
            return self._snapshot_result(revision)
        if method == "dispatch_action":
            self._require_params(params, {"action", "arguments"}, required={"action"})
            action = params["action"]
            arguments = params.get("arguments", {})
            if not isinstance(action, str) or action not in self._allowed_actions:
                raise InvalidPresentationMessage("Action is not allowed.")
            if not isinstance(arguments, dict):
                raise InvalidPresentationMessage("Action arguments must be an object.")
            return self._dispatch_action(action, arguments)
        if method == "show":
            self._require_params(params, set())
            self._on_visibility(True)
            return None
        if method == "minimize":
            self._require_params(params, set())
            self._on_minimize()
            return None
        if method == "recover_tray":
            self._require_params(params, set())
            self._on_tray_recovery()
            return None
        if method == "close":
            self._require_params(params, set())
            with self._lock:
                self._intentional_exit = True
                self._state = PresentationState.CLOSING
            self._on_visibility(False)
            self._on_exit()
            return None
        raise InvalidPresentationMessage("Method is not allowed.")

    def _snapshot_result(self, known_revision: int | None) -> dict[str, Any]:
        supplied = self._get_snapshot()
        if isinstance(supplied, PresentationSnapshot):
            snapshot = supplied
        elif isinstance(supplied, tuple) and len(supplied) == 2:
            snapshot = PresentationSnapshot(supplied[0], supplied[1])
        else:
            raise PresentationError("get_snapshot must return PresentationSnapshot or (revision, value).")
        if isinstance(snapshot.revision, bool) or not isinstance(snapshot.revision, int) or snapshot.revision < 0:
            raise PresentationError("Snapshot revision must be a non-negative integer.")
        _validate_json_value(snapshot.value)
        if known_revision == snapshot.revision:
            return {"revision": snapshot.revision, "changed": False}
        return {"revision": snapshot.revision, "changed": True, "snapshot": snapshot.value}

    @staticmethod
    def _require_params(
        params: Mapping[str, Any], allowed: set[str], *, required: set[str] | None = None
    ) -> None:
        if set(params) - allowed or (required or set()) - set(params):
            raise InvalidPresentationMessage("Method params are invalid.")

    def _report_failure(self, reason: str) -> None:
        with self._lock:
            if self._failure_reported or self._intentional_exit:
                return
            self._failure_reported = True
            self._state = PresentationState.CRASHED

        def recover() -> None:
            self._on_tray_recovery()
            self._on_child_failure(reason)

        self._post_to_ui(recover)

    @staticmethod
    def _encode_response(response: Mapping[str, Any]) -> bytes:
        _validate_json_value(dict(response))
        encoded = json.dumps(
            response, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            fallback = WebPresentationController._error_response(
                response.get("id"), "response_too_large", "Response exceeds the size limit."
            )
            return json.dumps(fallback, separators=(",", ":")).encode("utf-8")
        return encoded

    @staticmethod
    def _error_response(request_id: Any, code: str, message: str) -> dict[str, Any]:
        return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}

    def _unlink_unix_socket(self) -> None:
        if PIPE_FAMILY != "AF_UNIX" or not self._pipe_name:
            return
        try:
            Path(self._pipe_name).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _safe_close(resource: Any) -> None:
        if resource is None:
            return
        try:
            resource.close()
        except Exception:
            pass

    @staticmethod
    def _stop_process(process: Any, timeout: float) -> None:
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
        except Exception:
            pass


__all__ = [
    "AUTHKEY_ENVIRONMENT_VARIABLE",
    "CHILD_MODE_ARGUMENT",
    "ChildCommand",
    "InvalidPresentationMessage",
    "UserActionError",
    "MAX_MESSAGE_BYTES",
    "PIPE_ARGUMENT",
    "PIPE_FAMILY",
    "PresentationError",
    "PresentationSnapshot",
    "PresentationState",
    "WebPresentationController",
    "build_presentation_child_command",
    "read_presentation_authkey",
    "validate_request",
]
