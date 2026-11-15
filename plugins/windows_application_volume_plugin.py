from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import io
import os
from collections import Counter
import threading
from typing import Mapping

import core_audio
from PIL import Image
from plugin_api import PLUGIN_API_VERSION, PluginHostContext, SlotAction, plugin_ui_document, plugin_ui_result


PARAMETER_KEYS = {"endpoint_id", "endpoint_name", "executable_path", "process_name", "display_name"}
SET_VOLUME_ACTION = "set-application-volume"


def _display_process_name(process_name: str) -> str:
    """Keep the executable identity internally, but omit its extension in UI text."""
    return process_name[:-4] if process_name.casefold().endswith(".exe") else process_name


def _executable_icon_data(executable_path: str) -> str | None:
    """Return the first Windows executable icon as a small PNG data URI."""
    if os.name != "nt" or not executable_path.lower().endswith(".exe"):
        return None
    icon = None
    info = None
    hdc = None
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", ctypes.wintypes.BOOL),
                ("xHotspot", ctypes.wintypes.DWORD),
                ("yHotspot", ctypes.wintypes.DWORD),
                ("hbmMask", ctypes.wintypes.HBITMAP),
                ("hbmColor", ctypes.wintypes.HBITMAP),
            ]

        class BITMAP(ctypes.Structure):
            _fields_ = [
                ("bmType", ctypes.c_long),
                ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", ctypes.c_ushort),
                ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p),
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD),
                ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.wintypes.DWORD * 3)]

        shell32.ExtractIconExW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(ctypes.wintypes.HICON),
            ctypes.POINTER(ctypes.wintypes.HICON),
            ctypes.wintypes.UINT,
        ]
        shell32.ExtractIconExW.restype = ctypes.wintypes.UINT
        user32.GetIconInfo.argtypes = [ctypes.wintypes.HICON, ctypes.POINTER(ICONINFO)]
        user32.GetIconInfo.restype = ctypes.wintypes.BOOL
        user32.DestroyIcon.argtypes = [ctypes.wintypes.HICON]
        user32.DestroyIcon.restype = ctypes.wintypes.BOOL
        user32.GetDC.argtypes = [ctypes.wintypes.HWND]
        user32.GetDC.restype = ctypes.wintypes.HDC
        user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.GetObjectW.argtypes = [ctypes.wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
        gdi32.GetObjectW.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [
            ctypes.wintypes.HDC,
            ctypes.wintypes.HBITMAP,
            ctypes.wintypes.UINT,
            ctypes.wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(BITMAPINFO),
            ctypes.wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [ctypes.wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = ctypes.wintypes.BOOL

        icons = (ctypes.wintypes.HICON * 1)()
        if shell32.ExtractIconExW(executable_path, 0, icons, None, 1) == 0 or not icons[0]:
            return None
        icon = icons[0]
        info = ICONINFO()
        if not user32.GetIconInfo(icon, ctypes.byref(info)) or not info.hbmColor:
            return None
        bitmap = BITMAP()
        if not gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
            return None
        width, height = max(1, bitmap.bmWidth), max(1, bitmap.bmHeight)
        pixels = (ctypes.c_ubyte * (width * height * 4))()
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bitmap_info = BITMAPINFO(header)
        hdc = user32.GetDC(None)
        if not hdc or gdi32.GetDIBits(hdc, info.hbmColor, 0, height, pixels, ctypes.byref(bitmap_info), 0) == 0:
            return None
        image = Image.frombuffer("RGBA", (width, height), bytes(pixels), "raw", "BGRA", 0, 1)
        if image.getchannel("A").getbbox() is None:
            image.putalpha(255)
        image.thumbnail((32, 32), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        return None
    finally:
        try:
            if icon:
                user32.DestroyIcon(icon)
            if hdc:
                user32.ReleaseDC(None, hdc)
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)
        except Exception:
            pass


def _slot_parameters(parameters: Mapping[str, object]) -> tuple[str, str, int]:
    if not isinstance(parameters, Mapping):
        raise ValueError("Automation step configuration is invalid.")
    if set(parameters) == {"session", "volume"}:
        session = parameters.get("session")
    elif "volume" in parameters and PARAMETER_KEYS.issubset(parameters):
        # Accept the pre-session-wrapper format used by older saved automations.
        session = {key: parameters[key] for key in PARAMETER_KEYS}
        if isinstance(parameters.get("icon"), str):
            session["icon"] = parameters["icon"]
    else:
        raise ValueError("Automation step configuration is invalid.")
    volume = parameters.get("volume")
    try:
        selected = validate_parameters(session)
    except ValueError as exc:
        raise ValueError("Automation step configuration is invalid.") from exc
    if not selected:
        raise ValueError("Automation step configuration is invalid.")
    if isinstance(volume, str):
        try: volume = int(volume)
        except ValueError as exc: raise ValueError("Automation step configuration is invalid.") from exc
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ValueError("Automation step configuration is invalid.")
    return selected["endpoint_id"], selected["executable_path"], volume


def validate_parameters(parameters: object) -> dict[str, str]:
    if not isinstance(parameters, dict) or set(parameters) - (PARAMETER_KEYS | {"icon"}) or not PARAMETER_KEYS.issubset(parameters):
        raise ValueError("Windows application output requires an endpoint, executable identity, process name, and display name.")
    values = {key: parameters[key] for key in PARAMETER_KEYS}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("Select an active Windows application audio session.")
    result = {key: str(value).strip() for key, value in values.items()}
    if isinstance(parameters.get("icon"), str) and parameters["icon"].startswith("data:image/png;base64,"):
        result["icon"] = parameters["icon"]
    return result


def _document(selected: object, options: list[dict[str, object]]) -> dict[str, object]:
    return plugin_ui_document(
        "Configure Windows application volume",
        [{"id": "session", "type": "select", "label": "Application audio session", "value": selected, "options": options, "required": True}],
        [
            {"id": "discover", "label": "Refresh", "kind": "action", "async": True, "auto": True},
            {"id": "save", "label": "Save", "kind": "submit", "async": False},
        ],
        "Select one active application session. The route matches its executable on this output, never a transient process ID.",
    )


class WindowsApplicationVolumePlugin:
    plugin_id = "windows-application-volume"
    name = "Windows application volume"
    description = "Controls one active Windows application's audio-session volume on a selected render output."
    provider_name = "Windows application volume"
    supports_native_mute = True

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._host: PluginHostContext | None = None
        self._parameters = parameters
        self._session_options: list[dict[str, object]] = []
        self._session_lock = threading.Lock()
        self._session_discovery_thread: threading.Thread | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        self._start_session_discovery()

    def _start_session_discovery(self) -> None:
        with self._session_lock:
            if self._session_discovery_thread is not None and self._session_discovery_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._discover_sessions,
                name="windows-application-volume-discovery",
                daemon=True,
            )
            self._session_discovery_thread = thread
        thread.start()

    def _discover_sessions(self) -> None:
        try:
            sessions = core_audio.enumerate_render_audio_sessions()
            identities = Counter((session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path)) for session in sessions)
            options = []
            for session in sessions:
                identity = (session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path))
                if identities[identity] != 1:
                    continue
                parameters = {
                    "endpoint_id": session.endpoint_id,
                    "endpoint_name": session.endpoint_name,
                    "executable_path": session.executable_path,
                    "process_name": session.process_name,
                    "display_name": session.display_name,
                }
                icon = _executable_icon_data(session.executable_path)
                if icon is not None:
                    parameters["icon"] = icon
                options.append({"label": f"{session.display_name} ({_display_process_name(session.process_name)}) - {session.endpoint_name}", "value": parameters})
            with self._session_lock:
                self._session_options = options
        except Exception:
            with self._session_lock:
                self._session_options = []
        finally:
            with self._session_lock:
                self._session_discovery_thread = None

    def get_slot_actions(self) -> list[SlotAction]:
        return [SlotAction(SET_VOLUME_ACTION, "Windows application volume", "Controls one active Windows application's audio-session volume on a selected render output.")]

    def get_slot_ui(self, action_id: str, parameters: Mapping[str, object]) -> dict[str, object]:
        if action_id != SET_VOLUME_ACTION:
            raise ValueError(f"Unknown Windows application volume action {action_id!r}.")
        with self._session_lock:
            discovery_running = self._session_discovery_thread is not None and self._session_discovery_thread.is_alive()
        if discovery_running:
            document = plugin_ui_document(
                "Configure Windows application volume",
                [],
                [],
                "Discovering monitor inputs. Please wait...",
            )
            document["state"] = "loading"
            return document
        selected = parameters.get("session")
        try:
            selected = validate_parameters(selected)
        except ValueError:
            selected = None
        with self._session_lock:
            options = list(self._session_options)
        if isinstance(selected, dict):
            matching = next((option.get("value") for option in options if isinstance(option, dict) and isinstance(option.get("value"), dict) and option["value"].get("endpoint_id", "").casefold() == selected.get("endpoint_id", "").casefold() and option["value"].get("executable_path", "").casefold() == selected.get("executable_path", "").casefold()), None)
            if isinstance(matching, dict):
                selected = matching
        if isinstance(selected, dict):
            if not any(option.get("value") == selected for option in options if isinstance(option, dict)):
                options.append({"label": f"{selected['display_name']} ({_display_process_name(selected['process_name'])}) - {selected['endpoint_name']}", "value": selected})
        return plugin_ui_document("Configure Windows application volume", [
            {"id": "session", "type": "select", "label": "Application audio session", "value": selected, "options": options, "icon": selected.get("icon") if isinstance(selected, dict) else None, "required": True},
            {"id": "volume", "type": "integer", "label": "Windows application volume", "value": parameters.get("volume", 50), "minimum": 0, "maximum": 100, "required": True},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Select one active application session. The route matches its executable on this output, never a transient process ID.")

    def refresh_slot_ui(self, action_id: str, parameters: Mapping[str, object]) -> None:
        if action_id != SET_VOLUME_ACTION:
            raise ValueError(f"Unknown Windows application volume action {action_id!r}.")
        self._start_session_discovery()

    def invoke_slot_ui_action(self, action_id: str, ui_action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != SET_VOLUME_ACTION:
            raise ValueError("Unknown Windows application volume step configuration action.")
        if ui_action_id != "save":
            raise ValueError("Unknown Windows application volume step configuration action.")
        _slot_parameters(values)
        return plugin_ui_result("save", values={"session": values.get("session"), "volume": int(values.get("volume", 0))})

    def slot_summary(self, action_id: str, parameters: Mapping[str, object]) -> str:
        try:
            selected = validate_parameters(parameters.get("session"))
            _slot_parameters(parameters)
            return selected["display_name"]
        except ValueError:
            return "Not configured"

    def slot_icon(self, action_id: str, parameters: Mapping[str, object]) -> str | None:
        if action_id != SET_VOLUME_ACTION:
            return None
        try:
            selected = validate_parameters(parameters.get("session"))
            return selected.get("icon") or _executable_icon_data(selected["executable_path"])
        except ValueError:
            return None

    def run_slot(self, action_id: str, parameters: Mapping[str, object]) -> int:
        if action_id != SET_VOLUME_ACTION:
            raise ValueError(f"Unknown Windows application volume action {action_id!r}.")
        endpoint_id, executable_path, volume = _slot_parameters(parameters)
        try:
            confirmed = core_audio.write_application_session_volume(endpoint_id, executable_path, volume)
        except core_audio.CoreAudioError:
            # The application may have moved to another output since discovery.
            # Recover only when there is exactly one current session for this executable.
            expected = core_audio.normalize_application_executable_path(executable_path)
            sessions = core_audio.enumerate_render_audio_sessions()
            matches = [
                session for session in sessions
                if core_audio.normalize_application_executable_path(session.executable_path) == expected
            ]
            if len(matches) != 1:
                raise
            confirmed = core_audio.write_application_session_volume(matches[0].endpoint_id, executable_path, volume)
        if confirmed != volume:
            raise RuntimeError("The application volume did not confirm the requested value.")
        return confirmed

    def create_output(self, parameters: object) -> "WindowsApplicationVolumePlugin":
        return WindowsApplicationVolumePlugin(validate_parameters(parameters))

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> dict[str, object]:
        try:
            selected: object = validate_parameters(dict(parameters))
        except ValueError:
            selected = None
        options = [{"label": str(selected["display_name"]), "value": selected}] if isinstance(selected, dict) else []
        return _document(selected, options)

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id == "discover":
            sessions = core_audio.enumerate_render_audio_sessions()
            identities = Counter((session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path)) for session in sessions)
            options = []
            for session in sessions:
                identity = (session.endpoint_id.casefold(), core_audio.normalize_application_executable_path(session.executable_path))
                if identities[identity] != 1:
                    continue
                parameters = {
                    "endpoint_id": session.endpoint_id,
                    "endpoint_name": session.endpoint_name,
                    "executable_path": session.executable_path,
                    "process_name": session.process_name,
                    "display_name": session.display_name,
                }
                options.append({"label": f"{session.display_name} ({_display_process_name(session.process_name)}) - {session.endpoint_name}", "value": parameters})
            return plugin_ui_result(
                "update",
                document=_document(values.get("session"), options),
                message=f"{len(options)} unambiguous active application audio session(s) found.",
            )
        if action_id == "save":
            return plugin_ui_result("save", values=validate_parameters(values.get("session")))
        raise ValueError(f"Unknown Windows application volume UI action {action_id!r}.")

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        try:
            values = validate_parameters(parameters)
            return f"Selected application: {values['display_name']} ({_display_process_name(values['process_name'])})"
        except ValueError:
            return "No Windows application audio session selected for this route."

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        try:
            validate_parameters(self._parameters)
        except ValueError as exc:
            return False, str(exc)
        return True, None

    def read_volume(self) -> int:
        values = validate_parameters(self._parameters)
        return core_audio.read_application_session_volume(values["endpoint_id"], values["executable_path"])

    def write_volume(self, target_volume: int) -> int:
        values = validate_parameters(self._parameters)
        return core_audio.write_application_session_volume(values["endpoint_id"], values["executable_path"], max(0, min(100, int(target_volume))))

    def toggle_mute(self) -> bool:
        values = validate_parameters(self._parameters)
        return core_audio.toggle_application_session_mute(values["endpoint_id"], values["executable_path"])

    def activate_volume_provider(self) -> None:
        return None

    def deactivate_volume_provider(self) -> None:
        return None

    def on_volume_topology_changed(self) -> None:
        return None

    def get_shortcut_actions(self) -> list[object]:
        return []

    def trigger_shortcut(self, action_id: str) -> None:
        raise ValueError("Windows application volume output has no actions.")

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> WindowsApplicationVolumePlugin:
    return WindowsApplicationVolumePlugin()
