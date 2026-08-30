from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable

from plugin_api import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotkeySpec
from windows_platform import (
    HC_ACTION,
    HOOKPROC,
    KBDLLHOOKSTRUCT,
    PlatformError,
    WH_KEYBOARD_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_QUIT,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    kernel32,
    user32,
    win_error,
)


START_TIMEOUT_SECONDS = 2.0
STOP_TIMEOUT_SECONDS = 2.0

# A passive low-level hook is deliberate: RegisterHotKey reserves the input for
# windows-ddc. Plugin shortcuts must instead run alongside the foreground app.
_MODIFIER_FLAGS = {
    0x10: MOD_SHIFT,
    0xA0: MOD_SHIFT,
    0xA1: MOD_SHIFT,
    0x11: MOD_CONTROL,
    0xA2: MOD_CONTROL,
    0xA3: MOD_CONTROL,
    0x12: MOD_ALT,
    0xA4: MOD_ALT,
    0xA5: MOD_ALT,
    0x5B: MOD_WIN,
    0x5C: MOD_WIN,
}


class HotkeyRegistrationError(PlatformError):
    pass


class HotkeyConflictError(HotkeyRegistrationError):
    pass


class PluginHotkeyController:
    """Observes plugin shortcuts without consuming foreground keyboard input."""

    def __init__(
        self,
        on_hotkey: Callable[[str], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.on_hotkey = on_hotkey
        self.on_error = on_error
        self._ready = threading.Event()
        self._active = threading.Event()
        self._stopping = threading.Event()
        self._thread_id = 0
        self._hook_handle: wintypes.HANDLE | None = None
        self._start_error: Exception | None = None
        self._binding_lock = threading.Lock()
        self._bindings_by_plugin: dict[str, HotkeySpec] = {}
        self._plugins_by_hotkey: dict[HotkeySpec, str] = {}
        self._held_modifier_keys: set[int] = set()
        self._pressed_keys: set[int] = set()
        self._dispatch_queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._hook_loop,
            name="plugin-hotkey-loop",
            daemon=True,
        )
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="plugin-hotkey-dispatch",
            daemon=True,
        )
        # Keep the ctypes callback strongly referenced for the hook lifetime.
        self._hook_callback = HOOKPROC(self._keyboard_proc)

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    def start(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        if timeout <= 0:
            raise ValueError("Plugin hotkey startup timeout must be positive.")
        deadline = time.monotonic() + timeout
        self._dispatch_thread.start()
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop(max(0.0, deadline - time.monotonic()))
            raise PlatformError("Timed out while starting plugin hotkeys.")
        if self._start_error is not None:
            self.stop(max(0.0, deadline - time.monotonic()))
            raise PlatformError(f"Failed to start plugin hotkeys: {self._start_error}") from self._start_error
        if self._hook_handle is None or not self._active.is_set():
            self.stop(max(0.0, deadline - time.monotonic()))
            raise PlatformError("Failed to start plugin hotkeys.")

    def set_binding(
        self,
        plugin_id: str,
        hotkey: HotkeySpec | None,
        timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Plugin hotkey update timeout must be positive.")
        if not self._active.is_set() or self._stopping.is_set():
            raise HotkeyRegistrationError("Plugin hotkeys are not running.")
        self._apply_binding(plugin_id, hotkey)

    def _apply_binding(self, plugin_id: str, hotkey: HotkeySpec | None) -> None:
        with self._binding_lock:
            old_hotkey = self._bindings_by_plugin.get(plugin_id)
            if old_hotkey == hotkey:
                return
            if hotkey is not None:
                other_plugin = self._plugins_by_hotkey.get(hotkey)
                if other_plugin is not None and other_plugin != plugin_id:
                    raise HotkeyConflictError(
                        f"{hotkey.label} is already used by plugin {other_plugin}."
                    )
            if old_hotkey is not None:
                self._plugins_by_hotkey.pop(old_hotkey, None)
                del self._bindings_by_plugin[plugin_id]
            if hotkey is not None:
                self._bindings_by_plugin[plugin_id] = hotkey
                self._plugins_by_hotkey[hotkey] = plugin_id

    def _current_modifiers(self) -> int:
        flags = 0
        for virtual_key in self._held_modifier_keys:
            flags |= _MODIFIER_FLAGS[virtual_key]
        return flags

    def _keyboard_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code != HC_ACTION:
            return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)
        try:
            key_info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            virtual_key = int(key_info.vkCode)
            modifier_flag = _MODIFIER_FLAGS.get(virtual_key)
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if modifier_flag is not None:
                    self._held_modifier_keys.add(virtual_key)
                elif virtual_key not in self._pressed_keys:
                    self._pressed_keys.add(virtual_key)
                    hotkey = HotkeySpec(self._current_modifiers(), virtual_key)
                    with self._binding_lock:
                        plugin_id = self._plugins_by_hotkey.get(hotkey)
                    if plugin_id is not None and self._active.is_set():
                        self._dispatch_queue.put(plugin_id)
            elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                if modifier_flag is not None:
                    self._held_modifier_keys.discard(virtual_key)
                else:
                    self._pressed_keys.discard(virtual_key)
        except Exception as exc:
            if not self._stopping.is_set():
                try:
                    self.on_error(exc)
                except Exception:
                    pass
        # This observer must never eat the foreground application's input.
        return user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

    def _hook_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        module_handle = kernel32.GetModuleHandleW(None)
        try:
            hook_handle = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hook_callback, module_handle, 0
            )
            if not hook_handle:
                raise win_error("SetWindowsHookExW failed for plugin hotkeys")
            self._hook_handle = hook_handle
            if not self._stopping.is_set():
                self._active.set()
            self._ready.set()
            message = wintypes.MSG()
            while not self._stopping.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise win_error("Plugin hotkey GetMessageW failed")
                if result == 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
            elif not self._stopping.is_set():
                try:
                    self.on_error(exc)
                except Exception:
                    pass
        finally:
            self._active.clear()
            with self._binding_lock:
                self._bindings_by_plugin.clear()
                self._plugins_by_hotkey.clear()
            self._held_modifier_keys.clear()
            self._pressed_keys.clear()
            if self._hook_handle is not None:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None
            self._dispatch_queue.put(None)

    def _dispatch_loop(self) -> None:
        while True:
            plugin_id = self._dispatch_queue.get()
            if plugin_id is None:
                return
            try:
                self.on_hotkey(plugin_id)
            except Exception as exc:
                try:
                    self.on_error(exc)
                except Exception:
                    pass

    def _request_stop(self) -> None:
        self._stopping.set()
        self._active.clear()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        else:
            self._dispatch_queue.put(None)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> bool:
        if timeout < 0:
            raise ValueError("Plugin hotkey stop timeout cannot be negative.")
        deadline = time.monotonic() + timeout
        self._request_stop()
        if self._thread.is_alive():
            self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._dispatch_thread.is_alive():
            self._dispatch_queue.put(None)
            self._dispatch_thread.join(max(0.0, deadline - time.monotonic()))
        return not self._thread.is_alive() and not self._dispatch_thread.is_alive()
