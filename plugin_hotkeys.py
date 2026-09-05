from __future__ import annotations

import ctypes
import queue
import threading
import time
from dataclasses import dataclass
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
# FenSoundSwitch. Plugin shortcuts must instead run alongside the foreground app.
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


@dataclass(frozen=True)
class _RouteBinding:
    hotkey: HotkeySpec
    consume: bool


@dataclass(frozen=True)
class _ActionBinding:
    hotkey: HotkeySpec
    consume: bool


class PluginHotkeyController:
    """Observes action/route shortcuts and consumes only configured held key pairs."""

    def __init__(
        self,
        on_hotkey: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_route_key: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.on_hotkey = on_hotkey
        self.on_error = on_error
        self.on_route_key = on_route_key or (lambda _binding_id, _pressed: None)
        self._ready = threading.Event()
        self._active = threading.Event()
        self._stopping = threading.Event()
        self._thread_id = 0
        self._hook_handle: wintypes.HANDLE | None = None
        self._start_error: Exception | None = None
        self._binding_lock = threading.Lock()
        self._bindings_by_plugin: dict[str, _ActionBinding] = {}
        self._plugins_by_hotkey: dict[HotkeySpec, str] = {}
        self._route_bindings: dict[str, _RouteBinding] = {}
        self._routes_by_hotkey: dict[HotkeySpec, str] = {}
        self._held_route_keys: dict[int, tuple[str, HotkeySpec, bool]] = {}
        self._held_action_keys: dict[int, bool] = {}
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
        *,
        consume: bool = False,
        timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Plugin hotkey update timeout must be positive.")
        if not self._active.is_set() or self._stopping.is_set():
            raise HotkeyRegistrationError("Plugin hotkeys are not running.")
        if not isinstance(consume, bool):
            raise ValueError("Action shortcut consume setting must be true or false.")
        self._apply_binding(plugin_id, hotkey, consume)

    def set_route_binding(self, binding_id: str, hotkey: HotkeySpec | None, *, consume: bool = False) -> None:
        """Register a repeatable route key with an explicit consumption policy."""
        if not self._active.is_set() or self._stopping.is_set():
            raise HotkeyRegistrationError("Plugin hotkeys are not running.")
        if not isinstance(consume, bool):
            raise ValueError("Route keyboard consume setting must be true or false.")
        with self._binding_lock:
            old_binding = self._route_bindings.get(binding_id)
            if old_binding is not None and old_binding.hotkey == hotkey and old_binding.consume == consume:
                return
            if hotkey is not None:
                other = self._routes_by_hotkey.get(hotkey) or self._plugins_by_hotkey.get(hotkey)
                if other is not None and other != binding_id:
                    raise HotkeyConflictError(f"{hotkey.label} is already used by {other}.")
            if old_binding is not None:
                self._routes_by_hotkey.pop(old_binding.hotkey, None)
                self._route_bindings.pop(binding_id, None)
                self._release_route_binding_locked(binding_id)
            if hotkey is not None:
                self._route_bindings[binding_id] = _RouteBinding(hotkey, consume)
                self._routes_by_hotkey[hotkey] = binding_id

    def _release_route_binding_locked(self, binding_id: str) -> None:
        for virtual_key, (held_binding, _hotkey, _consume) in tuple(self._held_route_keys.items()):
            if held_binding == binding_id:
                del self._held_route_keys[virtual_key]
                self.on_route_key(binding_id, False)

    def _release_inactive_route_keys(self) -> None:
        with self._binding_lock:
            modifiers = self._current_modifiers()
            for virtual_key, (binding_id, hotkey, _consume) in tuple(self._held_route_keys.items()):
                if hotkey.modifiers != modifiers:
                    del self._held_route_keys[virtual_key]
                    self.on_route_key(binding_id, False)

    def _release_all_route_keys(self) -> None:
        with self._binding_lock:
            for virtual_key, (binding_id, _hotkey, _consume) in tuple(self._held_route_keys.items()):
                del self._held_route_keys[virtual_key]
                self.on_route_key(binding_id, False)

    def _apply_binding(self, plugin_id: str, hotkey: HotkeySpec | None, consume: bool = False) -> None:
        with self._binding_lock:
            old_hotkey = self._bindings_by_plugin.get(plugin_id)
            if old_hotkey is not None and old_hotkey.hotkey == hotkey and old_hotkey.consume == consume:
                return
            if hotkey is not None:
                other_plugin = self._plugins_by_hotkey.get(hotkey) or self._routes_by_hotkey.get(hotkey)
                if other_plugin is not None and other_plugin != plugin_id:
                    raise HotkeyConflictError(
                        f"{hotkey.label} is already used by plugin {other_plugin}."
                    )
            if old_hotkey is not None:
                self._plugins_by_hotkey.pop(old_hotkey.hotkey, None)
                del self._bindings_by_plugin[plugin_id]
            if hotkey is not None:
                self._bindings_by_plugin[plugin_id] = _ActionBinding(hotkey, consume)
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
            consume = False
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
                        route_binding = self._routes_by_hotkey.get(hotkey)
                        if route_binding is not None and self._active.is_set():
                            route = self._route_bindings[route_binding]
                            self._held_route_keys[virtual_key] = (route_binding, hotkey, route.consume)
                            consume = route.consume
                    if plugin_id is not None and self._active.is_set():
                        action = self._bindings_by_plugin.get(plugin_id)
                        if action is not None:
                            self._held_action_keys[virtual_key] = action.consume
                            consume = action.consume
                        self._dispatch_queue.put(plugin_id)
                    if route_binding is not None and self._active.is_set():
                        self.on_route_key(route_binding, True)
                else:
                    with self._binding_lock:
                        held = self._held_route_keys.get(virtual_key)
                        action_consume = self._held_action_keys.get(virtual_key, False)
                    consume = (held is not None and held[2]) or action_consume
                    if held is not None and self._active.is_set():
                        self.on_route_key(held[0], True)
            elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                if modifier_flag is not None:
                    self._held_modifier_keys.discard(virtual_key)
                    self._release_inactive_route_keys()
                else:
                    self._pressed_keys.discard(virtual_key)
                    with self._binding_lock:
                        held = self._held_route_keys.pop(virtual_key, None)
                        action_consume = self._held_action_keys.pop(virtual_key, False)
                    if held is not None:
                        self.on_route_key(held[0], False)
                        consume = held[2]
                    consume = consume or action_consume
        except Exception as exc:
            self._release_all_route_keys()
            # A hook/dispatch error must fail open for the current event too.
            consume = False
            if not self._stopping.is_set():
                try:
                    self.on_error(exc)
                except Exception:
                    pass
        if consume and self._active.is_set():
            return 1
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
            self._release_all_route_keys()
            with self._binding_lock:
                self._bindings_by_plugin.clear()
                self._plugins_by_hotkey.clear()
                self._route_bindings.clear()
                self._routes_by_hotkey.clear()
                self._held_action_keys.clear()
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
        self._release_all_route_keys()
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
