from __future__ import annotations

import ctypes
import os
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


HC_ACTION = 0
WH_KEYBOARD_LL = 13
CS_DBLCLKS = 0x0008
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_SETTINGCHANGE = 0x001A
WM_SYSCOLORCHANGE = 0x0015
WM_COMMAND = 0x0111
WM_CONTEXTMENU = 0x007B
WM_DISPLAYCHANGE = 0x007E
WM_DEVICECHANGE = 0x0219
WM_THEMECHANGED = 0x031A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_TRAY_SHOW = WM_APP + 2
WM_TRAY_HIDE = WM_APP + 3
WM_TRAY_EXIT = WM_APP + 4
WM_DISPLAY_LISTENER_EXIT = WM_APP + 5
HWND_BROADCAST = 0xFFFF
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_VOLUME_MUTE = 0xAD
IDI_APPLICATION = 32512
IMAGE_ICON = 1
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NOTIFYICON_VERSION_4 = 4
LR_LOADFROMFILE = 0x0010
MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MONITORINFOF_PRIMARY = 0x00000001
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
HWND_TOPMOST = -1
DEFAULT_DISPLAY_SCALE_PERCENT = 100
USER_DEFAULT_SCREEN_DPI = 96
SPI_GETHIGHCONTRAST = 0x0042
HCF_HIGHCONTRASTON = 0x00000001
SM_CXSMICON = 49
SM_CYSMICON = 50
GA_ROOT = 2
DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001
DISPLAY_DEVICE_ACTIVE = 0x00000001
DICS_FLAG_GLOBAL = 0x00000001
DIREG_DEV = 0x00000001
KEY_READ = 0x00020019
REG_BINARY = 3
ERROR_ALREADY_EXISTS = 183
NATIVE_START_TIMEOUT_SECONDS = 2.0
NATIVE_STOP_TIMEOUT_SECONDS = 2.0
SINGLE_INSTANCE_MUTEX_NAME = r"Local\fensoundswitch-single-instance"
SINGLE_INSTANCE_RESTORE_MESSAGE_NAME = "fensoundswitch-restore-existing-instance"

BYTE = ctypes.c_ubyte
UINT_PTR = ctypes.c_size_t
HINSTANCE = wintypes.HANDLE
HICON = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
HBRUSH = wintypes.HANDLE
HMENU = wintypes.HANDLE
LPVOID = ctypes.c_void_p
LPCRECT = ctypes.POINTER(wintypes.RECT)
ULONG_PTR = wintypes.WPARAM
LRESULT = ctypes.c_ssize_t
HRESULT = ctypes.c_long
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", BYTE * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("handle", wintypes.HANDLE),
        ("description", wintypes.WCHAR * 128),
    ]


class HIGHCONTRASTW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwFlags", wintypes.DWORD),
        ("lpszDefaultScheme", wintypes.LPWSTR),
    ]


class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved", wintypes.DWORD),
        ("dbcc_classguid", GUID),
        ("dbcc_name", wintypes.WCHAR * 1),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", UINT_PTR),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", UINT_PTR),
    ]


@dataclass(frozen=True)
class WindowsMonitorIdentity:
    display_device_name: str
    device_path: str
    manufacturer_id: str | None = None
    product_code: int | None = None
    serial_number: str | None = None
    monitor_description: str | None = None


@dataclass(frozen=True)
class WindowsDdcMonitor:
    handle: int
    description: str
    identity: WindowsMonitorIdentity | None


@dataclass(frozen=True)
class ScreenRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


@dataclass(frozen=True)
class DisplayArea:
    display_device_name: str
    bounds: ScreenRect
    work_area: ScreenRect
    scale_percent: int = DEFAULT_DISPLAY_SCALE_PERCENT
    primary: bool = False


class PlatformError(RuntimeError):
    pass


class InstanceAlreadyRunningError(PlatformError):
    pass


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:
    shcore = None
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:
    dwmapi = None

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ctypes.c_ushort
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    HMENU,
    HINSTANCE,
    LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
user32.LoadIconW.argtypes = [HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = HICON
user32.LoadImageW.argtypes = [
    HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.DestroyIcon.argtypes = [HICON]
user32.DestroyIcon.restype = wintypes.BOOL
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = HMENU
user32.AppendMenuW.argtypes = [HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    LPCRECT,
]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    LPVOID,
    wintypes.UINT,
]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    MONITORENUMPROC,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.EnumDisplayDevicesW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(DISPLAY_DEVICEW),
    wintypes.DWORD,
]
user32.EnumDisplayDevicesW.restype = wintypes.BOOL
user32.RegisterDeviceNotificationW.argtypes = [wintypes.HANDLE, LPVOID, wintypes.DWORD]
user32.RegisterDeviceNotificationW.restype = wintypes.HANDLE
user32.UnregisterDeviceNotification.argtypes = [wintypes.HANDLE]
user32.UnregisterDeviceNotification.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(PHYSICAL_MONITOR),
]
dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
dxva2.DestroyPhysicalMonitor.restype = wintypes.BOOL
setupapi.SetupDiCreateDeviceInfoList.argtypes = [ctypes.POINTER(GUID), wintypes.HWND]
setupapi.SetupDiCreateDeviceInfoList.restype = wintypes.HANDLE
setupapi.SetupDiOpenDeviceInterfaceW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiOpenDeviceInterfaceW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiOpenDevRegKey.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVINFO_DATA),
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
]
setupapi.SetupDiOpenDevRegKey.restype = wintypes.HANDLE
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
advapi32.RegQueryValueExW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    LPVOID,
    ctypes.POINTER(wintypes.DWORD),
    LPVOID,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.RegQueryValueExW.restype = wintypes.LONG
advapi32.RegCloseKey.argtypes = [wintypes.HANDLE]
advapi32.RegCloseKey.restype = wintypes.LONG
if shcore is not None:
    shcore.GetScaleFactorForMonitor.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_int),
    ]
    shcore.GetScaleFactorForMonitor.restype = HRESULT
if dwmapi is not None:
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, LPVOID, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = HRESULT


def win_error(message: str) -> OSError:
    error_code = ctypes.get_last_error()
    if error_code:
        return ctypes.WinError(error_code)
    return OSError(message)


def make_int_resource(value: int) -> wintypes.LPCWSTR:
    return ctypes.cast(ctypes.c_void_p(value), wintypes.LPCWSTR)


class SingleInstanceGuard:
    def __init__(self, mutex_name: str = SINGLE_INSTANCE_MUTEX_NAME) -> None:
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            error = win_error("CreateMutexW failed")
            raise PlatformError(f"Failed to create the single-instance guard: {error}") from error

        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise InstanceAlreadyRunningError(
                "FenSoundSwitch is already running in this Windows session."
            )
        self._handle: wintypes.HANDLE | None = handle

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            kernel32.CloseHandle(handle)

    def __enter__(self) -> SingleInstanceGuard:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def request_existing_instance_restore() -> None:
    message = user32.RegisterWindowMessageW(SINGLE_INSTANCE_RESTORE_MESSAGE_NAME)
    if not message:
        raise PlatformError("Failed to register the existing-instance restore message.")
    if not user32.PostMessageW(HWND_BROADCAST, message, 0, 0):
        error = win_error("Failed to broadcast the existing-instance restore message")
        raise PlatformError(f"Failed to restore the existing FenSoundSwitch instance: {error}") from error


def _normalize_edid_serial(value: str) -> str | None:
    value = value.replace("\x00", "").strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", value)
    if not compact:
        return None
    if compact in {"0", "UNKNOWN", "NONE", "NA", "DEFAULT", "SERIAL", "SERIALNUMBER"}:
        return None
    if set(compact) <= {"0"} or set(compact) <= {"F"}:
        return None
    return value


def parse_edid_identity(edid: bytes) -> tuple[str | None, int | None, str | None]:
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None, None, None

    manufacturer_raw = int.from_bytes(edid[8:10], "big")
    manufacturer_letters = (
        (manufacturer_raw >> 10) & 0x1F,
        (manufacturer_raw >> 5) & 0x1F,
        manufacturer_raw & 0x1F,
    )
    if all(1 <= letter <= 26 for letter in manufacturer_letters):
        manufacturer_id = "".join(chr(ord("A") + letter - 1) for letter in manufacturer_letters)
    else:
        manufacturer_id = None

    raw_product_code = int.from_bytes(edid[10:12], "little")
    product_code = raw_product_code or None

    descriptor_serial = None
    for offset in (54, 72, 90, 108):
        descriptor = edid[offset : offset + 18]
        if len(descriptor) == 18 and descriptor[:3] == b"\x00\x00\x00" and descriptor[3] == 0xFF:
            descriptor_serial = _normalize_edid_serial(
                descriptor[5:18].decode("ascii", errors="ignore")
            )
            if descriptor_serial is not None:
                break

    numeric_serial = int.from_bytes(edid[12:16], "little")
    serial_number = descriptor_serial
    if serial_number is None and numeric_serial not in (0, 0xFFFFFFFF):
        serial_number = str(numeric_serial)

    return manufacturer_id, product_code, serial_number


def _read_monitor_edid(device_path: str) -> bytes | None:
    invalid_handle_value = ctypes.c_void_p(-1).value
    device_info_set = setupapi.SetupDiCreateDeviceInfoList(None, None)
    if not device_info_set or int(device_info_set) == invalid_handle_value:
        return None

    registry_key = None
    try:
        interface_data = SP_DEVICE_INTERFACE_DATA()
        interface_data.cbSize = ctypes.sizeof(interface_data)
        if not setupapi.SetupDiOpenDeviceInterfaceW(
            device_info_set,
            device_path,
            0,
            ctypes.byref(interface_data),
        ):
            return None

        required_size = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            device_info_set,
            ctypes.byref(interface_data),
            None,
            0,
            ctypes.byref(required_size),
            None,
        )
        if required_size.value == 0:
            return None

        detail_buffer = ctypes.create_string_buffer(required_size.value)
        detail_cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        ctypes.cast(detail_buffer, ctypes.POINTER(wintypes.DWORD)).contents.value = detail_cb_size
        device_info_data = SP_DEVINFO_DATA()
        device_info_data.cbSize = ctypes.sizeof(device_info_data)
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
            device_info_set,
            ctypes.byref(interface_data),
            detail_buffer,
            required_size,
            None,
            ctypes.byref(device_info_data),
        ):
            return None

        registry_key = setupapi.SetupDiOpenDevRegKey(
            device_info_set,
            ctypes.byref(device_info_data),
            DICS_FLAG_GLOBAL,
            0,
            DIREG_DEV,
            KEY_READ,
        )
        if not registry_key or int(registry_key) == invalid_handle_value:
            registry_key = None
            return None

        value_type = wintypes.DWORD()
        value_size = wintypes.DWORD()
        if advapi32.RegQueryValueExW(
            registry_key,
            "EDID",
            None,
            ctypes.byref(value_type),
            None,
            ctypes.byref(value_size),
        ) != 0:
            return None
        if value_type.value != REG_BINARY or value_size.value == 0:
            return None

        value_buffer = (ctypes.c_ubyte * value_size.value)()
        if advapi32.RegQueryValueExW(
            registry_key,
            "EDID",
            None,
            ctypes.byref(value_type),
            value_buffer,
            ctypes.byref(value_size),
        ) != 0:
            return None
        return bytes(value_buffer[: value_size.value])
    finally:
        if registry_key is not None:
            advapi32.RegCloseKey(registry_key)
        setupapi.SetupDiDestroyDeviceInfoList(device_info_set)


def _display_device_identity(
    display_device_name: str,
    display_device: DISPLAY_DEVICEW,
) -> WindowsMonitorIdentity | None:
    device_path = display_device.DeviceID.strip()
    if not device_path:
        return None
    edid = _read_monitor_edid(device_path)
    manufacturer_id, product_code, serial_number = parse_edid_identity(edid or b"")
    return WindowsMonitorIdentity(
        display_device_name=display_device_name,
        device_path=device_path,
        manufacturer_id=manufacturer_id,
        product_code=product_code,
        serial_number=serial_number,
        monitor_description=display_device.DeviceString.strip() or None,
    )


def _identity_slots_for_hmonitor(
    hmonitor: wintypes.HANDLE,
) -> list[WindowsMonitorIdentity | None]:
    physical_count = wintypes.DWORD()
    if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
        hmonitor,
        ctypes.byref(physical_count),
    ):
        raise win_error("GetNumberOfPhysicalMonitorsFromHMONITOR failed")

    count = physical_count.value
    monitor_info = MONITORINFOEXW()
    monitor_info.cbSize = ctypes.sizeof(monitor_info)
    if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info)):
        return [None] * count

    active_devices: list[DISPLAY_DEVICEW] = []
    device_index = 0
    while True:
        display_device = DISPLAY_DEVICEW()
        display_device.cb = ctypes.sizeof(display_device)
        if not user32.EnumDisplayDevicesW(
            monitor_info.szDevice,
            device_index,
            ctypes.byref(display_device),
            EDD_GET_DEVICE_INTERFACE_NAME,
        ):
            break
        if display_device.StateFlags & DISPLAY_DEVICE_ACTIVE and display_device.DeviceID.strip():
            active_devices.append(display_device)
        device_index += 1

    if count == 1 and len(active_devices) == 1:
        return [_display_device_identity(monitor_info.szDevice, active_devices[0])]
    return [None] * count


def enumerate_windows_monitor_identities() -> list[WindowsMonitorIdentity | None]:
    identity_slots: list[WindowsMonitorIdentity | None] = []
    callback_error: Exception | None = None

    def callback(
        hmonitor: wintypes.HANDLE,
        _hdc: wintypes.HDC,
        _rect: ctypes.POINTER(wintypes.RECT),
        _data: wintypes.LPARAM,
    ) -> bool:
        nonlocal callback_error
        try:
            identity_slots.extend(_identity_slots_for_hmonitor(hmonitor))
        except Exception as exc:
            callback_error = exc
            return False
        return True

    monitor_callback = MONITORENUMPROC(callback)
    if not user32.EnumDisplayMonitors(None, None, monitor_callback, 0):
        if callback_error is not None:
            raise callback_error
        raise win_error("EnumDisplayMonitors failed")
    if callback_error is not None:
        raise callback_error
    return identity_slots


def enumerate_windows_ddc_monitors() -> list[WindowsDdcMonitor]:
    """Acquire each DDC handle with identity from the same HMONITOR callback."""
    records: list[WindowsDdcMonitor] = []
    callback_error: Exception | None = None

    def callback(hmonitor, _hdc, _rect, _data) -> bool:
        nonlocal callback_error
        try:
            identities = _identity_slots_for_hmonitor(hmonitor)
            count = len(identities)
            physical_monitors = (PHYSICAL_MONITOR * count)()
            if count and not dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count, physical_monitors):
                raise win_error("GetPhysicalMonitorsFromHMONITOR failed")
            for physical, identity in zip(physical_monitors, identities):
                if physical.handle:
                    records.append(WindowsDdcMonitor(int(physical.handle), physical.description, identity))
        except Exception as exc:
            callback_error = exc
            return False
        return True

    monitor_callback = MONITORENUMPROC(callback)
    if not user32.EnumDisplayMonitors(None, None, monitor_callback, 0):
        if callback_error is None:
            callback_error = win_error("EnumDisplayMonitors failed")
    if callback_error is not None:
        for record in records:
            dxva2.DestroyPhysicalMonitor(record.handle)
        raise callback_error
    return records


def set_window_dark_mode(hwnd: int, enabled: bool) -> bool:
    if dwmapi is None or not hwnd:
        return False

    value = wintypes.BOOL(1 if enabled else 0)
    for attribute in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1):
        result = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            attribute,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        if result == 0:
            return True
    return False


def get_toplevel_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0

    top_level_hwnd = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
    if top_level_hwnd:
        return int(top_level_hwnd)
    return int(hwnd)


def get_window_dpi(hwnd: int) -> int:
    if not hwnd:
        return USER_DEFAULT_SCREEN_DPI
    dpi = int(user32.GetDpiForWindow(wintypes.HWND(hwnd)))
    if dpi <= 0:
        return USER_DEFAULT_SCREEN_DPI
    return dpi


def is_high_contrast_enabled() -> bool:
    high_contrast = HIGHCONTRASTW()
    high_contrast.cbSize = ctypes.sizeof(high_contrast)
    if not user32.SystemParametersInfoW(
        SPI_GETHIGHCONTRAST,
        ctypes.sizeof(high_contrast),
        ctypes.byref(high_contrast),
        0,
    ):
        return False
    return bool(high_contrast.dwFlags & HCF_HIGHCONTRASTON)


def _monitor_scale_percent(hmonitor: wintypes.HANDLE) -> int:
    if shcore is None:
        return DEFAULT_DISPLAY_SCALE_PERCENT

    scale_percent = ctypes.c_int(DEFAULT_DISPLAY_SCALE_PERCENT)
    result = shcore.GetScaleFactorForMonitor(hmonitor, ctypes.byref(scale_percent))
    if result != 0 or not 100 <= scale_percent.value <= 500:
        return DEFAULT_DISPLAY_SCALE_PERCENT
    return scale_percent.value


def enumerate_display_areas() -> list[DisplayArea]:
    display_areas: list[DisplayArea] = []
    callback_failed = False

    def callback(
        hmonitor: wintypes.HANDLE,
        _hdc: wintypes.HDC,
        _rect: ctypes.POINTER(wintypes.RECT),
        _data: wintypes.LPARAM,
    ) -> bool:
        nonlocal callback_failed
        try:
            monitor_info = MONITORINFOEXW()
            monitor_info.cbSize = ctypes.sizeof(monitor_info)
            if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info)):
                return True

            bounds = ScreenRect(
                monitor_info.rcMonitor.left,
                monitor_info.rcMonitor.top,
                monitor_info.rcMonitor.right,
                monitor_info.rcMonitor.bottom,
            )
            work_area = ScreenRect(
                monitor_info.rcWork.left,
                monitor_info.rcWork.top,
                monitor_info.rcWork.right,
                monitor_info.rcWork.bottom,
            )
            if bounds.width <= 0 or bounds.height <= 0:
                return True
            if work_area.width <= 0 or work_area.height <= 0:
                work_area = bounds
            display_areas.append(
                DisplayArea(
                    display_device_name=monitor_info.szDevice,
                    bounds=bounds,
                    work_area=work_area,
                    scale_percent=_monitor_scale_percent(hmonitor),
                    primary=bool(monitor_info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        except Exception:
            callback_failed = True
            return False
        return True

    monitor_callback = MONITORENUMPROC(callback)
    if not user32.EnumDisplayMonitors(None, None, monitor_callback, 0):
        return []
    if callback_failed:
        return []
    return display_areas


def select_display_area(
    display_areas: list[DisplayArea] | tuple[DisplayArea, ...],
    preferred_display_device_name: str | None,
    cursor_position: tuple[int, int] | None,
) -> DisplayArea | None:
    if not display_areas:
        return None

    if cursor_position is not None:
        cursor_x, cursor_y = cursor_position
        for display_area in display_areas:
            bounds = display_area.bounds
            if (
                bounds.left <= cursor_x < bounds.right
                and bounds.top <= cursor_y < bounds.bottom
            ):
                return display_area

    if preferred_display_device_name:
        preferred_name = preferred_display_device_name.casefold()
        for display_area in display_areas:
            if display_area.display_device_name.casefold() == preferred_name:
                return display_area

    for display_area in display_areas:
        if display_area.primary:
            return display_area
    return display_areas[0]


def get_overlay_display_area(
    preferred_display_device_name: str | None = None,
) -> DisplayArea | None:
    display_areas = enumerate_display_areas()
    if not display_areas:
        return None

    cursor_position = None
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        cursor_position = point.x, point.y

    return select_display_area(
        display_areas,
        preferred_display_device_name,
        cursor_position,
    )


def configure_no_activate_window(hwnd: int) -> bool:
    if not hwnd:
        return False

    native_hwnd = wintypes.HWND(hwnd)
    ctypes.set_last_error(0)
    current_style = user32.GetWindowLongW(native_hwnd, GWL_EXSTYLE)
    if current_style == 0 and ctypes.get_last_error() != 0:
        return False

    desired_style = current_style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if desired_style != current_style:
        ctypes.set_last_error(0)
        previous_style = user32.SetWindowLongW(native_hwnd, GWL_EXSTYLE, desired_style)
        if previous_style == 0 and ctypes.get_last_error() != 0:
            return False

    return bool(
        user32.SetWindowPos(
            native_hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOZORDER
            | SWP_NOACTIVATE
            | SWP_FRAMECHANGED,
        )
    )


def show_window_no_activate(
    hwnd: int,
    x: int,
    y: int,
    width: int,
    height: int,
) -> bool:
    if not hwnd or width <= 0 or height <= 0:
        return False
    return bool(
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST),
            x,
            y,
            width,
            height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
        )
    )


class DisplayChangeListener:
    START_TIMEOUT_SECONDS = NATIVE_START_TIMEOUT_SECONDS
    STOP_TIMEOUT_SECONDS = NATIVE_STOP_TIMEOUT_SECONDS

    def __init__(
        self,
        on_change: Callable[[], None],
        on_error: Callable[[Exception], None],
        on_theme_change: Callable[[], None] | None = None,
    ) -> None:
        self.on_change = on_change
        self.on_error = on_error
        self.on_theme_change = on_theme_change or (lambda: None)
        self._instance = kernel32.GetModuleHandleW(None)
        self._class_name = f"MonitorVolumeDisplayListener_{os.getpid()}_{id(self)}"
        self._wndproc = WNDPROC(self._window_proc)
        self._ready = threading.Event()
        self._active = threading.Event()
        self._stop_requested = threading.Event()
        self._thread = threading.Thread(
            target=self._message_loop,
            name="display-change-listener",
            daemon=True,
        )
        self._start_error: Exception | None = None
        self._hwnd: wintypes.HWND | None = None
        self._notification_handle: wintypes.HANDLE | None = None

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    def start(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("Display-listener start timeout must be positive.")
        self._thread.start()
        if not self._ready.wait(timeout):
            self._request_stop()
            raise PlatformError("Timed out while starting the display-change listener.")
        if self._start_error is not None:
            raise PlatformError(
                f"Failed to initialize display-change listener: {self._start_error}"
            ) from self._start_error
        if self._hwnd is None or not self._active.is_set():
            raise PlatformError("Failed to initialize display-change listener.")

    def _request_stop(self) -> None:
        self._stop_requested.set()
        self._active.clear()
        if self._hwnd is not None:
            user32.PostMessageW(self._hwnd, WM_DISPLAY_LISTENER_EXIT, 0, 0)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> bool:
        if timeout < 0:
            raise ValueError("Display-listener stop timeout cannot be negative.")
        self._request_stop()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _message_loop(self) -> None:
        class_registered = False
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance
        window_class.lpszClassName = self._class_name

        if not user32.RegisterClassW(ctypes.byref(window_class)):
            self._start_error = win_error("RegisterClassW failed")
            self._ready.set()
            return
        class_registered = True

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self._instance,
            None,
        )
        if not hwnd:
            self._start_error = win_error("CreateWindowExW failed")
            user32.UnregisterClassW(self._class_name, self._instance)
            self._ready.set()
            return

        self._hwnd = hwnd
        notification_filter = DEV_BROADCAST_DEVICEINTERFACE_W()
        notification_filter.dbcc_size = ctypes.sizeof(notification_filter)
        notification_filter.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
        notification_filter.dbcc_classguid = GUID(
            0xE6F07B5F,
            0xEE97,
            0x4A90,
            (BYTE * 8)(0xB0, 0x76, 0x33, 0xF5, 0x7B, 0xF4, 0xEA, 0xA7),
        )
        notification_handle = user32.RegisterDeviceNotificationW(
            hwnd,
            ctypes.byref(notification_filter),
            DEVICE_NOTIFY_WINDOW_HANDLE,
        )
        if not notification_handle:
            self._start_error = win_error("RegisterDeviceNotificationW failed")
            user32.DestroyWindow(hwnd)
            self._hwnd = None
            user32.UnregisterClassW(self._class_name, self._instance)
            self._ready.set()
            return

        self._notification_handle = notification_handle
        if not self._stop_requested.is_set():
            self._active.set()
        self._ready.set()

        message = wintypes.MSG()
        try:
            while not self._stop_requested.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise win_error("Display listener GetMessageW failed")
                if result == 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._active.clear()
            self.on_error(exc)
        finally:
            self._active.clear()
            if self._notification_handle is not None:
                user32.UnregisterDeviceNotification(self._notification_handle)
                self._notification_handle = None
            if self._hwnd is not None:
                user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if class_registered:
                user32.UnregisterClassW(self._class_name, self._instance)

    def _window_proc(self, hwnd: wintypes.HWND, msg: int, w_param: int, l_param: int) -> int:
        if msg == WM_DISPLAYCHANGE or (
            msg == WM_DEVICECHANGE
            and w_param in (DBT_DEVNODES_CHANGED, DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE)
        ):
            try:
                self.on_change()
            except Exception as exc:
                self.on_error(exc)
            return 0
        if msg in (WM_SETTINGCHANGE, WM_SYSCOLORCHANGE, WM_THEMECHANGED):
            try:
                self.on_theme_change()
            except Exception as exc:
                self.on_error(exc)
            return 0
        if msg == WM_DISPLAY_LISTENER_EXIT:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self._active.clear()
            self._hwnd = None
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)


class _TrayShowRequest:
    def __init__(self) -> None:
        self.completed = threading.Event()
        self.error: Exception | None = None


@dataclass(frozen=True)
class TrayMonitorMenuItem:
    label: str
    selection: object
    active: bool = False


@dataclass(frozen=True)
class TraySignalMenuItem:
    label: str
    signal_id: str


@dataclass(frozen=True)
class TrayMenuState:
    active_monitor: str | None = None
    current_volume: int | None = None
    routing_enabled: bool = False
    monitors: tuple[TrayMonitorMenuItem, ...] = ()
    signals: tuple[TraySignalMenuItem, ...] = ()


class TrayIconController:
    ICON_ID = 1
    MENU_RESTORE = 1001
    MENU_EXIT = 1002
    MENU_REFRESH = 1003
    MENU_MONITOR_BASE = 2000
    MENU_SIGNAL_BASE = 3000
    MAX_MONITOR_MENU_ITEMS = 100
    MAX_SIGNAL_MENU_ITEMS = 100
    SHOW_TIMEOUT_SECONDS = 2.0
    START_TIMEOUT_SECONDS = NATIVE_START_TIMEOUT_SECONDS
    STOP_TIMEOUT_SECONDS = NATIVE_STOP_TIMEOUT_SECONDS

    def __init__(
        self,
        tooltip: str,
        on_restore: Callable[[], None],
        on_exit: Callable[[], None],
        on_error: Callable[[Exception], None],
        icon_path: Path | None = None,
        on_refresh: Callable[[], None] | None = None,
        on_select_monitor: Callable[[object], None] | None = None,
        on_signal: Callable[[str], None] | None = None,
    ) -> None:
        self.tooltip = tooltip[:127]
        self.on_restore = on_restore
        self.on_exit = on_exit
        self.on_error = on_error
        self.on_refresh = on_refresh or (lambda: None)
        self.on_select_monitor = on_select_monitor or (lambda _selection: None)
        self.on_signal = on_signal or (lambda _signal_id: None)
        self._icon_path = str(icon_path) if icon_path is not None else None
        self._instance = kernel32.GetModuleHandleW(None)
        self._class_name = f"MonitorVolumeTrayWindow_{os.getpid()}_{id(self)}"
        self._wndproc = WNDPROC(self._window_proc)
        self._taskbar_created_message = user32.RegisterWindowMessageW("TaskbarCreated")
        if not self._taskbar_created_message:
            raise PlatformError("Failed to register the TaskbarCreated message.")
        self._restore_existing_instance_message = user32.RegisterWindowMessageW(
            SINGLE_INSTANCE_RESTORE_MESSAGE_NAME
        )
        if not self._restore_existing_instance_message:
            raise PlatformError("Failed to register the existing-instance restore message.")
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._thread = threading.Thread(target=self._message_loop, name="tray-icon", daemon=True)
        self._start_error: Exception | None = None
        self._hwnd: wintypes.HWND | None = None
        self._icon_visible = False
        self._icon_handle: HICON | None = None
        self._owns_icon_handle = False
        self._show_requests_lock = threading.Lock()
        self._show_requests: dict[int, _TrayShowRequest] = {}
        self._next_show_request_id = 1
        self._menu_state_lock = threading.Lock()
        self._menu_state = TrayMenuState()

    @property
    def is_visible(self) -> bool:
        return self._icon_visible

    def start(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("Tray start timeout must be positive.")
        self._thread.start()
        if not self._ready.wait(timeout):
            self._request_stop()
            raise PlatformError("Timed out while starting the tray controller.")
        if self._start_error is not None:
            raise PlatformError(f"Failed to initialize tray icon: {self._start_error}") from self._start_error
        if self._hwnd is None or self._stop_requested.is_set():
            raise PlatformError("Failed to initialize tray icon.")

    def show(self, timeout: float = SHOW_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("Tray show timeout must be positive.")

        hwnd = self._hwnd
        if hwnd is None:
            raise PlatformError("Tray controller is not running.")

        request = _TrayShowRequest()
        with self._show_requests_lock:
            request_id = self._next_show_request_id
            self._next_show_request_id += 1
            self._show_requests[request_id] = request

        if not user32.PostMessageW(hwnd, WM_TRAY_SHOW, request_id, 0):
            with self._show_requests_lock:
                self._show_requests.pop(request_id, None)
            error = win_error("Failed to request tray icon visibility")
            raise PlatformError(f"Failed to show tray icon: {error}") from error

        if not request.completed.wait(timeout):
            with self._show_requests_lock:
                self._show_requests.pop(request_id, None)
            raise PlatformError("Timed out waiting for Windows to show the tray icon.")

        with self._show_requests_lock:
            self._show_requests.pop(request_id, None)
        if request.error is not None:
            raise PlatformError(f"Failed to show tray icon: {request.error}") from request.error

    def hide(self) -> None:
        if self._hwnd is not None:
            user32.PostMessageW(self._hwnd, WM_TRAY_HIDE, 0, 0)

    def update_menu_state(self, state: TrayMenuState) -> None:
        with self._menu_state_lock:
            self._menu_state = state

    def _get_menu_state(self) -> TrayMenuState:
        with self._menu_state_lock:
            return self._menu_state

    def _request_stop(self) -> None:
        self._stop_requested.set()
        if self._hwnd is not None:
            user32.PostMessageW(self._hwnd, WM_TRAY_EXIT, 0, 0)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> bool:
        if timeout < 0:
            raise ValueError("Tray stop timeout cannot be negative.")
        self._request_stop()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _message_loop(self) -> None:
        class_registered = False
        window_class = WNDCLASSW()
        window_class.style = CS_DBLCLKS
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance
        window_class.lpszClassName = self._class_name
        self._icon_handle = self._load_icon_handle()
        window_class.hIcon = self._icon_handle

        if not user32.RegisterClassW(ctypes.byref(window_class)):
            self._start_error = win_error("RegisterClassW failed")
            self._release_icon_handle()
            self._ready.set()
            return
        class_registered = True

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self._instance,
            None,
        )
        if not hwnd:
            self._start_error = win_error("CreateWindowExW failed")
            if class_registered:
                user32.UnregisterClassW(self._class_name, self._instance)
            self._release_icon_handle()
            self._ready.set()
            return

        self._hwnd = hwnd
        self._ready.set()

        message = wintypes.MSG()
        while not self._stop_requested.is_set():
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                self.on_error(win_error("Tray GetMessageW failed"))
                break
            if result == 0:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

        if self._hwnd is not None:
            self._hide_icon(self._hwnd)
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        self._fail_show_requests(PlatformError("Tray controller stopped before showing the icon."))
        if class_registered:
            user32.UnregisterClassW(self._class_name, self._instance)
        self._release_icon_handle()

    def _window_proc(self, hwnd: wintypes.HWND, msg: int, w_param: int, l_param: int) -> int:
        if msg == self._restore_existing_instance_message:
            self.on_restore()
            return 0
        if msg == self._taskbar_created_message:
            should_restore_icon = self._icon_visible
            self._icon_visible = False
            if should_restore_icon:
                error = self._show_icon(hwnd)
                if error is not None:
                    self.on_error(error)
            return 0
        if msg == WM_TRAY_SHOW:
            error = self._show_icon(hwnd)
            self._complete_show_request(int(w_param), error)
            return 0
        if msg == WM_TRAY_HIDE:
            self._hide_icon(hwnd)
            return 0
        if msg == WM_TRAY_EXIT:
            self._hide_icon(hwnd)
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_COMMAND:
            command_id = w_param & 0xFFFF
            if self._dispatch_menu_command(command_id):
                return 0
        if msg == WM_TRAYICON:
            tray_event = l_param & 0xFFFF
            if tray_event == WM_LBUTTONDBLCLK:
                self.on_restore()
                return 0
            if tray_event in (WM_CONTEXTMENU, WM_RBUTTONUP):
                self._show_context_menu(hwnd)
                return 0
        if msg == WM_DESTROY:
            self._hide_icon(hwnd)
            self._hwnd = None
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

    def _make_notify_icon_data(self, hwnd: wintypes.HWND) -> NOTIFYICONDATAW:
        notify_data = NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(notify_data)
        notify_data.hWnd = hwnd
        notify_data.uID = self.ICON_ID
        notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        notify_data.uCallbackMessage = WM_TRAYICON
        notify_data.hIcon = self._icon_handle or user32.LoadIconW(None, make_int_resource(IDI_APPLICATION))
        notify_data.szTip = self.tooltip
        notify_data.uVersion = NOTIFYICON_VERSION_4
        return notify_data

    def _load_icon_handle(self) -> HICON:
        if self._icon_path:
            icon_handle = user32.LoadImageW(
                None,
                self._icon_path,
                IMAGE_ICON,
                user32.GetSystemMetrics(SM_CXSMICON),
                user32.GetSystemMetrics(SM_CYSMICON),
                LR_LOADFROMFILE,
            )
            if icon_handle:
                self._owns_icon_handle = True
                return ctypes.cast(icon_handle, HICON)

        self._owns_icon_handle = False
        return user32.LoadIconW(None, make_int_resource(IDI_APPLICATION))

    def _release_icon_handle(self) -> None:
        if self._icon_handle is not None and self._owns_icon_handle:
            user32.DestroyIcon(self._icon_handle)
        self._icon_handle = None
        self._owns_icon_handle = False

    def _complete_show_request(self, request_id: int, error: Exception | None) -> None:
        with self._show_requests_lock:
            request = self._show_requests.get(request_id)
            if request is None:
                return
            request.error = error
            request.completed.set()

    def _fail_show_requests(self, error: Exception) -> None:
        with self._show_requests_lock:
            for request in self._show_requests.values():
                request.error = error
                request.completed.set()

    def _show_icon(self, hwnd: wintypes.HWND) -> Exception | None:
        notify_data = self._make_notify_icon_data(hwnd)
        message = NIM_MODIFY if self._icon_visible else NIM_ADD
        if not shell32.Shell_NotifyIconW(message, ctypes.byref(notify_data)):
            return win_error("Shell_NotifyIconW failed")
        if message == NIM_ADD:
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(notify_data))
        self._icon_visible = True
        return None

    def _hide_icon(self, hwnd: wintypes.HWND) -> None:
        if not self._icon_visible:
            return
        notify_data = self._make_notify_icon_data(hwnd)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(notify_data))
        self._icon_visible = False

    def _show_context_menu(self, hwnd: wintypes.HWND) -> None:
        state = self._get_menu_state()
        monitor_items = state.monitors[: self.MAX_MONITOR_MENU_ITEMS]
        signal_items = state.signals[: self.MAX_SIGNAL_MENU_ITEMS]
        menu = user32.CreatePopupMenu()
        if not menu:
            self.on_error(win_error("CreatePopupMenu failed"))
            return

        try:
            active_monitor = state.active_monitor or "Not selected"
            current_volume = (
                "--" if state.current_volume is None else f"{state.current_volume}%"
            )
            routing_state = "Enabled" if state.routing_enabled else "Disabled"
            entries = (
                (MF_STRING | MF_GRAYED, 0, f"Active monitor: {active_monitor}"),
                (MF_STRING | MF_GRAYED, 0, f"Current volume: {current_volume}"),
                (MF_STRING | MF_GRAYED, 0, f"Routing: {routing_state}"),
                (MF_SEPARATOR, 0, None),
                (MF_STRING, self.MENU_REFRESH, "Refresh"),
                (MF_STRING | MF_GRAYED, 0, "Switch monitor"),
            )
            for flags, command_id, label in entries:
                if not self._append_menu_item(menu, flags, command_id, label):
                    return

            if monitor_items:
                for index, item in enumerate(monitor_items):
                    flags = MF_STRING | (MF_CHECKED if item.active else 0)
                    if not self._append_menu_item(
                        menu,
                        flags,
                        self.MENU_MONITOR_BASE + index,
                        item.label,
                    ):
                        return
            elif not self._append_menu_item(
                menu,
                MF_STRING | MF_GRAYED,
                0,
                "No selectable monitors",
            ):
                return

            if signal_items:
                if not self._append_menu_item(menu, MF_SEPARATOR, 0, None):
                    return
                if not self._append_menu_item(menu, MF_STRING | MF_GRAYED, 0, "Automations"):
                    return
                for index, item in enumerate(signal_items):
                    if not self._append_menu_item(
                        menu,
                        MF_STRING,
                        self.MENU_SIGNAL_BASE + index,
                        item.label,
                    ):
                        return

            for flags, command_id, label in (
                (MF_SEPARATOR, 0, None),
                (MF_STRING, self.MENU_RESTORE, "Restore"),
                (MF_STRING, self.MENU_EXIT, "Exit"),
            ):
                if not self._append_menu_item(menu, flags, command_id, label):
                    return

            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                self.on_error(win_error("GetCursorPos failed"))
                return

            user32.SetForegroundWindow(hwnd)
            command_id = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)

            self._dispatch_menu_command(command_id, monitor_items, signal_items)
        finally:
            user32.DestroyMenu(menu)

    def _append_menu_item(
        self,
        menu: HMENU,
        flags: int,
        command_id: int,
        label: str | None,
    ) -> bool:
        menu_label = None if label is None else self._format_menu_label(label)
        if user32.AppendMenuW(menu, flags, command_id, menu_label):
            return True
        self.on_error(win_error("AppendMenuW failed"))
        return False

    @staticmethod
    def _format_menu_label(label: str, limit: int = 96) -> str:
        normalized = " ".join(label.replace("&", "&&").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    def _dispatch_menu_command(
        self,
        command_id: int,
        monitor_items: tuple[TrayMonitorMenuItem, ...] | None = None,
        signal_items: tuple[TraySignalMenuItem, ...] | None = None,
    ) -> bool:
        if command_id == self.MENU_RESTORE:
            self.on_restore()
            return True
        if command_id == self.MENU_EXIT:
            self.on_exit()
            return True
        if command_id == self.MENU_REFRESH:
            self.on_refresh()
            return True

        items = monitor_items if monitor_items is not None else self._get_menu_state().monitors
        item_index = command_id - self.MENU_MONITOR_BASE
        if 0 <= item_index < min(len(items), self.MAX_MONITOR_MENU_ITEMS):
            self.on_select_monitor(items[item_index].selection)
            return True
        signals = signal_items if signal_items is not None else self._get_menu_state().signals
        signal_index = command_id - self.MENU_SIGNAL_BASE
        if 0 <= signal_index < min(len(signals), self.MAX_SIGNAL_MENU_ITEMS):
            self.on_signal(signals[signal_index].signal_id)
            return True
        return False


class GlobalVolumeKeyListener:
    START_TIMEOUT_SECONDS = NATIVE_START_TIMEOUT_SECONDS
    STOP_TIMEOUT_SECONDS = NATIVE_STOP_TIMEOUT_SECONDS

    def __init__(
        self,
        on_delta: Callable[[int], None],
        should_consume: Callable[[], bool],
        on_error: Callable[[Exception], None],
        step: int,
        on_unavailable: Callable[[], None] | None = None,
        should_report_unavailable: Callable[[], bool] | None = None,
        on_mute: Callable[[], None] | None = None,
        should_consume_mute: Callable[[], bool] | None = None,
    ) -> None:
        self.on_delta = on_delta
        self.should_consume = should_consume
        self.on_error = on_error
        self._step_lock = threading.Lock()
        self._step = 1
        self.set_step(step)
        self.on_unavailable = on_unavailable
        self.should_report_unavailable = should_report_unavailable or (lambda: False)
        self.on_mute = on_mute or (lambda: None)
        self.should_consume_mute = should_consume_mute or (lambda: False)
        self._hook_ready = threading.Event()
        self._hook_active = threading.Event()
        self._stop_event = threading.Event()
        self._hook_thread_id = 0
        self._hook_handle: wintypes.HANDLE | None = None
        self._start_error: Exception | None = None
        self._thread = threading.Thread(target=self._hook_loop, name="volume-key-hook", daemon=True)
        self._hook_callback = HOOKPROC(self._keyboard_proc)
        self._volume_key_consumption: dict[int, bool] = {}
        self._unavailable_notice_reported = threading.Event()

    @property
    def is_active(self) -> bool:
        return self._hook_active.is_set()

    def set_step(self, step: int) -> None:
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise ValueError("Volume step must be a positive integer.")
        with self._step_lock:
            self._step = step

    def _current_step(self) -> int:
        with self._step_lock:
            return self._step

    def start(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("Keyboard-hook start timeout must be positive.")
        self._thread.start()
        if not self._hook_ready.wait(timeout):
            self._request_stop()
            raise PlatformError("Timed out while starting the keyboard hook.")
        if self._start_error is not None:
            raise PlatformError(f"Failed to install keyboard hook: {self._start_error}") from self._start_error
        if self._hook_handle is None or not self._hook_active.is_set():
            raise PlatformError("Failed to install keyboard hook.")

    def _request_stop(self) -> None:
        self._stop_event.set()
        self._hook_active.clear()
        if self._hook_thread_id:
            user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> bool:
        if timeout < 0:
            raise ValueError("Keyboard-hook stop timeout cannot be negative.")
        self._request_stop()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def reset_unavailable_notice(self) -> None:
        self._unavailable_notice_reported.clear()

    def _hook_loop(self) -> None:
        self._hook_thread_id = kernel32.GetCurrentThreadId()
        module_handle = kernel32.GetModuleHandleW(None)
        hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_callback, module_handle, 0)
        if not hook_handle:
            self._start_error = win_error("SetWindowsHookExW failed")
            self._hook_ready.set()
            return

        self._hook_handle = hook_handle
        if not self._stop_event.is_set():
            self._hook_active.set()
        self._hook_ready.set()

        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise win_error("GetMessageW failed")
                if result == 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._hook_active.clear()
            if not self._stop_event.is_set():
                self.on_error(exc)
        finally:
            self._hook_active.clear()
            self._volume_key_consumption.clear()
            if self._hook_handle:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None

    def _resolve_volume_key_event(self, vk_code: int, message: int) -> tuple[bool, int | None]:
        if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if vk_code not in self._volume_key_consumption:
                self._volume_key_consumption[vk_code] = self.should_consume()

            consume = self._volume_key_consumption[vk_code]
            if not consume or not self.should_consume():
                return consume, None

            step = self._current_step()
            delta = step if vk_code == VK_VOLUME_UP else -step
            return True, delta

        if message in (WM_KEYUP, WM_SYSKEYUP):
            return self._volume_key_consumption.pop(vk_code, False), None

        return False, None

    def _resolve_mute_key_event(self, message: int) -> tuple[bool, bool]:
        if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            first_press = VK_VOLUME_MUTE not in self._volume_key_consumption
            if first_press:
                self._volume_key_consumption[VK_VOLUME_MUTE] = self.should_consume_mute()
            consume = self._volume_key_consumption[VK_VOLUME_MUTE]
            return consume, first_press and consume
        if message in (WM_KEYUP, WM_SYSKEYUP):
            return self._volume_key_consumption.pop(VK_VOLUME_MUTE, False), False
        return False, False

    def _report_unavailable_key_event(self, message: int, consume: bool) -> None:
        if (
            not consume
            and message in (WM_KEYDOWN, WM_SYSKEYDOWN)
            and self.on_unavailable is not None
            and self.should_report_unavailable()
            and not self._unavailable_notice_reported.is_set()
        ):
            self._unavailable_notice_reported.set()
            self.on_unavailable()

    def _keyboard_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code != HC_ACTION:
            return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

        key_info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if key_info.vkCode not in (VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP):
            return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

        if key_info.vkCode == VK_VOLUME_MUTE:
            consume, toggle = self._resolve_mute_key_event(w_param)
            if toggle:
                self.on_mute()
        else:
            consume, delta = self._resolve_volume_key_event(key_info.vkCode, w_param)
            if delta is not None:
                self.on_delta(delta)
            elif delta is None:
                self._report_unavailable_key_event(w_param, consume)

        if consume:
            return 1

        return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)
