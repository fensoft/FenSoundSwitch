from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from plugin_api import (
    PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION,
    PluginHostContext,
    ShortcutAction,
    plugin_ui_document,
    plugin_ui_result,
)


PLUGIN_API_VERSION = HOST_PLUGIN_API_VERSION

_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2
_OP_PING = 3
_OP_PONG = 4
_HEADER = struct.Struct("<II")
_MAX_PAYLOAD_SIZE = 16 * 1024 * 1024
_PIPE_NAMES = tuple(rf"\\?\pipe\discord-ipc-{index}" for index in range(10))
_RPC_SCOPES = ("rpc", "rpc.voice.read", "rpc.voice.write")
_OAUTH_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_DEVELOPER_PORTAL_URL = "https://discord.com/developers/applications"
_DEFAULT_REDIRECT_URI = "https://127.0.0.1"
_CREDENTIAL_TARGET = "fensoundswitch/plugins/discord-output/oauth-rpc"
_LEGACY_CREDENTIAL_TARGET = "windows-ddc/plugins/discord-output/oauth-rpc"
_PROTOTYPE_CREDENTIAL_TARGET = "windows-ddc/test-discord/oauth-rpc"
_CREDENTIAL_VERSION = 1
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512


class _CredentialAttributeW(ctypes.Structure):
    _fields_ = (
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(wintypes.BYTE)),
    )


class _CredentialW(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CredentialAttributeW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    )


class DiscordRpcError(RuntimeError):
    pass


class DiscordRpcClient:
    def __init__(self, client_id: str, timeout: float = 5.0) -> None:
        self._client_id = client_id
        self._timeout = timeout
        self._pipe: BinaryIO | None = None

    def __enter__(self) -> DiscordRpcClient:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def connect(self) -> None:
        if sys.platform != "win32":
            raise DiscordRpcError("Discord local pipe access is Windows-only")
        for pipe_name in _PIPE_NAMES:
            try:
                self._pipe = open(pipe_name, "r+b", buffering=0)
                break
            except OSError:
                continue
        if self._pipe is None:
            raise DiscordRpcError("Discord is not running or no local RPC pipe is available")

        self._write_frame(_OP_HANDSHAKE, {"v": 1, "client_id": self._client_id})
        opcode, response = self._read_frame()
        if opcode == _OP_CLOSE:
            self.close()
            raise DiscordRpcError(_rpc_error_message(response, "Discord rejected the RPC handshake"))
        if opcode != _OP_FRAME or response.get("evt") != "READY":
            self.close()
            raise DiscordRpcError("Discord returned an unexpected RPC handshake response")

    def close(self) -> None:
        pipe = self._pipe
        self._pipe = None
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass

    def request(
        self,
        command: str,
        args: dict[str, object] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        nonce = str(uuid.uuid4())
        self._write_frame(_OP_FRAME, {"cmd": command, "args": args or {}, "nonce": nonce})
        previous_timeout = self._timeout
        if timeout is not None:
            self._timeout = timeout
        try:
            while True:
                opcode, response = self._read_frame()
                if opcode == _OP_PING:
                    self._write_frame(_OP_PONG, response)
                    continue
                if opcode == _OP_CLOSE:
                    raise DiscordRpcError(
                        _rpc_error_message(response, "Discord closed the RPC connection")
                    )
                if opcode != _OP_FRAME or response.get("nonce") != nonce:
                    continue
                if response.get("evt") == "ERROR":
                    raise DiscordRpcError(
                        _rpc_error_message(response, f"Discord rejected {command}")
                    )
                data = response.get("data")
                if not isinstance(data, dict):
                    raise DiscordRpcError(f"Discord returned no settings for {command}")
                return data
        finally:
            self._timeout = previous_timeout

    def _write_frame(self, opcode: int, payload: dict[str, object]) -> None:
        if self._pipe is None:
            raise DiscordRpcError("Discord RPC is not connected")
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self._pipe.write(_HEADER.pack(opcode, len(encoded)) + encoded)
        except OSError as exc:
            raise DiscordRpcError("Discord RPC connection was closed") from exc

    def _read_frame(self) -> tuple[int, dict[str, object]]:
        header = self._read_exact(_HEADER.size)
        opcode, payload_size = _HEADER.unpack(header)
        if payload_size > _MAX_PAYLOAD_SIZE:
            raise DiscordRpcError("Discord returned an oversized RPC payload")
        payload = self._read_exact(payload_size)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiscordRpcError("Discord returned an invalid RPC payload") from exc
        if not isinstance(decoded, dict):
            raise DiscordRpcError("Discord returned a non-object RPC payload")
        return opcode, decoded

    def _read_exact(self, size: int) -> bytes:
        deadline = time.monotonic() + self._timeout
        chunks = bytearray()
        while len(chunks) < size:
            pipe = self._pipe
            if pipe is None:
                raise DiscordRpcError("Discord RPC connection was closed")
            try:
                available = _pipe_bytes_available(pipe)
            except OSError as exc:
                raise DiscordRpcError("Discord RPC connection was closed") from exc
            if available:
                try:
                    chunk = pipe.read(min(size - len(chunks), available))
                except OSError as exc:
                    raise DiscordRpcError("Discord RPC connection was closed") from exc
                if not chunk:
                    raise DiscordRpcError("Discord closed the RPC connection")
                chunks.extend(chunk)
                continue
            if time.monotonic() >= deadline:
                raise DiscordRpcError("Timed out waiting for Discord RPC")
            time.sleep(0.01)
        return bytes(chunks)


def _pipe_bytes_available(pipe: BinaryIO) -> int:
    import msvcrt

    available = wintypes.DWORD()
    handle = msvcrt.get_osfhandle(pipe.fileno())
    if not ctypes.windll.kernel32.PeekNamedPipe(
        wintypes.HANDLE(handle), None, 0, None, ctypes.byref(available), None
    ):
        raise ctypes.WinError()
    return available.value


def _rpc_error_message(payload: dict[str, object], fallback: str) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        return fallback
    code = data.get("code")
    message = data.get("message")
    if isinstance(code, int) and isinstance(message, str):
        return f"{fallback}: {message} (code {code})"
    return fallback


def _output_settings(data: dict[str, object]) -> dict[str, object]:
    output = data.get("output")
    if not isinstance(output, dict):
        raise DiscordRpcError("Discord returned no output-device settings")
    return output


def _choose_alternative(output: dict[str, object]) -> tuple[str, str]:
    initial_id = output.get("device_id")
    devices = output.get("available_devices")
    if not isinstance(initial_id, str) or not isinstance(devices, list):
        raise DiscordRpcError("Discord returned malformed output-device settings")
    candidates: list[str] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = device.get("id")
        if isinstance(device_id, str) and device_id and device_id != initial_id:
            candidates.append(device_id)
    alternative_id = next((item for item in candidates if item != "default"), None)
    if alternative_id is None:
        raise DiscordRpcError("Discord reports no alternative concrete output device")
    return initial_id, alternative_id


def _validate_client_id(value: str) -> str:
    value = value.strip()
    if not value.isascii() or not value.isdecimal() or not 15 <= len(value) <= 22:
        raise DiscordRpcError("Discord Application ID must be a decimal snowflake")
    return value


def _credential_functions() -> tuple[Any, Any, Any, Any]:
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    cred_write = advapi32.CredWriteW
    cred_write.argtypes = (ctypes.POINTER(_CredentialW), wintypes.DWORD)
    cred_write.restype = wintypes.BOOL
    cred_read = advapi32.CredReadW
    cred_read.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CredentialW)),
    )
    cred_read.restype = wintypes.BOOL
    cred_delete = advapi32.CredDeleteW
    cred_delete.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    cred_delete.restype = wintypes.BOOL
    cred_free = advapi32.CredFree
    cred_free.argtypes = (wintypes.LPVOID,)
    cred_free.restype = None
    return cred_write, cred_read, cred_delete, cred_free


def _read_credential(target: str) -> dict[str, object] | None:
    _, cred_read, _, cred_free = _credential_functions()
    credential_pointer = ctypes.POINTER(_CredentialW)()
    if not cred_read(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)
    try:
        credential = credential_pointer.contents
        encoded = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
    finally:
        cred_free(credential_pointer)
    try:
        saved = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return saved if isinstance(saved, dict) else None


def _write_credential(target: str, saved: dict[str, object]) -> None:
    encoded = json.dumps(saved, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_CREDENTIAL_BLOB_SIZE:
        raise DiscordRpcError("The Discord OAuth credential is too large to save")
    blob = (wintypes.BYTE * len(encoded)).from_buffer_copy(encoded)
    credential = _CredentialW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "Discord OAuth RPC grant for FenSoundSwitch"
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(wintypes.BYTE))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = str(saved.get("client_id", "FenSoundSwitch"))
    cred_write, _, _, _ = _credential_functions()
    if not cred_write(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def _delete_credential(target: str) -> None:
    _, _, cred_delete, _ = _credential_functions()
    if cred_delete(target, _CRED_TYPE_GENERIC, 0):
        return
    error = ctypes.get_last_error()
    if error != _ERROR_NOT_FOUND:
        raise ctypes.WinError(error)


def _is_valid_saved_oauth(saved: object, *, require_tokens: bool = False) -> bool:
    if not isinstance(saved, dict) or saved.get("version") != _CREDENTIAL_VERSION:
        return False
    for field in ("client_id", "client_secret", "redirect_uri"):
        if not isinstance(saved.get(field), str) or not saved[field]:
            return False
    token_values = tuple(saved.get(field) for field in ("access_token", "refresh_token"))
    expires_at = saved.get("expires_at")
    has_tokens = all(isinstance(value, str) and bool(value) for value in token_values) and isinstance(
        expires_at, (int, float)
    ) and not isinstance(expires_at, bool)
    if require_tokens:
        return has_tokens
    has_any_token_field = any(value is not None for value in token_values) or expires_at is not None
    return has_tokens or not has_any_token_field


def _has_tokens(saved: dict[str, object]) -> bool:
    return _is_valid_saved_oauth(saved, require_tokens=True)


def _load_saved_oauth() -> dict[str, object] | None:
    saved = _read_credential(_CREDENTIAL_TARGET)
    if _is_valid_saved_oauth(saved):
        return saved
    for legacy_target in (_LEGACY_CREDENTIAL_TARGET, _PROTOTYPE_CREDENTIAL_TARGET):
        legacy = _read_credential(legacy_target)
        if _is_valid_saved_oauth(legacy, require_tokens=True):
            assert legacy is not None
            # Legacy grants are never deleted; the user can revoke them explicitly.
            _write_credential(_CREDENTIAL_TARGET, legacy)
            return legacy
    return None


def _save_oauth(saved: dict[str, object]) -> None:
    if not _is_valid_saved_oauth(saved):
        raise DiscordRpcError("Refusing to save an invalid Discord OAuth credential")
    _write_credential(_CREDENTIAL_TARGET, saved)


def _saved_client_configuration(
    client_id: str,
    client_secret: str,
    redirect_uri: str = _DEFAULT_REDIRECT_URI,
) -> dict[str, object]:
    return {
        "version": _CREDENTIAL_VERSION,
        "client_id": _validate_client_id(client_id),
        "client_secret": client_secret.strip(),
        "redirect_uri": redirect_uri,
    }


def _saved_oauth_from_token_response(
    saved_configuration: dict[str, object],
    token_response: dict[str, object],
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    access_token = token_response.get("access_token")
    refresh_token = token_response.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = previous.get("refresh_token") if previous is not None else None
    expires_in = token_response.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        raise DiscordRpcError("Discord returned no OAuth access token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise DiscordRpcError("Discord returned no OAuth refresh token")
    if not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool):
        raise DiscordRpcError("Discord returned no OAuth token lifetime")
    saved = dict(saved_configuration)
    saved.update(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + max(0.0, float(expires_in) - 30.0),
    )
    return saved


def _post_oauth_form(url: str, fields: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "fensoundswitch-discord-output/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            error_data = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_data = None
        message = error_data.get("error_description") if isinstance(error_data, dict) else None
        if not isinstance(message, str):
            message = error_data.get("message") if isinstance(error_data, dict) else None
        detail = f": {message}" if isinstance(message, str) else ""
        raise DiscordRpcError(f"Discord OAuth request failed with HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise DiscordRpcError("Could not reach Discord's OAuth service") from exc
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscordRpcError("Discord returned an invalid OAuth response") from exc
    if not isinstance(decoded, dict):
        raise DiscordRpcError("Discord returned a non-object OAuth response")
    return decoded


def _new_oauth_token_response(
    client: DiscordRpcClient,
    saved: dict[str, object],
) -> dict[str, object]:
    authorization = client.request(
        "AUTHORIZE",
        {"client_id": str(saved["client_id"]), "scopes": list(_RPC_SCOPES)},
        timeout=120.0,
    )
    authorization_code = authorization.get("code")
    if not isinstance(authorization_code, str) or not authorization_code:
        raise DiscordRpcError("Discord returned no RPC authorization code")
    return _post_oauth_form(
        _OAUTH_TOKEN_URL,
        {
            "client_id": str(saved["client_id"]),
            "client_secret": str(saved["client_secret"]),
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": str(saved["redirect_uri"]),
        },
    )


def _refresh_saved_oauth(saved: dict[str, object]) -> dict[str, object]:
    token_response = _post_oauth_form(
        _OAUTH_TOKEN_URL,
        {
            "client_id": str(saved["client_id"]),
            "client_secret": str(saved["client_secret"]),
            "grant_type": "refresh_token",
            "refresh_token": str(saved["refresh_token"]),
        },
    )
    return _saved_oauth_from_token_response(saved, token_response, previous=saved)


def _authenticate(client: DiscordRpcClient, access_token: str) -> None:
    authentication = client.request("AUTHENTICATE", {"access_token": access_token})
    scopes = authentication.get("scopes")
    if not isinstance(scopes, list):
        raise DiscordRpcError("Discord returned no OAuth scope list")
    missing_scopes = [scope for scope in _RPC_SCOPES if scope not in scopes]
    if missing_scopes:
        raise DiscordRpcError(
            "The OAuth grant is missing required Discord RPC voice scopes: "
            + ", ".join(missing_scopes)
        )


def _authenticate_saved_oauth(
    client: DiscordRpcClient,
    saved: dict[str, object],
) -> dict[str, object]:
    if not _has_tokens(saved):
        raise DiscordRpcError("Discord authorization has not completed")
    if float(saved["expires_at"]) > time.time():
        try:
            _authenticate(client, str(saved["access_token"]))
            return saved
        except DiscordRpcError:
            pass
    try:
        refreshed = _refresh_saved_oauth(saved)
        _authenticate(client, str(refreshed["access_token"]))
    except DiscordRpcError as exc:
        raise DiscordRpcError(
            "The saved Discord grant is unusable; reset authorization in Plugins"
        ) from exc
    _save_oauth(refreshed)
    return refreshed


def _authorize_new_oauth(
    client: DiscordRpcClient,
    saved_configuration: dict[str, object],
) -> dict[str, object]:
    token_response = _new_oauth_token_response(client, saved_configuration)
    saved = _saved_oauth_from_token_response(saved_configuration, token_response)
    _authenticate(client, str(saved["access_token"]))
    _save_oauth(saved)
    return saved


def _switch_authenticated_output(
    client: DiscordRpcClient,
    delay_seconds: float,
    stop_event: threading.Event,
) -> None:
    settings = client.request("GET_VOICE_SETTINGS")
    initial_id, alternative_id = _choose_alternative(_output_settings(settings))
    switched = False
    try:
        changed = client.request(
            "SET_VOICE_SETTINGS",
            {"output": {"device_id": alternative_id}},
        )
        if _output_settings(changed).get("device_id") != alternative_id:
            raise DiscordRpcError("Discord did not confirm the temporary output device")
        switched = True
        stop_event.wait(delay_seconds)
    finally:
        if switched:
            restored = client.request(
                "SET_VOICE_SETTINGS",
                {"output": {"device_id": initial_id}},
            )
            if _output_settings(restored).get("device_id") != initial_id:
                raise DiscordRpcError("Discord did not confirm restoration of the initial output")


class DiscordOutputPlugin:
    plugin_id = "discord-output"
    name = "Discord output switch"
    description = "Temporarily changes Discord's voice output for one second, then restores it."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._status = "Not initialized"
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._workers_lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        self._clients_lock = threading.Lock()
        self._clients: set[DiscordRpcClient] = set()
        self._shutdown = threading.Event()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        saved = _load_saved_oauth()
        if saved is None:
            self._set_status("Setup required")
            return
        self._set_status("Checking Discord authorization…")
        self._start_worker(self._validate_authorization, not _has_tokens(saved))

    def _set_status(self, status: str) -> None:
        with self._state_lock:
            self._status = status
        if self._host is not None:
            self._host.report_status(status)

    def _current_status(self) -> str:
        with self._state_lock:
            return self._status

    def _start_worker(self, target: Any, *args: object) -> bool:
        if self._shutdown.is_set():
            return False

        def run() -> None:
            try:
                target(*args)
            finally:
                with self._workers_lock:
                    self._workers.discard(threading.current_thread())

        worker = threading.Thread(
            target=run,
            name="discord-output-auth",
            daemon=True,
        )
        with self._workers_lock:
            self._workers.add(worker)
        worker.start()
        return True

    def _track_client(self, client: DiscordRpcClient) -> None:
        with self._clients_lock:
            self._clients.add(client)

    def _untrack_client(self, client: DiscordRpcClient) -> None:
        with self._clients_lock:
            self._clients.discard(client)

    def _validate_authorization(self, allow_authorize: bool = False) -> None:
        if not self._operation_lock.acquire(blocking=False):
            return
        client: DiscordRpcClient | None = None
        try:
            saved = _load_saved_oauth()
            if saved is None:
                raise DiscordRpcError("Discord setup is incomplete")
            client = DiscordRpcClient(str(saved["client_id"]))
            self._track_client(client)
            client.connect()
            if allow_authorize and not _has_tokens(saved):
                _authorize_new_oauth(client, saved)
            else:
                _authenticate_saved_oauth(client, saved)
            self._set_status("Ready")
        except (DiscordRpcError, OSError) as exc:
            self._set_status(f"Authorization failed: {str(exc).strip() or exc.__class__.__name__}")
        except Exception as exc:
            if self._host is not None:
                self._host.logger.error(
                    "Unexpected Discord authorization failure (%s).",
                    exc.__class__.__name__,
                )
            self._set_status(f"Authorization failed: {exc.__class__.__name__}")
        finally:
            if client is not None:
                client.close()
                self._untrack_client(client)
            self._operation_lock.release()

    def get_shortcut_actions(self) -> list[ShortcutAction]:
        return [ShortcutAction("switch-output", "Switch Discord output")]

    def trigger_shortcut(self, action_id: str) -> None:
        if action_id != "switch-output":
            raise ValueError(f"Unknown Discord shortcut action {action_id!r}.")
        if self._shutdown.is_set() or not self._operation_lock.acquire(blocking=False):
            return
        client: DiscordRpcClient | None = None
        try:
            saved = _load_saved_oauth()
            if saved is None or not _has_tokens(saved):
                raise DiscordRpcError("Discord authorization is not configured")
            self._set_status("Switching output…")
            client = DiscordRpcClient(str(saved["client_id"]))
            self._track_client(client)
            client.connect()
            _authenticate_saved_oauth(client, saved)
            _switch_authenticated_output(client, 1.0, self._shutdown)
            self._set_status("Ready")
        finally:
            if client is not None:
                client.close()
                self._untrack_client(client)
            self._operation_lock.release()

    def get_plugin_ui(self) -> dict[str, object]:
        return plugin_ui_document("Configure Discord output switch", [
            {"id": "client_secret", "type": "password", "label": "Client secret", "value": "", "required": True, "write_only": True, "description": "Reset the secret in Discord's Developer Portal before copying it here."},
            {"id": "client_id", "type": "text", "label": "Application ID", "value": "", "required": True},
        ], [
            {"id": "open_portal", "label": "Open Developer Portal", "kind": "action", "async": False},
            {"id": "setup", "label": "Save and authorize", "kind": "submit", "async": True},
            {"id": "reset", "label": "Reset authorization", "kind": "action", "async": True, "confirm": "Remove the saved Discord client secret and OAuth grant from Windows Credential Manager?"},
        ], f"OAuth status: {self._current_status()}. Add {_DEFAULT_REDIRECT_URI} exactly and request only rpc, rpc.voice.read, and rpc.voice.write. Discord's first consent prompt cannot be skipped.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "open_portal":
            client_id = values.get("client_id", "")
            url = _DEVELOPER_PORTAL_URL
            if isinstance(client_id, str) and client_id.strip():
                url = f"{url}/{_validate_client_id(client_id)}/oauth2"
            webbrowser.open(url, new=2)
            return plugin_ui_result("complete", message="Discord Developer Portal opened.")
        if action_id == "setup":
            secret = values.get("client_secret")
            client_id = values.get("client_id")
            if not isinstance(secret, str) or not secret.strip():
                raise DiscordRpcError("Client secret is required.")
            if not isinstance(client_id, str):
                raise DiscordRpcError("Discord Application ID must be text.")
            if not self._operation_lock.acquire(blocking=False):
                raise DiscordRpcError("Wait for the current Discord operation before reauthorizing")
            try:
                saved = _saved_client_configuration(client_id, secret)
                _save_oauth(saved)
            finally:
                self._operation_lock.release()
            self._set_status("Checking Discord authorization…")
            self._start_worker(self._validate_authorization, True)
            return plugin_ui_result("complete", message="Discord authorization started.")
        if action_id == "reset":
            if not self._operation_lock.acquire(blocking=False):
                raise DiscordRpcError("Wait for the current Discord operation before resetting")
            try:
                _delete_credential(_CREDENTIAL_TARGET)
                _delete_credential(_PROTOTYPE_CREDENTIAL_TARGET)
            finally:
                self._operation_lock.release()
            self._set_status("Setup required")
            return plugin_ui_result("complete", message="Discord authorization was reset.")
        raise ValueError(f"Unknown Discord UI action {action_id!r}.")

    def shutdown(self, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("Discord plugin shutdown timeout cannot be negative")
        self._shutdown.set()
        deadline = time.monotonic() + timeout
        operation_finished = self._operation_lock.acquire(
            timeout=max(0.0, deadline - time.monotonic())
        )
        if operation_finished:
            self._operation_lock.release()
        with self._clients_lock:
            clients = tuple(self._clients)
        if not operation_finished:
            # Closing the pipe is the final fallback after the restoration budget.
            for client in clients:
                client.close()
        with self._workers_lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is threading.current_thread():
                continue
            if worker.is_alive():
                worker.join(max(0.0, deadline - time.monotonic()))
        with self._workers_lock:
            return operation_finished and not any(worker.is_alive() for worker in self._workers)


def create_plugin() -> DiscordOutputPlugin:
    return DiscordOutputPlugin()
