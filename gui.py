from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, TypeAlias

from audio_outputs import (
    AudioOutputMatchError,
    AudioOutputResult,
    AudioOutputTopologyChanged,
    reconcile_monitor_audio_outputs,
)
from autostart import (
    AutostartCommandError,
    AutostartUnavailableError,
    is_start_with_windows_enabled,
    set_start_with_windows,
)
from ddc import (
    MonitorRef,
    SavedMonitorSelection,
    SelectionMatch,
    SelectionMatchStatus,
    clamp,
    enumerate_monitors,
    match_selected_monitor,
    read_monitor_volume,
    set_monitor_volume,
)
from diagnostics import get_logger
from overlay import VolumeOverlay
from settings import (
    DEFAULT_CHANGE_SPEED,
    load_change_speed,
    load_selected_monitor_key,
    save_change_speed,
    save_selected_monitor_key,
)
from theme import (
    apply_app_icon,
    apply_color_scheme,
    apply_theme,
    apply_window_chrome,
    get_tk_window_dpi,
    read_windows_theme_state,
)
from windows_platform import (
    DisplayChangeListener,
    GlobalVolumeKeyListener,
    TrayIconController,
    TrayMenuState,
    TrayMonitorMenuItem,
)


RefreshResult: TypeAlias = tuple[list[MonitorRef], SelectionMatch, int | None, Exception | None]
WriteResult: TypeAlias = tuple[list[MonitorRef], int, int, SavedMonitorSelection]
LOGGER = get_logger(__name__)


class MonitorSelectionUnavailable(RuntimeError):
    def __init__(self, message: str, monitors: list[MonitorRef] | None = None) -> None:
        super().__init__(message)
        self.monitors = monitors


class DisplayTopologyChanged(RuntimeError):
    pass


class MonitorVolumeApp:
    CHANGE_SPEED_STEPS = {
        "slow": 1,
        "medium": 2,
        "fast": 3,
    }
    TRAY_TOOLTIP = "windows-ddc"
    DISPLAY_CHANGE_DEBOUNCE_MS = 500
    DDC_OPERATION_TIMEOUT_MS = 10_000
    REFRESH_RETRY_DELAYS_MS = (1000, 2000, 4000)
    THEME_CHANGE_DEBOUNCE_MS = 100
    BASE_WINDOW_MIN_WIDTH = 620
    DECREASE_VOLUME_LABEL = "Decrease volume"
    INCREASE_VOLUME_LABEL = "Increase volume"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Monitor Volume")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self.on_window_unmap)

        self.theme_state = read_windows_theme_state()
        self.dark_mode = self.theme_state.dark_mode
        self.high_contrast = self.theme_state.high_contrast
        self.style = ttk.Style(self.root)
        self.active_theme = apply_theme(
            self.style,
            self.dark_mode,
            self.high_contrast,
        )
        self._ui_dpi = get_tk_window_dpi(self.root)

        self.monitors: list[MonitorRef] = []
        self.preferred_selected_key = load_selected_monitor_key()
        self.selected_key: SavedMonitorSelection | None = None
        self.current_volume: int | None = None
        self.target_volume: int | None = None
        self.change_speed = load_change_speed()
        self.volume_step = self.CHANGE_SPEED_STEPS[self.change_speed]
        try:
            self.start_with_windows = is_start_with_windows_enabled()
        except (OSError, AutostartUnavailableError) as exc:
            self.start_with_windows = False
            LOGGER.warning(
                "Reading the Start with Windows setting failed (%s).",
                exc.__class__.__name__,
            )
        self.app_icon_path: Path | None = None
        self._busy = False
        self._closing = False
        self._ignore_scale_events = False
        self._result_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._hotkey_delta_queue: queue.Queue[int] = queue.Queue()
        self._hotkeys_ready = False
        self._hotkeys_enabled = False
        self._listener: GlobalVolumeKeyListener | None = None
        self._display_listener: DisplayChangeListener | None = None
        self._plugin_manager: Any | None = None
        self._overlay: VolumeOverlay | None = None
        self._volume_write_inflight = False
        self._pending_target_volume: int | None = None
        self._tray_icon: TrayIconController | None = None
        self._in_tray = False
        self._poll_after_id: str | None = None
        self._refresh_after_id: str | None = None
        self._ddc_timeout_after_id: str | None = None
        self._theme_after_id: str | None = None
        self._scale_after_id: str | None = None
        self._audio_output_sync_inflight = False
        self._pending_audio_output_sync: tuple[
            int,
            tuple[str, ...],
            str,
        ] | None = None
        self._audio_rename_attempted_ids: set[str] = set()
        self._ddc_operation_sequence = 0
        self._active_ddc_operation_id: int | None = None
        self._active_ddc_operation_kind: str | None = None
        self._ddc_operation_timed_out = False
        self._refresh_retry_index = 0
        self._refresh_requested = False
        self._refresh_requested_automatic = False
        self._topology_generation = 0
        self._topology_generation_lock = threading.Lock()
        self._topology_valid = threading.Event()
        self._control_unavailable_reason: str | None = "Monitor selection is not ready."

        self.monitor_var = tk.StringVar()
        self.provider_var = tk.StringVar(value="Volume provider: not selected")
        self.volume_var = tk.DoubleVar(value=0.0)
        self.volume_text_var = tk.StringVar(value="--")
        self.change_speed_var = tk.StringVar(value=self.change_speed.title())
        self.start_with_windows_var = tk.BooleanVar(value=self.start_with_windows)
        self.status_var = tk.StringVar(value="Searching for monitors...")

        self.app_icon_path = apply_app_icon(self.root)
        self._build_widgets()
        self._overlay = VolumeOverlay(
            self.root,
            dark_mode=self.dark_mode,
            high_contrast=self.high_contrast,
        )
        apply_color_scheme(
            self.root,
            self.status_bar,
            self.dark_mode,
            self.high_contrast,
        )
        self._lock_window_size()
        apply_window_chrome(self.root, self.dark_mode)
        self._bind_keyboard_shortcuts()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._apply_control_state()
        self._start_plugins()
        self._start_display_listener()
        self._start_tray_icon()
        self._start_minimized()
        self._start_keyboard_listener()
        self._poll_after_id = self.root.after(50, self._poll_queues)
        self._refresh_after_id = self.root.after(
            50,
            lambda: self._run_scheduled_refresh(automatic=False),
        )

    def _build_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.content_frame = ttk.Frame(self.root, padding=self._scaled_px(12))
        self.content_frame.grid(row=0, column=0, sticky="nsew")
        self.content_frame.columnconfigure(1, weight=1)
        self.content_frame.columnconfigure(2, weight=1)

        self.monitor_label = ttk.Label(self.content_frame, textvariable=self.provider_var)
        self.monitor_label.grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.monitor_combo = ttk.Combobox(
            self.content_frame,
            textvariable=self.monitor_var,
            state="readonly",
            width=44,
            takefocus=True,
        )
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_selected)

        self.refresh_button = ttk.Button(
            self.content_frame,
            text="↻",
            width=3,
            takefocus=True,
            command=self.refresh_monitors,
        )
        self.refresh_button.grid(
            row=1,
            column=3,
            sticky="e",
            padx=(self._scaled_px(8), 0),
            pady=(self._scaled_px(10), 0),
        )

        self.volume_label = ttk.Label(self.content_frame, text="Volume:", underline=0)
        self.volume_label.grid(
            row=1,
            column=0,
            sticky="w",
            pady=(self._scaled_px(10), 0),
        )

        self.change_speed_label = ttk.Label(
            self.content_frame,
            text="Change speed:",
            underline=0,
        )
        self.change_speed_label.grid(
            row=2,
            column=0,
            sticky="w",
            pady=(self._scaled_px(12), 0),
        )

        self.change_speed_combo = ttk.Combobox(
            self.content_frame,
            textvariable=self.change_speed_var,
            values=tuple(speed.title() for speed in self.CHANGE_SPEED_STEPS),
            state="readonly",
            width=8,
            takefocus=True,
        )
        self.change_speed_combo.grid(
            row=2,
            column=1,
            sticky="w",
            padx=(self._scaled_px(8), 0),
            pady=(self._scaled_px(12), 0),
        )
        self.change_speed_combo.bind("<<ComboboxSelected>>", self.on_change_speed_selected)

        self.volume_value_label = ttk.Label(
            self.content_frame,
            textvariable=self.volume_text_var,
            width=5,
            anchor="e",
        )
        self.volume_value_label.grid(
            row=1,
            column=2,
            sticky="e",
            pady=(self._scaled_px(10), 0),
        )

        self.decrease_button = ttk.Button(
            self.content_frame,
            text=self.DECREASE_VOLUME_LABEL,
            underline=0,
            takefocus=True,
            command=lambda: self.adjust_selected_volume(-self.volume_step),
        )

        self.volume_scale = ttk.Scale(
            self.content_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.volume_var,
            command=self.on_scale_moved,
            length=self._scaled_px(260),
            takefocus=True,
        )
        self.volume_scale.grid(
            row=1,
            column=1,
            columnspan=1,
            sticky="ew",
            padx=self._scaled_px(8),
            pady=(self._scaled_px(10), 0),
        )
        self.volume_scale.bind("<ButtonRelease-1>", self.on_scale_released)
        for sequence in (
            "<KeyRelease-Left>",
            "<KeyRelease-Right>",
            "<KeyRelease-Up>",
            "<KeyRelease-Down>",
        ):
            self.volume_scale.bind(sequence, self.on_scale_released)
        self.volume_scale.bind("<Home>", lambda _event: self._set_volume_from_keyboard(0))
        self.volume_scale.bind("<End>", lambda _event: self._set_volume_from_keyboard(100))
        self.volume_scale.bind("<Prior>", lambda _event: self._adjust_volume_from_keyboard(10))
        self.volume_scale.bind("<Next>", lambda _event: self._adjust_volume_from_keyboard(-10))

        self.increase_button = ttk.Button(
            self.content_frame,
            text=self.INCREASE_VOLUME_LABEL,
            underline=0,
            takefocus=True,
            command=lambda: self.adjust_selected_volume(self.volume_step),
        )

        self.start_with_windows_check = ttk.Checkbutton(
            self.content_frame,
            text="Start with Windows",
            underline=0,
            variable=self.start_with_windows_var,
            takefocus=True,
            command=self.on_start_with_windows_toggled,
        )

        self.configure_plugins_button = ttk.Button(
            self.content_frame,
            text="Configure",
            underline=-1,
            takefocus=True,
            command=self.configure_plugins,
        )
        self.configure_plugins_button.grid(
            row=0,
            column=3,
            sticky="e",
            pady=0,
        )

        self.status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            relief=tk.SUNKEN,
            bd=1,
            padx=self._scaled_px(6),
        )
        self.status_bar.grid(row=1, column=0, sticky="ew")

    def _scaled_px(self, value: int) -> int:
        return max(1, round(value * self._ui_dpi / 96))

    def _apply_scaled_layout(self) -> None:
        self.content_frame.configure(padding=self._scaled_px(12))
        self.refresh_button.grid_configure(
            padx=(self._scaled_px(8), 0),
            pady=(self._scaled_px(10), 0),
        )
        self.volume_label.grid_configure(pady=(self._scaled_px(10), 0))
        self.change_speed_label.grid_configure(pady=(self._scaled_px(12), 0))
        self.change_speed_combo.grid_configure(
            padx=(self._scaled_px(8), 0),
            pady=(self._scaled_px(12), 0),
        )
        self.volume_value_label.grid_configure(pady=(self._scaled_px(10), 0))
        self.volume_scale.configure(length=self._scaled_px(260))
        self.volume_scale.grid_configure(
            padx=self._scaled_px(8),
            pady=(self._scaled_px(10), 0),
        )
        self.status_bar.configure(padx=self._scaled_px(6))

    def _lock_window_size(self) -> None:
        self._apply_scaled_layout()
        self._resize_for_content(force=True)
        self.root.resizable(True, False)

    def _resize_for_content(self, force: bool = False) -> None:
        self.root.update_idletasks()
        width = max(self.root.winfo_reqwidth(), self._scaled_px(self.BASE_WINDOW_MIN_WIDTH))
        height = self.root.winfo_reqheight()
        self.root.minsize(width, height)
        current_width = self.root.winfo_width()
        current_height = self.root.winfo_height()
        if force or current_width < width or current_height != height:
            self.root.geometry(f"{width if force else max(current_width, width)}x{height}")

    def _on_root_configure(self, event: Any) -> None:
        if self._closing or event.widget is not self.root or self._scale_after_id is not None:
            return
        self._scale_after_id = self.root.after_idle(self._refresh_ui_scaling)

    def _refresh_ui_scaling(self) -> None:
        self._scale_after_id = None
        if self._closing:
            return
        dpi = get_tk_window_dpi(self.root)
        if dpi == self._ui_dpi:
            return
        self._ui_dpi = dpi
        self._apply_scaled_layout()
        self._resize_for_content()

    def _bind_keyboard_shortcuts(self) -> None:
        bindings = {
            "<Alt-v>": lambda _event: self._focus_control(self.volume_scale),
            "<Alt-c>": lambda _event: self._focus_control(self.change_speed_combo),
            "<Alt-r>": lambda _event: self._invoke_control(self.refresh_button),
            "<Control-r>": lambda _event: self._invoke_control(self.refresh_button),
            "<F5>": lambda _event: self._invoke_control(self.refresh_button),
            "<Alt-p>": lambda _event: self._invoke_control(self.configure_plugins_button),
            "<Escape>": self._minimize_from_keyboard,
        }
        for sequence, callback in bindings.items():
            self.root.bind(sequence, callback)

    @staticmethod
    def _focus_control(widget: Any) -> str:
        if widget.instate(["!disabled"]):
            widget.focus_set()
        return "break"

    @staticmethod
    def _invoke_control(widget: Any) -> str:
        if widget.instate(["!disabled"]):
            widget.invoke()
        return "break"

    def _minimize_from_keyboard(self, _event: Any = None) -> str:
        self.minimize_to_tray()
        return "break"

    def _set_volume_from_keyboard(self, volume: int) -> str:
        if not self._control_ready() or (self._busy and not self._volume_write_inflight):
            self._show_unavailable_error()
            return "break"
        target_volume = clamp(volume, 0, 100)
        if target_volume == self._current_target_volume():
            self._show_volume_overlay(target_volume)
        else:
            self._request_volume_target(target_volume)
        return "break"

    def _adjust_volume_from_keyboard(self, delta: int) -> str:
        self.adjust_selected_volume(delta)
        return "break"

    def _start_display_listener(self) -> None:
        self._display_listener = DisplayChangeListener(
            on_change=self._handle_display_change_from_thread,
            on_error=self._handle_display_listener_error_from_thread,
            on_theme_change=self._handle_theme_change_from_thread,
        )
        try:
            self._display_listener.start()
        except Exception as exc:
            LOGGER.error(
                "Display-change listener startup failed (%s).",
                exc.__class__.__name__,
            )
            self._display_listener = None
            self._topology_valid.clear()
            self._control_unavailable_reason = "Display-change protection is unavailable."
            self._set_status(f"Display-change listener failed: {self._format_error(exc)}")

    def _start_plugins(self) -> None:
        # This import deliberately stays beyond app.py's single-instance boundary.
        # Duplicate launches must not import or discover external plugin code.
        try:
            from plugin_manager import PluginManager

            self._plugin_manager = PluginManager(
                self.root,
                post_to_ui=self._post_to_ui,
                on_notice=self._set_status,
                on_volume_provider_changed=self._on_volume_provider_changed,
                get_start_with_windows=lambda: self.start_with_windows,
                set_start_with_windows=self.set_start_with_windows_enabled,
            )
            self._plugin_manager.start()
            self.monitor_label.configure(textvariable=self.provider_var, underline=-1)
            self.monitor_combo.grid_remove()
            self.refresh_button.configure(text="Refresh volume")
        except Exception as exc:
            LOGGER.error("Plugin system startup failed (%s).", exc.__class__.__name__)
            self._plugin_manager = None
            self._set_status(f"Plugin system failed: {self._format_error(exc)}")

    def _on_volume_provider_changed(self, provider: Any | None, _plugin_id: str | None) -> None:
        if self._closing or provider is None:
            return
        self.provider_var.set(f"Volume provider: {provider.provider_name}")
        self._post_to_ui(self.refresh_volume_provider)

    def configure_plugins(self) -> None:
        if self._plugin_manager is None:
            self._set_status("Plugin configuration is unavailable.")
            return
        try:
            self._plugin_manager.show_configuration(self.root)
        except Exception as exc:
            LOGGER.error("Plugin configuration failed (%s).", exc.__class__.__name__)
            self._set_status(f"Plugin configuration failed: {self._format_error(exc)}")

    def _start_tray_icon(self) -> None:
        try:
            self._tray_icon = TrayIconController(
                tooltip=self.TRAY_TOOLTIP,
                on_restore=lambda: self._post_to_ui(self.restore_from_tray),
                on_exit=lambda: self._post_to_ui(self.on_close),
                on_error=self._handle_tray_error_from_thread,
                icon_path=self.app_icon_path,
                on_refresh=lambda: self._post_to_ui(self.refresh_monitors),
                on_select_monitor=lambda selection: self._post_to_ui(
                    lambda target=selection: self._select_monitor_from_tray(target)
                ),
            )
            self._sync_tray_menu_state()
            self._tray_icon.start()
        except Exception as exc:
            LOGGER.error("Tray icon startup failed (%s).", exc.__class__.__name__)
            self._tray_icon = None
            error_message = self._format_error(exc).rstrip(".")
            self._set_status(f"Tray icon failed: {error_message}. The main window will remain available.")

    def _start_minimized(self) -> None:
        if self._tray_icon is None:
            return
        self.minimize_to_tray()

    def _start_keyboard_listener(self) -> None:
        self._listener = GlobalVolumeKeyListener(
            on_delta=self._queue_hotkey_delta,
            should_consume=self._should_consume_volume_keys,
            on_error=self._handle_listener_error_from_thread,
            step=self.volume_step,
            on_unavailable=self._queue_unavailable_hotkey_notice,
            should_report_unavailable=self._should_report_unavailable_hotkey,
        )
        try:
            self._listener.start()
        except Exception as exc:
            LOGGER.error("Volume-key listener startup failed (%s).", exc.__class__.__name__)
            self._listener = None
            self._set_status(self._format_error(exc))

    def on_change_speed_selected(self, _event: Any = None) -> None:
        selected_speed = self.change_speed_var.get().strip().lower()
        if selected_speed not in self.CHANGE_SPEED_STEPS:
            selected_speed = DEFAULT_CHANGE_SPEED

        self.change_speed = selected_speed
        self.volume_step = self.CHANGE_SPEED_STEPS[selected_speed]
        self.change_speed_var.set(selected_speed.title())
        if self._listener is not None:
            self._listener.set_step(self.volume_step)
        try:
            save_change_speed(selected_speed)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Saving Change speed failed (%s).", exc.__class__.__name__)

    def on_start_with_windows_toggled(self) -> None:
        enabled = bool(self.start_with_windows_var.get())
        try:
            set_start_with_windows(enabled)
        except (OSError, AutostartCommandError, AutostartUnavailableError) as exc:
            self.start_with_windows_var.set(not enabled)
            LOGGER.warning(
                "Updating the Start with Windows setting failed (%s).",
                exc.__class__.__name__,
            )
            action = "enable" if enabled else "disable"
            self._set_status(
                f"Could not {action} Start with Windows: {self._format_error(exc)}"
            )
            return

        self.start_with_windows = enabled
        state = "enabled" if enabled else "disabled"
        self._set_status(f"Start with Windows {state}.")

    def set_start_with_windows_enabled(self, enabled: bool) -> None:
        self.start_with_windows_var.set(bool(enabled))
        self.on_start_with_windows_toggled()

    def _handle_display_change_from_thread(self) -> None:
        self._invalidate_topology_generation()
        self._post_to_ui(self._handle_display_change)

    def _handle_theme_change_from_thread(self) -> None:
        self._post_to_ui(self._schedule_theme_refresh)

    def _schedule_theme_refresh(self) -> None:
        if self._closing:
            return
        if self._theme_after_id is not None:
            self.root.after_cancel(self._theme_after_id)
        self._theme_after_id = self.root.after(
            self.THEME_CHANGE_DEBOUNCE_MS,
            self._apply_live_theme,
        )

    def _apply_live_theme(self) -> None:
        self._theme_after_id = None
        if self._closing:
            return
        self.theme_state = read_windows_theme_state()
        self.dark_mode = self.theme_state.dark_mode
        self.high_contrast = self.theme_state.high_contrast
        self.active_theme = apply_theme(
            self.style,
            self.dark_mode,
            self.high_contrast,
        )
        apply_color_scheme(
            self.root,
            self.status_bar,
            self.dark_mode,
            self.high_contrast,
        )
        if self._overlay is not None:
            self._overlay.apply_theme(self.dark_mode, self.high_contrast)
        plugin_manager = getattr(self, "_plugin_manager", None)
        if plugin_manager is not None:
            plugin_manager.apply_theme(self.dark_mode)
        apply_window_chrome(self.root, self.dark_mode)
        self._apply_scaled_layout()
        self._resize_for_content()

    def _handle_display_listener_error_from_thread(self, exc: Exception) -> None:
        self._invalidate_topology_generation()
        self._post_to_ui(lambda error=exc: self._handle_display_listener_error(error))

    def _handle_display_listener_error(self, exc: Exception) -> None:
        LOGGER.error("Display-change listener failed (%s).", exc.__class__.__name__)
        self._hotkeys_ready = False
        self.current_volume = None
        self.target_volume = None
        self._pending_target_volume = None
        self._control_unavailable_reason = "Display-change protection is unavailable."
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        self._set_status(f"Display-change listener failed: {self._format_error(exc)}")
        self._apply_control_state()

    def _handle_display_change(self) -> None:
        if self._closing:
            return
        self._pending_audio_output_sync = None
        plugin_manager = getattr(self, "_plugin_manager", None)
        if plugin_manager is not None:
            plugin_manager.notify_volume_topology_changed()
        self._hotkeys_ready = False
        self.current_volume = None
        self.target_volume = None
        self._pending_target_volume = None
        self._control_unavailable_reason = "Display configuration changed; checking the selected monitor."
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        if self._listener is not None:
            self._listener.reset_unavailable_notice()
        self._set_status("Display configuration changed. Revalidating the selected monitor...")
        self._apply_control_state()
        self._refresh_retry_index = 0
        self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)

    def _handle_listener_error_from_thread(self, exc: Exception) -> None:
        self._post_to_ui(lambda error=exc: self._handle_listener_error(error))

    def _handle_listener_error(self, exc: Exception) -> None:
        LOGGER.error("Volume-key listener failed (%s).", exc.__class__.__name__)
        self._hotkeys_ready = False
        self._update_hotkey_state()
        self._set_status(f"Volume-key listener failed: {self._format_error(exc)}")

    def _handle_tray_error_from_thread(self, exc: Exception) -> None:
        self._post_to_ui(lambda error=exc: self._handle_tray_error(error))

    def _handle_tray_error(self, exc: Exception) -> None:
        if self._closing:
            return
        LOGGER.error("Tray icon failed (%s).", exc.__class__.__name__)
        self._in_tray = False
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._show_main_window()
        error_message = self._format_error(exc).rstrip(".")
        self._set_status(f"Tray icon failed: {error_message}. The main window was restored.")

    def _invalidate_topology_generation(self) -> None:
        self._topology_valid.clear()
        with self._topology_generation_lock:
            self._topology_generation += 1

    def _current_topology_generation(self) -> int:
        with self._topology_generation_lock:
            return self._topology_generation

    def _is_topology_generation_current(self, generation: int) -> bool:
        return generation == self._current_topology_generation()

    def _display_listener_available(self) -> bool:
        return self._display_listener is not None and self._display_listener.is_active

    def _control_ready(self) -> bool:
        active_provider = (
            getattr(self, "_plugin_manager", None).active_volume_provider()
            if getattr(self, "_plugin_manager", None) is not None
            else None
        )
        return (
            not self._closing
            and self._display_listener_available()
            and self._topology_valid.is_set()
            and (self.selected_key is not None or active_provider is not None)
            and self.current_volume is not None
        )

    def _queue_hotkey_delta(self, delta: int) -> None:
        if not self._closing:
            self._hotkey_delta_queue.put(delta)

    def _queue_unavailable_hotkey_notice(self) -> None:
        self._post_to_ui(self._show_unavailable_error)

    def _should_report_unavailable_hotkey(self) -> bool:
        return (
            not self._closing
            and self._control_unavailable_reason is not None
            and (self.selected_key is not None or self.preferred_selected_key is not None)
        )

    def _should_consume_volume_keys(self) -> bool:
        return (
            self._hotkeys_enabled
            and self._topology_valid.is_set()
            and not self._closing
            and self._listener is not None
            and self._listener.is_active
        )

    def _post_to_ui(self, callback: Callable[[], None]) -> None:
        if not self._closing:
            self._result_queue.put(callback)

    def _poll_queues(self) -> None:
        self._poll_after_id = None
        if self._closing:
            return

        try:
            while True:
                try:
                    callback = self._result_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception as exc:
                    self._report_ui_callback_error(exc)

            if self._hotkeys_enabled and (not self._busy or self._volume_write_inflight):
                pending_delta = 0
                while True:
                    try:
                        pending_delta += self._hotkey_delta_queue.get_nowait()
                    except queue.Empty:
                        break
                if pending_delta:
                    try:
                        self.adjust_selected_volume(pending_delta)
                    except Exception as exc:
                        self._report_ui_callback_error(exc)
            elif not self._hotkeys_enabled:
                while True:
                    try:
                        self._hotkey_delta_queue.get_nowait()
                    except queue.Empty:
                        break
        except Exception as exc:
            self._report_ui_callback_error(exc)
        finally:
            if not self._closing:
                try:
                    self._poll_after_id = self.root.after(50, self._poll_queues)
                except tk.TclError:
                    self._poll_after_id = None

    def _report_ui_callback_error(self, exc: Exception) -> None:
        LOGGER.error("Tk queue callback failed (%s).", exc.__class__.__name__)
        message = f"Internal UI callback failed: {self._format_error(exc)}"
        try:
            self._topology_valid.clear()
            self._hotkeys_ready = False
            self.current_volume = None
            self.target_volume = None
            if self._active_ddc_operation_id is None:
                self._busy = False
                self._volume_write_inflight = False
                self._pending_target_volume = None
            self._control_unavailable_reason = (
                "An internal UI operation failed; monitor control is disabled until Refresh succeeds."
            )
            self._update_hotkey_state()
            self._set_displayed_volume(None)
            self._set_status(message)
            self._apply_control_state()
        except Exception:
            pass

        try:
            self.root.report_callback_exception(type(exc), exc, exc.__traceback__)
        except Exception:
            print(message, file=sys.stderr)

    def _format_error(self, exc: Exception) -> str:
        message = str(exc).strip()
        return message or exc.__class__.__name__

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _set_widget_enabled(self, widget: ttk.Widget, enabled: bool) -> None:
        if enabled:
            widget.state(["!disabled"])
        else:
            widget.state(["disabled"])

    def _apply_control_state(self) -> None:
        if self._closing:
            return

        has_monitors = bool(self.monitors) and getattr(self, "_plugin_manager", None) is None
        self._set_widget_enabled(self.refresh_button, not self._busy)
        self._set_widget_enabled(self.monitor_combo, has_monitors and not self._busy)
        volume_controls_enabled = self._control_ready() and (
            not self._busy or self._volume_write_inflight
        )
        self._set_widget_enabled(self.decrease_button, volume_controls_enabled)
        self._set_widget_enabled(self.increase_button, volume_controls_enabled)
        self._set_widget_enabled(self.volume_scale, volume_controls_enabled)

    def _set_busy(self, busy: bool, status_message: str | None = None) -> None:
        self._busy = busy
        if status_message is not None:
            self._set_status(status_message)
        self._apply_control_state()

    def _update_hotkey_state(self) -> None:
        self._hotkeys_enabled = (
            self._hotkeys_ready
            and self._control_ready()
            and self._listener is not None
            and self._listener.is_active
        )
        self._sync_tray_menu_state()

    def _remember_selected_monitor(self, selection: SavedMonitorSelection) -> None:
        self.selected_key = selection
        should_save = self.preferred_selected_key != selection
        self.preferred_selected_key = selection
        self._update_hotkey_state()
        if not should_save:
            return
        try:
            save_selected_monitor_key(selection)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Saving the selected monitor failed (%s).", exc.__class__.__name__)

    def _schedule_audio_output_reconciliation(
        self,
        generation: int,
        monitors: list[MonitorRef],
        selected_monitor: MonitorRef,
    ) -> None:
        if self._closing or selected_monitor.identity is None:
            return
        monitor_device_paths = tuple(
            monitor.identity.device_path
            for monitor in monitors
            if monitor.identity is not None
        )
        if not monitor_device_paths:
            return
        self._pending_audio_output_sync = (
            generation,
            monitor_device_paths,
            selected_monitor.identity.device_path,
        )
        self._start_pending_audio_output_reconciliation()

    def _start_pending_audio_output_reconciliation(self) -> None:
        if (
            self._closing
            or self._audio_output_sync_inflight
            or self._pending_audio_output_sync is None
        ):
            return
        generation, monitor_device_paths, selected_device_path = (
            self._pending_audio_output_sync
        )
        self._pending_audio_output_sync = None
        self._audio_output_sync_inflight = True
        rename_attempted_ids = frozenset(self._audio_rename_attempted_ids)

        def topology_is_current() -> bool:
            return (
                not self._closing
                and self._topology_valid.is_set()
                and self._is_topology_generation_current(generation)
            )

        def runner() -> None:
            try:
                result = reconcile_monitor_audio_outputs(
                    monitor_device_paths,
                    selected_device_path,
                    is_topology_current=topology_is_current,
                    rename_attempted_ids=rename_attempted_ids,
                )
            except Exception as exc:
                self._post_to_ui(
                    lambda error=exc, token=generation: self._finish_audio_output_reconciliation(
                        None,
                        error,
                        token,
                    )
                )
            else:
                self._post_to_ui(
                    lambda value=result, token=generation: self._finish_audio_output_reconciliation(
                        value,
                        None,
                        token,
                    )
                )

        try:
            threading.Thread(
                target=runner,
                name="audio-output-sync",
                daemon=True,
            ).start()
        except Exception as exc:
            self._finish_audio_output_reconciliation(None, exc, generation)

    def _finish_audio_output_reconciliation(
        self,
        result: AudioOutputResult | None,
        error: Exception | None,
        generation: int,
    ) -> None:
        self._audio_output_sync_inflight = False
        if self._closing:
            return
        can_update_status = (
            self._is_topology_generation_current(generation)
            and self._control_ready()
            and self.status_var.get().startswith("Ready.")
        )
        if error is not None:
            if isinstance(error, AudioOutputTopologyChanged):
                pass
            elif isinstance(error, AudioOutputMatchError):
                LOGGER.info("Windows sound output matching was inconclusive.")
                if can_update_status:
                    self._set_status(
                        "Ready. The matching Windows sound output could not be identified safely; no outputs were changed."
                    )
            else:
                LOGGER.warning(
                    "Windows sound output synchronization failed (%s).",
                    error.__class__.__name__,
                )
                if can_update_status:
                    self._set_status(
                        f"Ready. Windows sound outputs were not fully configured: {self._format_error(error)}"
                    )
        elif result is not None:
            if result.rename_needed:
                self._audio_rename_attempted_ids.add(result.endpoint_id.casefold())
            if result.rename_request_error is not None:
                LOGGER.warning("The FenSound rename request was not approved.")
            if can_update_status:
                if result.rename_request_error is not None:
                    self._set_status(
                        "Ready. Other matched screen outputs are hidden, but Windows did not approve the FenSound rename."
                    )
                elif result.rename_requested:
                    self._set_status(
                        "Ready. Other matched screen outputs are hidden and the FenSound rename is being applied."
                    )
                elif result.rename_needed:
                    self._set_status(
                        "Ready. Other matched screen outputs are hidden; Refresh after approving the FenSound rename."
                    )
                else:
                    self._set_status(
                        "Ready. FenSound is matched to the selected monitor and other matched screen outputs are hidden."
                    )
        self._start_pending_audio_output_reconciliation()

    def _set_displayed_volume(self, volume: int | None) -> None:
        self._ignore_scale_events = True
        try:
            self.volume_var.set(0.0 if volume is None else float(clamp(volume, 0, 100)))
        finally:
            self._ignore_scale_events = False

        if volume is None:
            self.volume_text_var.set("--")
        else:
            self.volume_text_var.set(f"{clamp(volume, 0, 100)}%")
        self._sync_tray_menu_state()

    def _show_volume_overlay(self, volume: int | None = None) -> None:
        if self._closing or self._overlay is None:
            return
        if volume is None:
            volume = self.current_volume
        if volume is not None:
            self._overlay.show(
                clamp(volume, 0, 100),
                preferred_display_device_name=self._selected_display_device_name(),
            )

    def _show_unavailable_error(self, message: str | None = None) -> None:
        reason = message or self._control_unavailable_reason or "Selected monitor is unavailable."
        self._set_status(reason)
        if not self._closing and self._overlay is not None:
            self._overlay.show_error(
                reason,
                preferred_display_device_name=self._selected_display_device_name(),
            )

    def _selected_display_device_name(self) -> str | None:
        if self.selected_key is None:
            return None
        for monitor_ref in self.monitors:
            if monitor_ref.selection_key == self.selected_key:
                return monitor_ref.display_device_name
        return None

    def _selected_monitor(self) -> MonitorRef | None:
        current_index = self.monitor_combo.current()
        if current_index < 0 or current_index >= len(self.monitors):
            return None
        return self.monitors[current_index]

    def _clear_active_selection(self) -> None:
        self.selected_key = None
        self.current_volume = None
        self.target_volume = None
        self._set_displayed_volume(None)
        self._hotkeys_ready = False
        self._pending_target_volume = None
        self._topology_valid.clear()
        self._update_hotkey_state()

    def _current_target_volume(self) -> int | None:
        if self.target_volume is not None:
            return self.target_volume
        return self.current_volume

    def _update_monitor_list(self, monitors: list[MonitorRef], selected_index: int | None) -> None:
        self.monitors = monitors
        self.monitor_combo["values"] = [monitor_ref.display_name for monitor_ref in monitors]
        if selected_index is None:
            self.monitor_var.set("")
        else:
            self.monitor_combo.current(selected_index)
        self._sync_tray_menu_state()

    def _sync_tray_menu_state(self) -> None:
        tray_icon = getattr(self, "_tray_icon", None)
        if tray_icon is None:
            return

        selected_key = getattr(self, "selected_key", None)
        active_monitor = selected_key.description if selected_key is not None else None
        monitor_items: list[TrayMonitorMenuItem] = []
        for monitor_ref in getattr(self, "monitors", ()):
            selection = monitor_ref.selection_key
            if selection is None:
                continue
            active = selection == selected_key
            if active:
                active_monitor = monitor_ref.display_name
            monitor_items.append(
                TrayMonitorMenuItem(
                    label=monitor_ref.display_name,
                    selection=selection,
                    active=active,
                )
            )

        tray_icon.update_menu_state(
            TrayMenuState(
                active_monitor=active_monitor,
                current_volume=getattr(self, "current_volume", None),
                routing_enabled=bool(getattr(self, "_hotkeys_enabled", False)),
                monitors=tuple(monitor_items),
            )
        )

    def _select_monitor_from_tray(self, selection: SavedMonitorSelection) -> None:
        if self._closing:
            return
        if self._busy:
            self._set_status("Wait for the current monitor operation before switching monitors.")
            return
        self.refresh_monitors(selection_target=selection)

    @staticmethod
    def _selection_error_message(status: SelectionMatchStatus) -> str:
        if status == SelectionMatchStatus.AMBIGUOUS:
            return "Selected monitor identity is ambiguous. Select the monitor again."
        if status == SelectionMatchStatus.UNVERIFIABLE:
            return "The monitor identity could not be verified; volume control is disabled."
        if status == SelectionMatchStatus.NEEDS_SELECTION:
            return "Select a monitor before monitor-volume control can start."
        return "Selected monitor was not found. Reconnect it or select another monitor."

    def _schedule_refresh(self, delay_ms: int, automatic: bool) -> None:
        if self._closing:
            return
        if self._refresh_after_id is not None:
            self.root.after_cancel(self._refresh_after_id)
        self._refresh_after_id = self.root.after(
            delay_ms,
            lambda: self._run_scheduled_refresh(automatic=automatic),
        )

    def _run_scheduled_refresh(self, automatic: bool) -> None:
        self._refresh_after_id = None
        self.refresh_monitors(automatic=automatic)

    def _schedule_next_refresh_retry(self) -> None:
        if self._refresh_retry_index >= len(self.REFRESH_RETRY_DELAYS_MS):
            return
        delay = self.REFRESH_RETRY_DELAYS_MS[self._refresh_retry_index]
        self._refresh_retry_index += 1
        self._schedule_refresh(delay, automatic=True)

    def _run_deferred_refresh(self) -> None:
        if not self._refresh_requested or self._busy or self._closing:
            return
        automatic = self._refresh_requested_automatic
        self._refresh_requested = False
        self._refresh_requested_automatic = False
        if self._refresh_after_id is not None:
            self.root.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        self.refresh_monitors(automatic=automatic)

    def _begin_ddc_operation(self, kind: str) -> int:
        if self._active_ddc_operation_id is not None:
            raise RuntimeError("A DDC operation is already active.")
        self._ddc_operation_sequence += 1
        operation_id = self._ddc_operation_sequence
        self._active_ddc_operation_id = operation_id
        self._active_ddc_operation_kind = kind
        self._ddc_operation_timed_out = False
        self._ddc_timeout_after_id = self.root.after(
            self.DDC_OPERATION_TIMEOUT_MS,
            lambda token=operation_id: self._handle_ddc_operation_timeout(token),
        )
        return operation_id

    def _accept_ddc_completion(self, operation_id: int | None) -> bool:
        # Optional IDs keep direct, hardware-free unit calls to the completion
        # helpers useful while every real worker supplies a token.
        if operation_id is None:
            return True
        if operation_id != self._active_ddc_operation_id:
            return False

        timed_out = self._ddc_operation_timed_out
        if self._ddc_timeout_after_id is not None:
            try:
                self.root.after_cancel(self._ddc_timeout_after_id)
            except tk.TclError:
                pass
        self._ddc_timeout_after_id = None
        self._active_ddc_operation_id = None
        self._active_ddc_operation_kind = None
        self._ddc_operation_timed_out = False

        if timed_out:
            self._finish_timed_out_ddc_operation()
            return False
        return True

    def _handle_ddc_operation_timeout(self, operation_id: int) -> None:
        if self._closing or operation_id != self._active_ddc_operation_id:
            return

        operation_kind = self._active_ddc_operation_kind or "DDC"
        LOGGER.error("%s operation exceeded the watchdog deadline.", operation_kind)
        self._ddc_timeout_after_id = None
        self._ddc_operation_timed_out = True
        self._invalidate_topology_generation()
        self._hotkeys_ready = False
        self.current_volume = None
        self.target_volume = None
        self._pending_target_volume = None
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        reason = (
            f"{operation_kind} timed out. Monitor state is unknown; control remains disabled "
            "until the DDC call returns and Refresh succeeds. Restart the app if it does not return."
        )
        self._control_unavailable_reason = reason
        self._show_unavailable_error(reason)
        self._apply_control_state()

    def _finish_timed_out_ddc_operation(self) -> None:
        if self._closing:
            return
        self._volume_write_inflight = False
        self._pending_target_volume = None
        self._busy = False
        self._refresh_retry_index = 0
        self._apply_control_state()
        if self._refresh_requested:
            self._run_deferred_refresh()
        else:
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)

    def refresh_volume_provider(self, automatic: bool = False) -> None:
        """Read the selected provider without exposing provider-specific state to Tk."""
        manager = self._plugin_manager
        provider = manager.active_volume_provider() if manager is not None else None
        if provider is None:
            self._control_unavailable_reason = "Select a volume provider in Configure plugins."
            self._set_status(self._control_unavailable_reason)
            self._apply_control_state()
            return
        if self._busy or self._closing:
            self._refresh_requested = True
            self._refresh_requested_automatic = self._refresh_requested_automatic or automatic
            return
        generation = self._current_topology_generation()
        self._topology_valid.clear()
        self._hotkeys_ready = False
        self.current_volume = None
        self.target_volume = None
        self._set_displayed_volume(None)
        self._update_hotkey_state()
        self._set_busy(True, f"Reading {provider.provider_name}...")
        operation_id = self._begin_ddc_operation("Volume provider read")

        def runner() -> None:
            try:
                value = clamp(int(provider.read_volume()), 0, 100)
            except Exception as exc:
                self._post_to_ui(lambda error=exc, token=generation, operation=operation_id: self._finish_provider_refresh_error(error, token, automatic, operation))
            else:
                self._post_to_ui(lambda volume=value, token=generation, operation=operation_id: self._finish_provider_refresh(volume, provider, token, operation))
        threading.Thread(target=runner, name="volume-provider-read", daemon=True).start()

    def _finish_provider_refresh(self, volume: int, provider: Any, generation: int, operation_id: int) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        self._busy = False
        if not self._is_topology_generation_current(generation):
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)
            return
        self.current_volume = clamp(volume, 0, 100)
        self.target_volume = self.current_volume
        self._set_displayed_volume(self.current_volume)
        if self._display_listener_available():
            self._topology_valid.set()
            self._hotkeys_ready = True
            self._control_unavailable_reason = None
        else:
            self._control_unavailable_reason = "Display-change protection is unavailable."
        self._update_hotkey_state()
        self._set_status(f"Ready. Volume provider: {provider.provider_name}.")
        self._apply_control_state()
        self._run_deferred_refresh()

    def _finish_provider_refresh_error(self, exc: Exception, generation: int, automatic: bool, operation_id: int) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        self._busy = False
        self.current_volume = None
        self.target_volume = None
        self._hotkeys_ready = False
        self._topology_valid.clear()
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        self._control_unavailable_reason = self._format_error(exc)
        self._set_status(self._control_unavailable_reason)
        self._apply_control_state()
        if automatic:
            self._schedule_next_refresh_retry()

    def _start_provider_volume_write(self, provider: Any, target_volume: int) -> None:
        generation = self._current_topology_generation()
        self._volume_write_inflight = True
        self._pending_target_volume = None
        self._set_busy(True, f"Setting {provider.provider_name} to {target_volume}%...")
        operation_id = self._begin_ddc_operation("Volume provider write")
        def runner() -> None:
            try:
                value = clamp(int(provider.write_volume(target_volume)), 0, 100)
            except Exception as exc:
                self._post_to_ui(lambda error=exc, token=generation, operation=operation_id: self._finish_provider_write_error(error, token, operation))
            else:
                self._post_to_ui(lambda volume=value, token=generation, operation=operation_id: self._finish_provider_write_success(volume, provider, token, operation))
        threading.Thread(target=runner, name="volume-provider-write", daemon=True).start()

    def _finish_provider_write_success(self, volume: int, provider: Any, generation: int, operation_id: int) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        if not self._is_topology_generation_current(generation):
            self._finish_provider_write_error(DisplayTopologyChanged("Display changed while setting volume; provider state is unknown."), generation, None)
            return
        next_target = self._pending_target_volume
        self.current_volume = clamp(volume, 0, 100)
        self._volume_write_inflight = False
        self._busy = False
        if next_target is not None and next_target != self.current_volume:
            self.target_volume = next_target
            self._pending_target_volume = None
            self._start_provider_volume_write(provider, next_target)
            return
        self.target_volume = self.current_volume
        self._pending_target_volume = None
        self._hotkeys_ready = True
        self._control_unavailable_reason = None
        self._update_hotkey_state()
        self._set_displayed_volume(self.current_volume)
        self._show_volume_overlay(self.current_volume)
        self._set_status(f"{provider.provider_name}: {self.current_volume}%")
        self._apply_control_state()

    def _finish_provider_write_error(self, exc: Exception, _generation: int, operation_id: int | None) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        self._volume_write_inflight = False
        self._pending_target_volume = None
        self._busy = False
        self.current_volume = None
        self.target_volume = None
        self._hotkeys_ready = False
        self._topology_valid.clear()
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        self._control_unavailable_reason = f"{self._format_error(exc).rstrip('.')}. Volume control is disabled."
        self._show_unavailable_error(self._control_unavailable_reason)
        self._apply_control_state()

    def refresh_monitors(
        self,
        automatic: bool = False,
        selection_target: SavedMonitorSelection | None = None,
    ) -> None:
        if selection_target is None and getattr(self, "_plugin_manager", None) is not None:
            self.refresh_volume_provider(automatic=automatic)
            return
        if self._refresh_after_id is not None:
            self.root.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        if self._busy or self._closing:
            self._refresh_requested = True
            self._refresh_requested_automatic = self._refresh_requested_automatic or automatic
            return

        if not automatic:
            self._refresh_retry_index = 0
        if selection_target is None:
            selection_target = self.selected_key or self.preferred_selected_key

        generation = self._current_topology_generation()
        self._topology_valid.clear()
        self._hotkeys_ready = False
        self.current_volume = None
        self.target_volume = None
        self._set_displayed_volume(None)
        if selection_target is not None:
            self._control_unavailable_reason = "Selected monitor is being revalidated."
            if self._listener is not None:
                self._listener.reset_unavailable_notice()
        self._update_hotkey_state()
        self._set_busy(True, "Searching for monitors...")
        operation_id = self._begin_ddc_operation("Monitor discovery")

        def runner() -> None:
            try:
                monitors = enumerate_monitors()
                match = match_selected_monitor(monitors, selection_target)
                if match.status != SelectionMatchStatus.FOUND or match.index is None:
                    result: RefreshResult = monitors, match, None, None
                else:
                    try:
                        volume = read_monitor_volume(monitors[match.index])
                    except Exception as exc:
                        result = monitors, match, None, exc
                    else:
                        result = monitors, match, volume, None
            except Exception as exc:
                self._post_to_ui(
                    lambda error=exc, token=generation, retry=automatic, operation=operation_id: self._finish_refresh_error(
                        error,
                        token,
                        retry,
                        operation,
                    )
                )
            else:
                self._post_to_ui(
                    lambda value=result, token=generation, retry=automatic, operation=operation_id: self._finish_refresh(
                        value,
                        token,
                        retry,
                        operation,
                    )
                )

        try:
            threading.Thread(target=runner, name="ddc-gui-worker", daemon=True).start()
        except Exception as exc:
            self._finish_refresh_error(exc, generation, automatic, operation_id)

    def _finish_refresh(
        self,
        result: RefreshResult,
        generation: int,
        automatic: bool,
        operation_id: int | None = None,
    ) -> None:
        if self._closing:
            return
        if not self._accept_ddc_completion(operation_id):
            return
        self._busy = False
        if not self._is_topology_generation_current(generation):
            self._apply_control_state()
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)
            return

        monitors, match, volume, volume_error = result
        selected_index = match.index if match.status == SelectionMatchStatus.FOUND else None
        self._update_monitor_list(monitors, selected_index)

        if match.status != SelectionMatchStatus.FOUND or selected_index is None:
            self._clear_active_selection()
            if not monitors:
                reason = "No DDC/CI monitors found."
            else:
                reason = self._selection_error_message(match.status)
            self._control_unavailable_reason = reason
            self._set_status(reason)
            self._apply_control_state()
            if automatic and match.status not in (
                SelectionMatchStatus.AMBIGUOUS,
                SelectionMatchStatus.NEEDS_SELECTION,
            ):
                self._schedule_next_refresh_retry()
            self._run_deferred_refresh()
            return

        selected_monitor = monitors[selected_index]
        selection = selected_monitor.selection_key
        if selection is None:
            self._clear_active_selection()
            self._control_unavailable_reason = self._selection_error_message(
                SelectionMatchStatus.UNVERIFIABLE
            )
            self._set_status(self._control_unavailable_reason)
            self._apply_control_state()
            return

        if volume_error is not None or volume is None:
            failure = volume_error or RuntimeError("Monitor volume is unavailable.")
            LOGGER.warning("Selected-monitor volume read failed (%s).", failure.__class__.__name__)
            self.selected_key = selection
            self.current_volume = None
            self.target_volume = None
            self._hotkeys_ready = False
            self._topology_valid.clear()
            self._update_hotkey_state()
            self._set_displayed_volume(None)
            reason = self._format_error(failure)
            self._control_unavailable_reason = reason
            self._set_status(reason)
            self._apply_control_state()
            if automatic:
                self._schedule_next_refresh_retry()
            return

        self.current_volume = volume
        self.target_volume = volume
        self._set_displayed_volume(volume)
        self._remember_selected_monitor(selection)
        if self._display_listener_available():
            self._topology_valid.set()
            self._hotkeys_ready = True
            self._control_unavailable_reason = None
        else:
            self._topology_valid.clear()
            self._hotkeys_ready = False
            self._control_unavailable_reason = "Display-change protection is unavailable."
        self._update_hotkey_state()
        if self._listener is not None:
            self._listener.reset_unavailable_notice()
        if self._control_ready():
            self._set_status(
                f"Ready. {len(monitors)} monitor(s) detected. Volume keys control {selected_monitor.description}."
            )
        else:
            self._set_status(
                f"{selected_monitor.description} detected at {volume}%, but display-change protection is unavailable."
            )
        if self._control_ready():
            plugin_manager = getattr(self, "_plugin_manager", None)
            if plugin_manager is not None and plugin_manager.active_volume_provider() is not None:
                self._apply_control_state()
                self._run_deferred_refresh()
                return
            self._schedule_audio_output_reconciliation(
                generation,
                monitors,
                selected_monitor,
            )
        self._apply_control_state()
        self._run_deferred_refresh()

    def _finish_refresh_error(
        self,
        exc: Exception,
        generation: int,
        automatic: bool,
        operation_id: int | None = None,
    ) -> None:
        if self._closing:
            return
        if not self._accept_ddc_completion(operation_id):
            return
        self._busy = False
        if not self._is_topology_generation_current(generation):
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)
            return
        LOGGER.error("Monitor refresh failed (%s).", exc.__class__.__name__)
        self.monitors = []
        self.monitor_combo["values"] = ()
        self.monitor_var.set("")
        self._clear_active_selection()
        reason = self._format_error(exc)
        self._control_unavailable_reason = reason
        self._set_status(reason)
        self._apply_control_state()
        if automatic:
            self._schedule_next_refresh_retry()
        self._run_deferred_refresh()

    def on_monitor_selected(self, _event: Any = None) -> None:
        monitor_ref = self._selected_monitor()
        if monitor_ref is None or self._busy:
            return
        selection = monitor_ref.selection_key
        if selection is None:
            self._clear_active_selection()
            reason = "The selected monitor has no verifiable Windows identity."
            self._control_unavailable_reason = reason
            self._show_unavailable_error(reason)
            self._apply_control_state()
            return
        self.refresh_monitors(selection_target=selection)

    def _request_volume_target(self, target_volume: int) -> None:
        if not self._control_ready():
            self._show_unavailable_error()
            return

        target_volume = clamp(target_volume, 0, 100)
        self.target_volume = target_volume
        self._set_displayed_volume(target_volume)
        self._show_volume_overlay(target_volume)

        if self._volume_write_inflight:
            self._pending_target_volume = target_volume
            self._set_status(f"Queued volume {target_volume}%...")
            return

        provider = self._plugin_manager.active_volume_provider() if self._plugin_manager is not None else None
        if provider is not None:
            self._start_provider_volume_write(provider, target_volume)
        elif self.selected_key is not None:
            self._start_volume_write(self.selected_key, target_volume)

    def _start_volume_write(
        self,
        selection: SavedMonitorSelection,
        target_volume: int,
    ) -> None:
        if self._closing or not self._control_ready():
            self._show_unavailable_error()
            return

        generation = self._current_topology_generation()
        self._volume_write_inflight = True
        self._pending_target_volume = None
        self._set_busy(True, f"Validating monitor and setting volume to {target_volume}%...")
        operation_id = self._begin_ddc_operation("Monitor volume write")

        def runner() -> None:
            try:
                monitors = enumerate_monitors()
                match = match_selected_monitor(monitors, selection)
                if match.status != SelectionMatchStatus.FOUND or match.index is None:
                    raise MonitorSelectionUnavailable(
                        self._selection_error_message(match.status),
                        monitors,
                    )
                if not self._is_topology_generation_current(generation) or not self._topology_valid.is_set():
                    raise DisplayTopologyChanged(
                        "Display changed while validating the selected monitor."
                    )
                monitor_ref = monitors[match.index]
                fresh_selection = monitor_ref.selection_key
                if fresh_selection is None:
                    raise MonitorSelectionUnavailable(
                        "The selected monitor identity could not be verified.",
                        monitors,
                    )
                new_volume = set_monitor_volume(monitor_ref, target_volume)
                result: WriteResult = monitors, match.index, new_volume, fresh_selection
            except Exception as exc:
                self._post_to_ui(
                    lambda error=exc, token=generation, operation=operation_id: self._finish_volume_write_error(
                        error,
                        token,
                        operation,
                    )
                )
            else:
                self._post_to_ui(
                    lambda value=result, token=generation, operation=operation_id: self._finish_volume_write_success(
                        value,
                        token,
                        operation,
                    )
                )

        try:
            threading.Thread(target=runner, name="ddc-volume-write", daemon=True).start()
        except Exception as exc:
            self._finish_volume_write_error(exc, generation, operation_id)

    def _finish_volume_write_success(
        self,
        result: WriteResult,
        generation: int,
        operation_id: int | None = None,
    ) -> None:
        if self._closing:
            return
        if not self._accept_ddc_completion(operation_id):
            return
        monitors, selected_index, new_volume, selection = result
        if not self._is_topology_generation_current(generation) or not self._topology_valid.is_set():
            self._finish_volume_write_error(
                DisplayTopologyChanged(
                    "Display changed while setting volume; monitor volume may have changed."
                ),
                generation,
            )
            return

        self._update_monitor_list(monitors, selected_index)
        self.current_volume = new_volume
        self._remember_selected_monitor(selection)
        self._hotkeys_ready = True
        self._control_unavailable_reason = None
        self._update_hotkey_state()

        next_target = self._pending_target_volume
        if next_target is not None and next_target != new_volume:
            # Keep the newest requested value authoritative while its follow-up
            # write is validated. Resetting this to the older readback makes
            # rapid +/- events calculate from a value that is one write behind.
            self.target_volume = next_target
            self._pending_target_volume = None
            self._volume_write_inflight = False
            self._busy = False
            self._start_volume_write(selection, next_target)
            return

        self.target_volume = new_volume
        self._volume_write_inflight = False
        self._pending_target_volume = None
        self._busy = False
        self._set_displayed_volume(new_volume)
        self._show_volume_overlay(new_volume)
        self._set_status(f"{selection.description} volume: {new_volume}%")
        self._apply_control_state()
        self._run_deferred_refresh()

    def _finish_volume_write_error(
        self,
        exc: Exception,
        generation: int | None = None,
        operation_id: int | None = None,
    ) -> None:
        if self._closing:
            return
        if not self._accept_ddc_completion(operation_id):
            return

        LOGGER.error("Monitor volume write failed (%s).", exc.__class__.__name__)
        if isinstance(exc, MonitorSelectionUnavailable) and exc.monitors is not None:
            self._update_monitor_list(exc.monitors, None)
        self._volume_write_inflight = False
        self._pending_target_volume = None
        self._busy = False
        self.current_volume = None
        self.target_volume = None
        self._hotkeys_ready = False
        self._topology_valid.clear()
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        error_message = self._format_error(exc).rstrip(".")
        if isinstance(exc, MonitorSelectionUnavailable):
            reason = f"{error_message}."
        elif isinstance(exc, DisplayTopologyChanged):
            reason = f"{error_message}."
        else:
            reason = f"{error_message}. Monitor volume may have changed; control is disabled."
        self._control_unavailable_reason = reason
        self._show_unavailable_error(reason)
        self._apply_control_state()
        self._refresh_retry_index = 0
        if self._refresh_requested:
            self._run_deferred_refresh()
        else:
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)

    def on_scale_moved(self, value: str) -> None:
        if self._ignore_scale_events:
            return
        self.volume_text_var.set(f"{clamp(round(float(value)), 0, 100)}%")

    def on_scale_released(self, _event: Any = None) -> None:
        if not self._control_ready() or (self._busy and not self._volume_write_inflight):
            self._show_unavailable_error()
            return

        target_volume = clamp(round(self.volume_var.get()), 0, 100)
        current_target = self._current_target_volume()
        if target_volume == current_target:
            self._show_volume_overlay(target_volume)
            return

        self._request_volume_target(target_volume)

    def adjust_selected_volume(self, delta: int) -> None:
        if not self._control_ready() or (self._busy and not self._volume_write_inflight):
            self._show_unavailable_error()
            return

        base_volume = self._current_target_volume()
        if base_volume is None:
            self._show_unavailable_error()
            return

        target_volume = clamp(base_volume + delta, 0, 100)
        if target_volume == base_volume:
            if delta < 0:
                self._set_status("Volume is already at 0%.")
            else:
                self._set_status("Volume is already at 100%.")
            self._show_volume_overlay(base_volume)
            return

        self._request_volume_target(target_volume)

    def minimize_to_tray(self) -> None:
        if self._closing or self._in_tray or self._tray_icon is None:
            return
        try:
            self._tray_icon.show()
        except Exception as exc:
            self._handle_tray_error(exc)
            return
        self._in_tray = True
        self.root.withdraw()

    def _show_main_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        apply_window_chrome(self.root, self.dark_mode)
        self.root.lift()
        self.root.focus_force()

    def restore_from_tray(self) -> None:
        if self._closing or not self._in_tray:
            return
        self._in_tray = False
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._show_main_window()

    def on_window_unmap(self, _event: Any = None) -> None:
        if self._closing or self._in_tray or self._tray_icon is None:
            return
        self.root.after_idle(self._minimize_if_iconified)

    def _minimize_if_iconified(self) -> None:
        if self._closing or self._in_tray or self._tray_icon is None:
            return
        try:
            window_state = self.root.state()
        except tk.TclError:
            return
        if window_state == "iconic":
            self.minimize_to_tray()

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._pending_audio_output_sync = None
        self._topology_valid.clear()
        self._hotkeys_ready = False
        self._update_hotkey_state()
        shutdown_failures: list[str] = []
        plugin_manager = getattr(self, "_plugin_manager", None)
        if plugin_manager is not None:
            self._stop_native_controller("Plugin system", plugin_manager, shutdown_failures)
            self._plugin_manager = None
        if self._tray_icon is not None:
            self._tray_icon.hide()
        if self._poll_after_id is not None:
            self.root.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        if self._refresh_after_id is not None:
            self.root.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        if self._ddc_timeout_after_id is not None:
            self.root.after_cancel(self._ddc_timeout_after_id)
            self._ddc_timeout_after_id = None
        if self._theme_after_id is not None:
            self.root.after_cancel(self._theme_after_id)
            self._theme_after_id = None
        if self._scale_after_id is not None:
            self.root.after_cancel(self._scale_after_id)
            self._scale_after_id = None
        if self._listener is not None:
            self._stop_native_controller("Volume-key listener", self._listener, shutdown_failures)
            self._listener = None
        if self._display_listener is not None:
            self._stop_native_controller(
                "Display-change listener",
                self._display_listener,
                shutdown_failures,
            )
            self._display_listener = None
        if self._tray_icon is not None:
            self._stop_native_controller("Tray controller", self._tray_icon, shutdown_failures)
            self._tray_icon = None
        if shutdown_failures:
            message = "Shutdown warning: " + "; ".join(shutdown_failures)
            LOGGER.error("Native controller shutdown did not complete cleanly.")
            print(message, file=sys.stderr)
            try:
                self._set_status(message)
                self.root.update_idletasks()
            except Exception:
                pass
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        self.root.destroy()

    @staticmethod
    def _stop_native_controller(
        name: str,
        controller: Any,
        failures: list[str],
    ) -> None:
        try:
            stopped = controller.stop()
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            failures.append(f"{name} stop failed: {message}")
        else:
            if not stopped:
                failures.append(f"{name} did not stop before the timeout")
