from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Mapping, TypeAlias

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
from app_version import APP_VERSION
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
from diagnostics import get_logger, read_log_contents
from config_archive import (
    ARCHIVE_EXTENSION,
    DEFAULT_ARCHIVE_NAME,
    ConfigurationArchiveError,
    add_to_import_history,
    configuration_directory,
    export_configuration,
    import_configuration,
    recent_configurations,
)
from plugin_api import OverlayRenderer, VolumeStatus
from settings import load_selected_monitor_key, save_selected_monitor_key
from theme import (
    DARK_BORDER,
    DARK_SURFACE,
    DARK_TEXT,
    LIGHT_BORDER,
    LIGHT_LIST_BG,
    LIGHT_TEXT,
    apply_app_icon,
    apply_color_scheme,
    apply_theme,
    apply_window_chrome,
    configure_style_metrics,
    get_tk_window_dpi,
    read_windows_theme_state,
)
from native_platform import (
    DisplayChangeListener,
    GlobalVolumeKeyListener,
    TrayIconController,
    TrayMenuState,
    TrayMonitorMenuItem,
    TraySignalMenuItem,
)
from web_presentation import PresentationSnapshot, UserActionError, WebPresentationController


RefreshResult: TypeAlias = tuple[list[MonitorRef], SelectionMatch, int | None, Exception | None]
WriteResult: TypeAlias = tuple[list[MonitorRef], int, int, SavedMonitorSelection]
LOGGER = get_logger(__name__)


class MonitorSelectionUnavailable(RuntimeError):
    def __init__(self, message: str, monitors: list[MonitorRef] | None = None) -> None:
        super().__init__(message)
        self.monitors = monitors


class DisplayTopologyChanged(RuntimeError):
    pass


class RouteInputRepeatScheduler:
    """Bounded held-key state; polling emits at most one delta per route."""

    INITIAL_DELAY_SECONDS = 0.35
    REPEAT_INTERVAL_SECONDS = 0.075

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._held: dict[str, tuple[int, float]] = {}

    def key_event(self, route_id: str, delta: int, pressed: bool) -> tuple[tuple[str, int], ...]:
        if not pressed:
            self._held.pop(route_id, None)
            return ()
        if route_id in self._held:
            return ()
        self._held[route_id] = (delta, self._clock() + self.INITIAL_DELAY_SECONDS)
        return ((route_id, delta),)

    def poll(self) -> tuple[tuple[str, int], ...]:
        now = self._clock()
        due: list[tuple[str, int]] = []
        for route_id, (delta, next_repeat) in tuple(self._held.items()):
            if now >= next_repeat:
                self._held[route_id] = (delta, now + self.REPEAT_INTERVAL_SECONDS)
                due.append((route_id, delta))
        return tuple(due)

    def is_held(self, route_id: str) -> bool:
        return route_id in self._held

    def cancel(self, route_ids: set[str] | None = None) -> None:
        if route_ids is None:
            self._held.clear()
        else:
            for route_id in route_ids:
                self._held.pop(route_id, None)


class MonitorVolumeApp:
    WINDOWS_VOLUME_INPUT_ID = "windows-volume-keys"
    TRAY_TOOLTIP = "FenSoundSwitch"
    DISPLAY_CHANGE_DEBOUNCE_MS = 500
    DDC_OPERATION_TIMEOUT_MS = 10_000
    REFRESH_RETRY_DELAYS_MS = (1000, 2000, 4000)
    THEME_CHANGE_DEBOUNCE_MS = 100
    LOG_VIEW_REFRESH_MS = 1000
    BASE_WINDOW_MIN_WIDTH = 900
    DECREASE_VOLUME_LABEL = "Decrease volume"
    INCREASE_VOLUME_LABEL = "Increase volume"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # macOS presents only the pywebview command center. Tk remains alive
        # solely as the scheduler and host for the existing overlay renderer.
        self._web_only = sys.platform == "darwin"
        self.root.title("FenSoundSwitch")
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
        self._volume_statuses: dict[str, VolumeStatus] = {}
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
        self.restart_requested = False
        self._ignore_scale_events = False
        self._result_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._hotkey_delta_queue: queue.Queue[int] = queue.Queue()
        self._pending_hotkey_delta = 0
        self._route_input_queue: queue.Queue[tuple[str, int]] = queue.Queue()
        self._route_volume_queue: queue.Queue[tuple[str, int]] = queue.Queue()
        self._route_key_queue: queue.Queue[tuple[str, int, bool]] = queue.Queue()
        self._route_repeat_scheduler = RouteInputRepeatScheduler()
        self._pending_route_deltas: dict[str, int] = {}
        self._route_optimistic_targets: dict[str, int] = {}
        self._route_reconciliation_pending: set[str] = set()
        self._hotkeys_ready = False
        self._hotkeys_enabled = False
        self._ready_route_ids: set[str] = set()
        self._listener: GlobalVolumeKeyListener | None = None
        self._display_listener: DisplayChangeListener | None = None
        self._plugin_manager: Any | None = None
        self._overlay: OverlayRenderer | None = None
        self._volume_write_inflight = False
        self._pending_target_volume: int | None = None
        self._tray_icon: TrayIconController | None = None
        self._log_window: tk.Toplevel | None = None
        self._log_text: tk.Text | None = None
        self._log_refresh_after_id: str | None = None
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
        self._control_unavailable_reason: str | None = "Configured routes are not ready."

        self.monitor_var = tk.StringVar()
        self.start_with_windows_var = tk.BooleanVar(value=self.start_with_windows)
        self.status_var = tk.StringVar(value="Searching for monitors...")

        if not self._web_only:
            self.app_icon_path = apply_app_icon(self.root)
            self._build_widgets()
            apply_color_scheme(
                self.root,
                self.status_bar,
                self.dark_mode,
                self.high_contrast,
            )
            log_text = getattr(self, "_log_text", None)
            if log_text is not None:
                self._style_log_text(log_text)
            self._lock_window_size()
            apply_window_chrome(self.root, self.dark_mode)
            self._bind_keyboard_shortcuts()
            self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._apply_control_state()
        self._start_plugins()
        self._start_display_listener()
        self._start_tray_icon()
        self._start_web_presentation()
        self._start_minimized()
        self._start_keyboard_listener()
        self._poll_after_id = self.root.after(50, self._poll_queues)
        self._refresh_after_id = self.root.after(50, self.refresh_configured_routes)

    def _build_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.content_frame = ttk.Frame(self.root, style="App.TFrame")
        self.content_frame.grid(row=0, column=0, sticky="nsew")
        self.content_frame.columnconfigure(1, weight=1)
        self.content_frame.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(
            self.content_frame,
            style="Sidebar.TFrame",
            padding=(self._scaled_px(14), self._scaled_px(22)),
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(5, weight=1)

        self.brand_frame = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self.brand_frame.grid(row=0, column=0, sticky="ew", padx=self._scaled_px(8))
        ttk.Label(
            self.brand_frame,
            text="FenSoundSwitch",
            style="AppTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.brand_frame,
            text="Audio routing control",
            style="AppSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(self._scaled_px(2), 0))

        self.navigation = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self.navigation.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(self._scaled_px(24), 0),
        )
        self.navigation.columnconfigure(0, weight=1)
        self.route_nav_button = ttk.Button(
            self.navigation,
            text="Routes",
            style="Selected.Nav.TButton",
            command=lambda: self._show_page("routes"),
        )
        self.route_nav_button.grid(row=0, column=0, sticky="ew")
        self.plugin_nav_button = ttk.Button(
            self.navigation,
            text="Automations",
            style="Nav.TButton",
            command=lambda: self._show_page("plugins"),
        )
        self.plugin_nav_button.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(self._scaled_px(4), 0),
        )
        self.integrations_nav_button = ttk.Button(
            self.navigation,
            text="Integrations",
            style="Nav.TButton",
            command=lambda: self._show_page("integrations"),
        )
        self.integrations_nav_button.grid(row=2, column=0, sticky="ew", pady=(self._scaled_px(4), 0))
        self.appearance_nav_button = ttk.Button(
            self.navigation,
            text="Appearance",
            style="Nav.TButton",
            command=lambda: self._show_page("appearance"),
        )
        self.appearance_nav_button.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(self._scaled_px(4), 0),
        )
        self.settings_nav_button = ttk.Button(
            self.navigation,
            text="Settings",
            style="Nav.TButton",
            command=lambda: self._show_page("settings"),
        )
        self.settings_nav_button.grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(self._scaled_px(4), 0),
        )

        self.main_frame = ttk.Frame(
            self.content_frame,
            style="Content.TFrame",
            padding=(self._scaled_px(28), self._scaled_px(24)),
        )
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(1, weight=1)

        self.page_header = ttk.Frame(self.main_frame, style="Content.TFrame")
        self.page_header.grid(row=0, column=0, sticky="ew", pady=(0, self._scaled_px(18)))
        self.page_title_var = tk.StringVar(value="Audio routes")
        self.page_subtitle_var = tk.StringVar(
            value="Send each input to the output you want to control."
        )
        self.monitor_label = ttk.Label(
            self.page_header,
            textvariable=self.page_title_var,
            style="PageTitle.TLabel",
        )
        self.monitor_label.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ttk.Label(
            self.page_header,
            textvariable=self.page_subtitle_var,
            style="PageSubtitle.TLabel",
        )
        self.page_subtitle.grid(
            row=1,
            column=0,
            sticky="w",
            pady=(self._scaled_px(4), 0),
        )

        # Retained as the nonvisual stable-identity selection model used by tray actions.
        self.monitor_combo = ttk.Combobox(
            self.main_frame,
            textvariable=self.monitor_var,
            state="readonly",
        )
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_selected)

        self.pages = ttk.Frame(self.main_frame, style="Content.TFrame")
        self.pages.grid(row=1, column=0, sticky="nsew")
        self.pages.columnconfigure(0, weight=1)
        self.pages.rowconfigure(0, weight=1)
        self.routes_panel = ttk.Frame(self.pages, style="Content.TFrame")
        self.routes_panel.grid(row=0, column=0, sticky="nsew")
        self.plugins_panel = ttk.Frame(self.pages, style="Content.TFrame")
        self.plugins_panel.grid(row=0, column=0, sticky="nsew")
        self.integrations_panel = ttk.Frame(self.pages, style="Content.TFrame")
        self.integrations_panel.grid(row=0, column=0, sticky="nsew")
        self.appearance_panel = ttk.Frame(self.pages, style="Content.TFrame")
        self.appearance_panel.grid(row=0, column=0, sticky="nsew")
        self.settings_panel = ttk.Frame(self.pages, style="Content.TFrame")
        self.settings_panel.grid(row=0, column=0, sticky="nsew")

        self.log_button = ttk.Button(
            self.sidebar,
            text="Diagnostics",
            style="Nav.TButton",
            command=self.show_diagnostic_log,
        )
        self.log_button.grid(row=6, column=0, sticky="ew")

        startup_card = ttk.LabelFrame(
            self.settings_panel,
            text="Startup",
            style="Card.TLabelframe",
            padding=16,
        )
        startup_card.grid(row=0, column=0, sticky="ew")
        startup_card.columnconfigure(0, weight=1)
        ttk.Label(
            startup_card,
            text="Start with Windows",
            style="Card.TLabel",
            font=("Segoe UI Variable", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            startup_card,
            text="Launch quietly in the notification area when you sign in.",
            style="CardMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(self._scaled_px(3), 0))
        self.start_with_windows_button = ttk.Button(
            startup_card,
            text="On" if self.start_with_windows_var.get() else "Off",
            command=self._toggle_start_with_windows_from_button,
            style="Toggle.TButton",
        )
        self.start_with_windows_button.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(self._scaled_px(18), 0),
        )

        self.configuration_actions = ttk.LabelFrame(
            self.settings_panel,
            text="Configuration",
            style="Card.TLabelframe",
            padding=16,
        )
        self.configuration_actions.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(self._scaled_px(14), 0),
        )
        self.configuration_actions.columnconfigure(0, weight=1)
        ttk.Label(
            self.configuration_actions,
            text="Export a backup, import one, or restore the bundled defaults.",
            style="CardMuted.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, self._scaled_px(12)))
        ttk.Button(
            self.configuration_actions,
            text="Export",
            style="Accent.TButton",
            command=self.export_configuration,
        ).grid(row=1, column=0, sticky="w")
        ttk.Button(
            self.configuration_actions,
            text="Import",
            style="Quiet.TButton",
            command=self.import_configuration,
        ).grid(row=1, column=1, sticky="w", padx=(self._scaled_px(8), 0))
        self.import_history_button = ttk.Button(
            self.configuration_actions,
            text="Recent",
            style="Quiet.TButton",
            command=self.show_import_history,
            takefocus=False,
        )
        self.import_history_button.grid(
            row=1,
            column=2,
            sticky="w",
            padx=(self._scaled_px(8), 0),
        )
        ttk.Button(
            self.configuration_actions,
            text="Restore default",
            style="Quiet.TButton",
            command=self.import_default_configuration,
        ).grid(row=2, column=0, sticky="w", pady=(self._scaled_px(10), 0))
        self.settings_panel.columnconfigure(0, weight=1)

        self.status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            relief=tk.SUNKEN,
            bd=1,
            padx=self._scaled_px(6),
        )
        self.status_bar.grid(row=1, column=0, sticky="ew")
        self._show_page("routes")

    def _scaled_px(self, value: int) -> int:
        return max(1, round(value * self._ui_dpi / 96))

    def _apply_scaled_layout(self) -> None:
        configure_style_metrics(self.style, self._ui_dpi)
        self.sidebar.configure(padding=(self._scaled_px(14), self._scaled_px(22)))
        self.sidebar.grid_propagate(True)
        self.content_frame.columnconfigure(0, minsize=self._scaled_px(190))
        self.main_frame.configure(padding=(self._scaled_px(28), self._scaled_px(24)))
        self.page_header.grid_configure(pady=(0, self._scaled_px(18)))
        self.navigation.grid_configure(pady=(self._scaled_px(24), 0))
        self.configuration_actions.grid_configure(pady=(self._scaled_px(14), 0))
        self.status_bar.configure(padx=self._scaled_px(10), pady=self._scaled_px(4))

    def _show_page(self, page: str) -> None:
        pages = {
            "routes": (
                self.routes_panel,
                self.route_nav_button,
                "Audio routes",
                "Send each input to the output you want to control.",
            ),
            "plugins": (
                self.plugins_panel,
                self.plugin_nav_button,
                "Automations",
                "Build ordered steps and choose how they run.",
            ),
            "integrations": (
                self.integrations_panel,
                self.integrations_nav_button,
                "Integrations",
                "Configure shared connections used by routes and automations.",
            ),
            "appearance": (
                self.appearance_panel,
                self.appearance_nav_button,
                "Appearance",
                "Choose how volume changes are presented on screen.",
            ),
            "settings": (
                self.settings_panel,
                self.settings_nav_button,
                "Settings",
                "Manage startup behavior and configuration backups.",
            ),
        }
        selected_page, selected_button, title, subtitle = pages.get(page, pages["routes"])
        for candidate_page, candidate_button, _title, _subtitle in pages.values():
            if candidate_page is selected_page:
                candidate_page.grid()
                candidate_button.configure(style="Selected.Nav.TButton")
            else:
                candidate_page.grid_remove()
                candidate_button.configure(style="Nav.TButton")
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)

    def show_diagnostic_log(self) -> None:
        """Open the bounded diagnostic history without mutating its files."""
        LOGGER.info("Diagnostic log viewer opened.")
        if self._log_window is None or not self._log_window.winfo_exists():
            self._create_diagnostic_log_window()
        else:
            self._log_window.deiconify()
            self._log_window.lift()
        self._refresh_diagnostic_log()
        self._schedule_diagnostic_log_refresh()
        if self._log_window is not None:
            self._log_window.focus_set()

    def export_configuration(self) -> None:
        directory = configuration_directory()
        filename = f"FenSoundSwitch-{datetime.now():%Y%m%d-%H%M%S-%f}{ARCHIVE_EXTENSION}"
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export FenSoundSwitch configuration",
            initialdir=directory,
            initialfile=filename,
            defaultextension=ARCHIVE_EXTENSION,
            filetypes=[("FenSoundSwitch configuration", f"*{ARCHIVE_EXTENSION}"), ("All files", "*.*")],
        )
        if not destination:
            return
        try:
            archive = Path(destination)
            export_configuration(archive)
            history_archive = add_to_import_history(archive)
        except ConfigurationArchiveError as exc:
            LOGGER.warning("Configuration export failed (%s).", exc.__class__.__name__)
            self._set_status(str(exc))
            return
        if history_archive == archive:
            self._set_status(f"Configuration exported to {archive}.")
        else:
            self._set_status(f"Configuration exported to {archive} and added to import history.")

    def import_configuration(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="Import FenSoundSwitch configuration",
            initialdir=configuration_directory(),
            filetypes=[("FenSoundSwitch configuration", f"*{ARCHIVE_EXTENSION}"), ("All files", "*.*")],
        )
        if source:
            self._confirm_import_configuration(Path(source))

    def show_import_history(self) -> None:
        configurations = recent_configurations()
        if not configurations:
            self._set_status("No exported configuration is available.")
            return
        menu = tk.Menu(self.root, tearoff=False)
        for source in configurations:
            menu.add_command(
                label=source.name,
                command=lambda selected=source: self._confirm_import_configuration(selected),
            )
        try:
            menu.tk_popup(
                self.import_history_button.winfo_rootx(),
                self.import_history_button.winfo_rooty() + self.import_history_button.winfo_height(),
            )
        finally:
            menu.grab_release()

    def import_default_configuration(self) -> None:
        source = configuration_directory() / DEFAULT_ARCHIVE_NAME
        if not source.is_file():
            self._set_status(f"Default configuration not found: {source}.")
            return
        self._confirm_import_configuration(source)

    def _confirm_import_configuration(self, source: Path) -> None:
        if not messagebox.askyesno(
            "Restart FenSoundSwitch?",
            f"Importing {source.name} will close and restart FenSoundSwitch. Continue?",
            parent=self.root,
        ):
            return
        try:
            import_configuration(source)
        except ConfigurationArchiveError as exc:
            LOGGER.warning("Configuration import failed (%s).", exc.__class__.__name__)
            self._set_status(str(exc))
            return
        self._set_status(f"Configuration imported from {source.name}. Restarting FenSoundSwitch...")
        self.restart_requested = True
        self.root.after_idle(self.on_close)

    def _create_diagnostic_log_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("FenSoundSwitch diagnostic log")
        window.minsize(self._scaled_px(600), self._scaled_px(360))
        window.geometry(f"{self._scaled_px(820)}x{self._scaled_px(560)}")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_diagnostic_log)
        apply_window_chrome(window, self.dark_mode)

        content = ttk.Frame(
            window,
            padding=self._scaled_px(20),
            style="Dialog.TFrame",
        )
        content.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        ttk.Label(
            content,
            text="Diagnostic log",
            style="DialogTitle.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            content,
            text="Live application events. Sensitive credentials are never written here.",
            style="DialogSubtitle.TLabel",
        ).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(self._scaled_px(4), self._scaled_px(14)),
        )

        text = tk.Text(
            content,
            wrap="none",
            state="disabled",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            padx=self._scaled_px(10),
            pady=self._scaled_px(10),
            font=("Cascadia Mono", 9),
        )
        vertical_scrollbar = ttk.Scrollbar(content, orient="vertical", command=text.yview)
        horizontal_scrollbar = ttk.Scrollbar(content, orient="horizontal", command=text.xview)
        text.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        text.grid(row=2, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=2, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=3, column=0, sticky="ew")
        self._style_log_text(text)

        actions = ttk.Frame(content)
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(self._scaled_px(14), 0))
        ttk.Button(actions, text="Refresh", style="Accent.TButton", command=self._refresh_diagnostic_log).grid(row=0, column=0)
        ttk.Button(actions, text="Close", style="Quiet.TButton", command=self._close_diagnostic_log).grid(
            row=0,
            column=1,
            padx=(self._scaled_px(8), 0),
        )
        self._log_window = window
        self._log_text = text

    def _style_log_text(self, text: tk.Text) -> None:
        if self.high_contrast:
            background = LIGHT_LIST_BG
            foreground = LIGHT_TEXT
            border = LIGHT_TEXT
        elif self.dark_mode:
            background = DARK_SURFACE
            foreground = DARK_TEXT
            border = DARK_BORDER
        else:
            background = LIGHT_LIST_BG
            foreground = LIGHT_TEXT
            border = LIGHT_BORDER
        text.configure(
            bg=background,
            fg=foreground,
            insertbackground=foreground,
            highlightbackground=border,
            highlightcolor=border,
            selectbackground="#3A7BD5" if self.dark_mode else "SystemHighlight",
            selectforeground=DARK_TEXT if self.dark_mode else "SystemHighlightText",
        )

    def _refresh_diagnostic_log(self, record_event: bool = True) -> None:
        if self._log_text is None:
            return
        if record_event:
            LOGGER.info("Diagnostic log viewer refreshed.")
        contents = read_log_contents()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.insert("1.0", contents)
        self._log_text.configure(state="disabled")
        self._log_text.yview_moveto(1.0)

    def _schedule_diagnostic_log_refresh(self) -> None:
        if self._closing or self._log_window is None or self._log_refresh_after_id is not None:
            return
        self._log_refresh_after_id = self.root.after(
            self.LOG_VIEW_REFRESH_MS,
            self._refresh_diagnostic_log_live,
        )

    def _refresh_diagnostic_log_live(self) -> None:
        self._log_refresh_after_id = None
        if self._log_window is None or not self._log_window.winfo_exists():
            return
        self._refresh_diagnostic_log(record_event=False)
        self._schedule_diagnostic_log_refresh()

    def _close_diagnostic_log(self) -> None:
        window = self._log_window
        self._log_window = None
        self._log_text = None
        if self._log_refresh_after_id is not None:
            self.root.after_cancel(self._log_refresh_after_id)
            self._log_refresh_after_id = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def _lock_window_size(self) -> None:
        self._apply_scaled_layout()
        self._resize_for_content(force=True)
        self.root.resizable(True, True)

    def _resize_for_content(self, force: bool = False) -> None:
        self.root.update_idletasks()
        width = max(self.root.winfo_reqwidth(), self._scaled_px(self.BASE_WINDOW_MIN_WIDTH))
        height = self.root.winfo_reqheight()
        self.root.minsize(width, height)
        current_width = self.root.winfo_width()
        current_height = self.root.winfo_height()
        if force or current_width < width or current_height < height:
            self.root.geometry(f"{width if force else max(current_width, width)}x{height if force else max(current_height, height)}")

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
        bindings = {"<Escape>": self._minimize_from_keyboard}
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
            self.refresh_configured_routes()
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
        manager: Any | None = None
        try:
            from plugin_manager import PluginManager

            manager = PluginManager(
                self.root,
                post_to_ui=self._post_to_ui,
                on_notice=self._set_status,
                get_start_with_windows=lambda: self.start_with_windows,
                set_start_with_windows=self.set_start_with_windows_enabled,
                get_volume_statuses=self._get_volume_statuses,
                on_overlay_renderer_changed=self._replace_overlay_renderer,
                on_overlay_text=self._show_plugin_overlay_text,
                on_overlay_preview=self._show_overlay_preview,
                on_volume_routes_changed=self._routes_changed,
                on_route_input=self._queue_route_input_delta,
                on_route_volume=self._queue_route_input_volume,
                on_route_key=self._queue_route_input_key,
                on_action_signals_changed=self._sync_tray_menu_state,
            )
            manager.start()
            self._plugin_manager = manager
            self._sync_tray_menu_state()
            self._overlay = manager.create_overlay_renderer(
                self.dark_mode,
                self.high_contrast,
            )
            if not self._web_only:
                manager.build_routes_panel(self.routes_panel)
                manager.build_action_plugins_panel(self.integrations_panel)
                manager.build_appearance_panel(self.appearance_panel)
            manager.dispatch_startup_automations()
            # Panels are added after the initial window-size lock. Measure them
            # now so a tray-first window never opens at the pre-plugin height.
            if not self._web_only:
                self._resize_for_content(force=True)
        except Exception as exc:
            LOGGER.error("Plugin system startup failed (%s).", exc.__class__.__name__)
            self._plugin_manager = None
            if manager is not None:
                try:
                    manager.stop(2.0)
                except Exception:
                    pass
            message = (
                f"Plugin system failed: {self._format_error(exc)}\n\n"
                "Volume routes are unavailable. Restart the app after correcting the Routes or plugin configuration."
            )
            self._set_status(message)
            if not self._web_only:
                ttk.Label(
                    self.routes_panel,
                    text=message,
                    justify="left",
                    wraplength=self._scaled_px(560),
                ).grid(sticky="ew", padx=self._scaled_px(12), pady=self._scaled_px(12))

    def _on_volume_provider_changed(self, provider: Any | None, _plugin_id: str | None) -> None:
        if self._closing or provider is None:
            return
        if hasattr(self, "provider_var"):
            self.provider_var.set(f"Volume provider: {provider.provider_name}")
        self._publish_volume_status(provider, None, "Not yet read")
        self._post_to_ui(self.refresh_volume_provider)

    def _start_tray_icon(self) -> None:
        try:
            self._tray_icon = TrayIconController(
                tooltip=self.TRAY_TOOLTIP,
                on_restore=lambda: self._post_to_ui(self.restore_from_tray),
                on_exit=lambda: self._post_to_ui(self.on_close),
                on_error=self._handle_tray_error_from_thread,
                icon_path=self.app_icon_path,
                on_refresh=lambda: self._post_to_ui(self.refresh_configured_routes),
                on_select_monitor=lambda selection: self._post_to_ui(
                    lambda target=selection: self._select_monitor_from_tray(target)
                ),
                on_signal=lambda signal_id: self._post_to_ui(
                    lambda target=signal_id: self._dispatch_tray_signal(target)
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

    def _start_web_presentation(self) -> None:
        self._presentation_revision = 0
        self._presentation_requested_visible = False
        self._presentation = WebPresentationController(
            Path(__file__).resolve().with_name("web_ui_host.py"),
            post_to_ui=self._post_to_ui,
            get_snapshot=self._web_snapshot,
            dispatch_action=self._dispatch_web_action,
            allowed_actions={"route.save", "route.endpoint-form", "route.delete", "signal.save", "signal.delete", "signal.run", "slot.ui", "slot.action", "slot.save", "mqtt.profile.save", "mqtt.profile.delete", "action.save", "plugin.action", "appearance.save", "settings.save", "config.export", "config.import", "config.restore-default", "diagnostics.refresh"},
            on_exit=self.on_close,
            on_minimize=self._on_web_minimize,
            on_visibility=self._on_web_visibility,
            on_tray_recovery=self._recover_web_tray,
            on_child_failure=lambda reason: self._set_status(f"Command center unavailable: {reason}"),
        )
        if self._tray_icon is None:
            if not self._presentation.launch():
                if sys.platform == "darwin":
                    # The command center is the only macOS primary UI. Do not
                    # expose the legacy Tk shell when its WebKit child fails.
                    self.root.after_idle(self.on_close)
                    return
                self.root.deiconify()
                self.root.state("normal")

    def _web_snapshot(self) -> PresentationSnapshot:
        self._presentation_revision += 1
        manager = self._plugin_manager
        statuses = {item.provider_id: item for item in self._get_volume_statuses()}
        inputs = [{"label": r.input_name, "value": r.input_id, "description": r.description} for r in manager.records if r.initialized and r.input_id]
        outputs = [{"label": r.name, "value": r.plugin_id, "description": r.description} for r in manager.records if r.initialized and r.is_volume_provider and r.plugin_id]
        routes = []
        for route in manager.input_routes:
            status = statuses.get(route.route_id)
            input_name = next((v["label"] for v in inputs if v["value"] == route.input_id), route.input_id)
            output_name = next((v["label"] for v in outputs if v["value"] == route.provider_id), route.provider_id)
            route_fields = [{"key": "name", "type": "text", "label": "Route name", "required": True}, {"key": "input_id", "type": "select", "label": "Input", "required": True, "options": inputs}, {"key": "provider_id", "type": "select", "label": "Output", "required": True, "options": outputs}]
            route_values: dict[str, object] = {"name": route.name, "input_id": route.input_id, "provider_id": route.provider_id}
            for endpoint in ("input", "output"):
                try: document = manager.get_route_ui(route.route_id, endpoint)
                except Exception: document = None
                if document is None: continue
                for field in document["fields"]:
                    assert isinstance(field, dict)
                    key = f"{endpoint}__{field['id']}"; type_map = {"integer": "number", "choice": "select"}
                    route_fields.append({"key": key, "type": type_map.get(str(field["type"]), field["type"]), "label": f"{endpoint.title()}: {field['label']}", "required": field.get("required", False), "min": field.get("minimum"), "max": field.get("maximum"), "options": field.get("options", [])})
                    if not field.get("write_only"): route_values[key] = field.get("value")
            routes.append({"id": route.route_id, "name": route.name, "description": "Audio volume route", "input": {"name": input_name, "summary": ""}, "output": {"name": output_name, "summary": ""}, "summary": f"{status.confirmed_volume}%" if status and status.confirmed_volume is not None else (status.reason if status else "Checking"), "enabled": bool(status and status.confirmed_volume is not None), "values": route_values, "form": {"method": "route.save", "fields": route_fields}})
        integrations = []
        for record in manager.records:
            if not manager._is_action_plugin(record) or not record.plugin_id or not callable(getattr(record.plugin, "get_plugin_ui", None)):
                continue
            try:
                document = manager.get_plugin_ui(record.plugin_id)
            except Exception as exc:
                record.status = f"Configuration unavailable: {exc}"
                document = None
            fields = [{"key": "enabled", "type": "boolean", "label": "Enabled", "default": record.enabled}]
            values: dict[str, object] = {"enabled": record.enabled}
            ui_action = ""
            if document is not None:
                type_map = {"integer": "number", "choice": "select"}
                fields = []
                for field in document["fields"]:
                    assert isinstance(field, dict)
                    fields.append({"key": field["id"], "type": type_map.get(str(field["type"]), field["type"]), "label": field["label"], "required": field.get("required", False), "min": field.get("minimum"), "max": field.get("maximum"), "options": field.get("options", []), "depends_on": field.get("depends_on")})
                    if not field.get("write_only"): values[str(field["id"])] = field.get("value")
                submit = next((item for item in document["actions"] if isinstance(item, dict) and item.get("kind") == "submit"), None)
                ui_action = str(submit.get("id", "")) if isinstance(submit, dict) else ""
            integrations.append({"id": record.plugin_id, "name": record.name, "description": record.display_status, "values": values, "ui_actions": document["actions"] if document else [], "form": {"method": "action.save", "ui_action": ui_action, "description": document.get("description", "") if document else "", "fields": fields}})
        signal_options = list(manager.signal_action_options())
        try:
            mqtt_profile_options = list(manager.mqtt_profile_options())
        except Exception:
            mqtt_profile_options = []
        trigger_field = {
            "key": "triggers",
            "type": "trigger-list",
            "label": "Triggers",
            "options": [
                {"value": "app-start", "label": "App start", "description": "Runs once when the primary application has finished starting."},
                {"value": "keyboard", "label": "Key press", "description": "Runs when the configured global key combination is pressed."},
                {"value": "tray", "label": "Tray menu option", "description": "Adds a named command under Automations in the notification-area menu."},
                {"value": "mqtt", "label": "MQTT / Home Assistant", "description": "Exposes a Home Assistant MQTT button that runs this automation when pressed."},
            ],
            "mqtt_profiles": mqtt_profile_options,
        }
        signal_form_fields = [
            {"key": "name", "type": "text", "label": "Automation name", "required": True, "max_length": 80},
            trigger_field,
            {"key": "slots", "type": "sequence", "label": "Ordered steps", "options": signal_options},
        ]
        signals = []
        for signal in manager.action_signals:
            signal_options_for_form = list(signal_options)
            slot_values = []
            slot_labels = []
            for slot in signal.slots:
                if hasattr(slot, "milliseconds"):
                    milliseconds = int(slot.milliseconds)
                    slot_values.append({"kind": "wait", "milliseconds": milliseconds})
                    slot_labels.append(f"wait {milliseconds / 1000:g}s")
                else:
                    target = f"{slot.plugin_id}/{slot.action_id}"
                    if not any(option["value"] == target for option in signal_options_for_form):
                        signal_options_for_form.append({"label": f"Unavailable: {target}", "value": target, "configurable": False, "disabled": True})
                    label = next((str(option["label"]) for option in signal_options if option["value"] == target), target)
                    summary = manager.slot_summary(slot.plugin_id, slot.action_id, slot.parameters)
                    slot_values.append({"kind": "action", "target": target, "parameters": slot.parameters, "summary": summary})
                    slot_labels.append(f"{label} ({summary})" if summary else label)
            trigger_labels = []
            if signal.hotkey.hotkey is not None: trigger_labels.append(signal.hotkey.hotkey.label)
            if signal.tray_label is not None: trigger_labels.append(f"Tray: {signal.tray_label}")
            if signal.on_start: trigger_labels.append("App start")
            mqtt_trigger = next((trigger for trigger in signal.plugin_triggers if trigger.plugin_id == "mqtt-input" and trigger.trigger_id == "mqtt-ha"), None)
            if mqtt_trigger is not None: trigger_labels.append("MQTT / Home Assistant")
            mqtt_parameters = mqtt_trigger.parameters if mqtt_trigger is not None else {}
            trigger_values = []
            if signal.on_start:
                trigger_values.append({"kind": "app-start"})
            if signal.hotkey.hotkey is not None:
                trigger_values.append({"kind": "keyboard", "hotkey": signal.hotkey.hotkey.to_json(), "forward_keys": signal.hotkey.forward_keys})
            if signal.tray_label is not None:
                trigger_values.append({"kind": "tray", "label": signal.tray_label})
            if mqtt_trigger is not None:
                trigger_values.append({"kind": "mqtt", "profile_id": mqtt_parameters.get("profile_id", ""), "ha_name": mqtt_parameters.get("ha_name", signal.name), "ha_id": mqtt_parameters.get("ha_id", "")})
            fields = [dict(signal_form_fields[0]), dict(trigger_field), {"key": "slots", "type": "sequence", "label": "Ordered steps", "options": signal_options_for_form}]
            signals.append({"id": signal.signal_id, "name": signal.name, "description": " then ".join(slot_labels), "steps": slot_labels, "trigger": " + ".join(trigger_labels) or "No trigger", "values": {"name": signal.name, "triggers": trigger_values, "slots": slot_values}, "form": {"method": "signal.save", "fields": fields}})
        renderers = []
        for record in manager.records:
            if not record.initialized or not record.is_overlay_renderer or not record.plugin_id: continue
            try:
                document = manager.get_plugin_ui(record.plugin_id)
            except ValueError:
                document = None
            fields = []; values = {}
            if document:
                type_map = {"integer": "number", "choice": "select"}
                for field in document["fields"]:
                    if not isinstance(field, dict): continue
                    fields.append({"key": field["id"], "type": type_map.get(str(field["type"]), field["type"]), "label": field["label"], "required": field.get("required", False), "options": field.get("options", [])})
                    if not field.get("write_only"): values[str(field["id"])] = field.get("value")
            renderers.append({"id": record.plugin_id, "name": record.name, "description": record.display_status, "selected": record.plugin_id == manager._active_overlay_plugin_id, "values": values, "ui_actions": document["actions"] if document else [], "form": {"method": "action.save", "fields": fields} if fields else None})
        diagnostic_text = read_log_contents()
        if len(diagnostic_text) > 12000: diagnostic_text = diagnostic_text[-12000:]
        recent_archives = [{"name": path.stem, "path": str(path)} for path in recent_configurations()]
        signal_form = {"method": "signal.save", "fields": signal_form_fields}
        def mqtt_profile_entity(profile_id: str | None) -> dict[str, object]:
            document = manager.get_mqtt_profile_ui(profile_id)
            profile_fields = []
            profile_values: dict[str, object] = {}
            type_map = {"integer": "number", "choice": "select"}
            for field in document["fields"]:
                if not isinstance(field, dict): continue
                profile_fields.append({"key": field["id"], "type": type_map.get(str(field["type"]), field["type"]), "label": field["label"], "required": field.get("required", False), "min": field.get("minimum"), "max": field.get("maximum"), "options": field.get("options", []), "default": field.get("value")})
                if not field.get("write_only"): profile_values[str(field["id"])] = field.get("value")
            return {"values": profile_values, "form": {"method": "mqtt.profile.save", "ui_action": "save", "fields": profile_fields}}
        mqtt_profiles = []
        mqtt_profile_form = None
        try:
            mqtt_profile_form = mqtt_profile_entity(None)["form"]
            for profile in manager.mqtt_profiles():
                profile_id = str(profile.get("profile_id", ""))
                entity = mqtt_profile_entity(profile_id)
                mqtt_profiles.append({"id": profile_id, "name": str(profile.get("name", profile_id)), "description": f"{profile.get('host', '')}:{profile.get('port', '')}", **entity})
        except ValueError:
            pass
        startup_label = "Start at Login" if sys.platform == "darwin" else "Start with Windows"
        startup_description = (
            "Launch FenSoundSwitch when you sign in."
            if sys.platform == "darwin"
            else "Launch quietly in the notification area when you sign in."
        )
        snapshot = {"presentation": {"visible": self._presentation_requested_visible}, "application": {"name": "FenSoundSwitch", "version": APP_VERSION}, "routes": routes, "signals": signals, "integrations": integrations, "mqtt_profiles": mqtt_profiles, "appearance": {"renderers": renderers}, "settings": {"start_with_windows": bool(self.start_with_windows_var.get()), "startup_label": startup_label, "startup_description": startup_description, "configuration_directory": str(configuration_directory()), "recent_configurations": recent_archives}, "diagnostics": {"status": "Ready", "summary": self.status_var.get(), "text": diagnostic_text}, "forms": {"route": {"method": "route.save", "fields": [{"key": "name", "type": "text", "label": "Route name", "required": True}, {"key": "input_id", "type": "select", "label": "Input", "required": True, "options": inputs}, {"key": "provider_id", "type": "select", "label": "Output", "required": True, "options": outputs}]}, "signal": signal_form, "mqtt_profile": mqtt_profile_form}}
        return PresentationSnapshot(self._presentation_revision, snapshot)

    def _dispatch_web_action(self, action: str, arguments: Mapping[str, Any]) -> Any:
        manager = self._plugin_manager
        response: dict[str, object] | None = None
        if action == "route.endpoint-form":
            endpoint = arguments.get("endpoint")
            plugin_id = arguments.get("plugin_id")
            parameters = arguments.get("parameters", {})
            if endpoint not in {"input", "output"} or not isinstance(plugin_id, str) or not isinstance(parameters, dict):
                raise UserActionError("Route endpoint configuration is invalid.")
            return {"document": manager.get_new_route_endpoint_ui(plugin_id, endpoint, parameters)}
        if action == "route.save":
            values = arguments.get("values", {})
            if not isinstance(values, dict): raise ValueError("Route values are invalid.")
            route_id = arguments.get("id")
            if isinstance(route_id, str):
                existing = next((route for route in manager.input_routes if route.route_id == route_id), None)
                if existing is None: raise ValueError("The route no longer exists.")
                input_parameters = existing.input.parameters if values.get("input_id") == existing.input_id else {}
                output_parameters = existing.output.parameters if values.get("provider_id") == existing.provider_id else {}
                for endpoint in ("input", "output"):
                    if endpoint == "input" and values.get("input_id") != existing.input_id: continue
                    if endpoint == "output" and values.get("provider_id") != existing.provider_id: continue
                    document = manager.get_route_ui(route_id, endpoint)
                    if document is None: continue
                    submit = next((item for item in document["actions"] if isinstance(item, dict) and item.get("kind") == "submit"), None)
                    if submit is None: continue
                    submitted = {str(field["id"]): values.get(f"{endpoint}__{field['id']}") for field in document["fields"] if isinstance(field, dict)}
                    result = manager.invoke_route_ui_action(route_id, endpoint, str(submit["id"]), submitted)
                    if result.get("status") == "save":
                        if endpoint == "input": input_parameters = dict(result["values"])
                        else: output_parameters = dict(result["values"])
                ok = manager.update_route(route_id, str(values.get("input_id", "")), str(values.get("provider_id", "")), str(values.get("name", "")), input_parameters, output_parameters)
            else:
                input_id = str(values.get("input_id", ""))
                provider_id = str(values.get("provider_id", ""))
                input_record = next(
                    (record for record in manager.records if record.input_id == input_id),
                    None,
                )
                if input_record is None or not input_record.initialized:
                    raise UserActionError("The selected route input is unavailable.")
                provider_record = next(
                    (record for record in manager.records if record.plugin_id == provider_id),
                    None,
                )
                if (
                    provider_record is None
                    or not provider_record.initialized
                    or not provider_record.is_volume_provider
                ):
                    raise UserActionError("The selected volume output is unavailable.")
                raw_name = values.get("name", "")
                name = raw_name.strip() if isinstance(raw_name, str) else ""
                route_ids_before = {route.route_id for route in manager.input_routes}
                input_parameters = values.get("input_parameters", {})
                output_parameters = values.get("output_parameters", {})
                if not isinstance(input_parameters, dict) or not isinstance(output_parameters, dict):
                    raise UserActionError("Route configuration is invalid.")
                input_parameters = manager.validate_new_route_endpoint(input_id, "input", input_parameters)
                output_parameters = manager.validate_new_route_endpoint(provider_id, "output", output_parameters)
                ok = manager.add_route(input_id, provider_id, name or None, input_parameters, output_parameters)
                if ok:
                    created_route = next(
                        (route for route in manager.input_routes if route.route_id not in route_ids_before),
                        None,
                    )
                    if created_route is not None:
                        response = {"route_id": created_route.route_id}
            if not ok:
                raise UserActionError("The route could not be saved. Check the route name and configuration directory.")
        elif action == "route.delete":
            if not manager.remove_route(str(arguments.get("id", ""))): raise ValueError("The route could not be removed.")
        elif action == "signal.save":
            values = arguments.get("values", {})
            if not isinstance(values, dict): raise ValueError("Automation values are invalid.")
            if not isinstance(values.get("slots"), list) or not values["slots"]:
                raise UserActionError("Add at least one action or wait step.")
            triggers = values.get("triggers")
            if not isinstance(triggers, list) or not triggers or len(triggers) > 4:
                raise UserActionError("Choose at least one trigger.")
            seen_kinds: set[str] = set()
            on_start = False
            hotkey = None
            forward_keys = True
            tray_label = None
            plugin_triggers = []
            for trigger in triggers:
                if not isinstance(trigger, dict) or not isinstance(trigger.get("kind"), str):
                    raise UserActionError("Automation triggers are invalid.")
                kind = trigger["kind"]
                if kind in seen_kinds or kind not in {"app-start", "keyboard", "tray", "mqtt"}:
                    raise UserActionError("Each trigger type can be added only once.")
                seen_kinds.add(kind)
                if kind == "app-start":
                    on_start = True
                elif kind == "keyboard":
                    hotkey = trigger.get("hotkey")
                    forward_keys = trigger.get("forward_keys", True)
                    if hotkey is None:
                        raise UserActionError("Press the key combination that should run this automation.")
                    if not isinstance(forward_keys, bool):
                        raise UserActionError("The keyboard forwarding setting is invalid.")
                elif kind == "tray":
                    tray_label = trigger.get("label")
                    if not isinstance(tray_label, str) or not tray_label.strip():
                        raise UserActionError("Enter the tray menu option text.")
                else:
                    mqtt_values = (trigger.get("profile_id"), trigger.get("ha_name"), trigger.get("ha_id"))
                    if not all(isinstance(value, str) and value.strip() for value in mqtt_values):
                        raise UserActionError("Choose an MQTT/HA configuration and enter the Home Assistant name and ID.")
                    plugin_triggers.append({"target": "mqtt-input/mqtt-ha", "parameters": {"profile_id": mqtt_values[0], "ha_name": mqtt_values[1], "ha_id": mqtt_values[2]}})
            signal_id = arguments.get("id")
            if signal_id is not None and not isinstance(signal_id, str): raise ValueError("Automation ID is invalid.")
            if not manager.save_action_signal(signal_id, values.get("name"), hotkey, forward_keys, tray_label, values.get("slots"), on_start, plugin_triggers):
                raise ValueError("The automation could not be saved.")
        elif action == "signal.delete":
            if not manager.remove_action_signal(str(arguments.get("id", ""))): raise ValueError("The automation could not be removed.")
        elif action == "signal.run":
            manager.dispatch_action_signal(str(arguments.get("id", "")))
        elif action == "slot.ui":
            target = arguments.get("target")
            parameters = arguments.get("parameters", {})
            if not isinstance(target, str) or not isinstance(parameters, dict):
                raise ValueError("Automation step configuration is invalid.")
            plugin_id, separator, action_id = target.partition("/")
            if not separator:
                raise ValueError("Automation step target is invalid.")
            document = manager.get_slot_ui(plugin_id, action_id, parameters)
            if document is None:
                raise ValueError("That automation step has no configuration.")
            type_map = {"integer": "number", "choice": "select"}
            fields = []
            values: dict[str, object] = {}
            for field in document["fields"]:
                if not isinstance(field, dict):
                    continue
                fields.append({"key": field["id"], "type": type_map.get(str(field["type"]), field["type"]), "label": field["label"], "required": field.get("required", False), "min": field.get("minimum"), "max": field.get("maximum"), "options": field.get("options", []), "depends_on": field.get("depends_on")})
                if not field.get("write_only"):
                    values[str(field["id"])] = field.get("value")
            submit = next((item for item in document["actions"] if isinstance(item, dict) and item.get("kind") == "submit"), None)
            state = str(document.get("state", "ready"))
            if state == "ready" and not isinstance(submit, dict):
                raise ValueError("Automation step configuration has no save action.")
            return {"title": document["title"], "description": document.get("description"), "state": state, "fields": fields, "values": values, "actions": document["actions"], "action_id": submit["id"] if isinstance(submit, dict) else ""}
        elif action == "slot.action":
            target = arguments.get("target")
            values = arguments.get("values", {})
            ui_action_id = arguments.get("action_id")
            if not isinstance(target, str) or not isinstance(values, dict) or not isinstance(ui_action_id, str):
                raise ValueError("Automation step configuration is invalid.")
            plugin_id, separator, action_id = target.partition("/")
            if not separator:
                raise ValueError("Automation step target is invalid.")
            return manager.invoke_slot_ui_action(plugin_id, action_id, ui_action_id, values)
        elif action == "slot.save":
            target = arguments.get("target")
            values = arguments.get("values", {})
            ui_action_id = arguments.get("action_id")
            if not isinstance(target, str) or not isinstance(values, dict) or not isinstance(ui_action_id, str):
                raise ValueError("Automation step configuration is invalid.")
            plugin_id, separator, action_id = target.partition("/")
            if not separator:
                raise ValueError("Automation step target is invalid.")
            result = manager.invoke_slot_ui_action(plugin_id, action_id, ui_action_id, values)
            if result.get("status") != "save":
                raise ValueError("Automation step configuration was not saved.")
            parameters = result["values"]
            return {"parameters": parameters, "summary": manager.slot_summary(plugin_id, action_id, parameters), "message": result.get("message")}
        elif action == "mqtt.profile.save":
            values = arguments.get("values", {})
            profile_id = arguments.get("id")
            if not isinstance(values, dict) or (profile_id is not None and not isinstance(profile_id, str)):
                raise UserActionError("MQTT/HA configuration values are invalid.")
            manager.invoke_mqtt_profile_ui(profile_id, "save", values)
            return {"saved": True}
        elif action == "mqtt.profile.delete":
            if not manager.remove_mqtt_profile(str(arguments.get("id", ""))):
                raise UserActionError("The MQTT/HA configuration is unavailable or still in use.")
        elif action == "action.save":
            values = arguments.get("values", {})
            plugin_id = str(arguments.get("id", ""))
            if not isinstance(values, dict): raise ValueError("Plugin values are invalid.")
            document = manager.get_plugin_ui(plugin_id)
            if document is None:
                manager.set_action_plugin_enabled(plugin_id, bool(values.get("enabled")))
            else:
                submit = next((item for item in document["actions"] if isinstance(item, dict) and item.get("kind") == "submit"), None)
                if not isinstance(submit, dict): raise ValueError("Plugin configuration has no save action.")
                manager.invoke_plugin_ui_action(plugin_id, str(submit["id"]), values)
        elif action == "appearance.save":
                if not manager.set_active_overlay_plugin_id(str(arguments.get("renderer_id", ""))): raise ValueError("The overlay renderer is unavailable.")
        elif action == "plugin.action":
            values = arguments.get("values", {})
            if not isinstance(values, dict): raise ValueError("Plugin values are invalid.")
            manager.invoke_plugin_ui_action(str(arguments.get("id", "")), str(arguments.get("action_id", "")), values)
        elif action == "settings.save": self.set_start_with_windows_enabled(bool(arguments.get("start_with_windows")))
        elif action == "config.export": export_configuration(Path(str(arguments.get("path", ""))))
        elif action == "config.import":
            import_configuration(Path(str(arguments.get("path", "")))); self.restart_requested = True; self.root.after_idle(self.on_close)
        elif action == "config.restore-default":
            import_configuration(configuration_directory() / DEFAULT_ARCHIVE_NAME); self.restart_requested = True; self.root.after_idle(self.on_close)
        elif action != "diagnostics.refresh": raise ValueError("Unknown command-center action.")
        return response or {"accepted": True}

    def _on_web_visibility(self, visible: bool) -> None:
        if visible:
            self._presentation_requested_visible = True
            self._in_tray = False

    def _on_web_minimize(self) -> None:
        self._presentation_requested_visible = False
        if self._tray_icon is not None:
            self._tray_icon.show(); self._in_tray = True

    def _recover_web_tray(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.show(); self._in_tray = True
        elif sys.platform == "darwin":
            # A child crash must not reveal the Tk-only legacy UI on macOS.
            self.root.after_idle(self.on_close)
        else:
            self.root.deiconify(); self.root.state("normal")

    def _start_keyboard_listener(self) -> None:
        self._listener = GlobalVolumeKeyListener(
            on_delta=self._queue_hotkey_delta,
            should_consume=self._should_consume_volume_keys,
            on_error=self._handle_listener_error_from_thread,
            step=1,
            on_unavailable=self._queue_unavailable_hotkey_notice,
            should_report_unavailable=self._should_report_unavailable_hotkey,
        )
        try:
            self._listener.start()
            # A route probe can complete while the hook is being installed.
            # Recompute after activation so that readiness is not stranded
            # until a later route refresh.
            self._update_hotkey_state()
        except Exception as exc:
            LOGGER.error("Volume-key listener startup failed (%s).", exc.__class__.__name__)
            self._listener = None
            self._set_status(self._format_error(exc))

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
        button = getattr(self, "start_with_windows_button", None)
        if button is not None:
            button.configure(text="On" if self.start_with_windows_var.get() else "Off")
        manager = getattr(self, "_plugin_manager", None)
        if manager is not None:
            manager.refresh_start_with_windows_controls()

    def _toggle_start_with_windows_from_button(self) -> None:
        self.start_with_windows_var.set(not bool(self.start_with_windows_var.get()))
        self.on_start_with_windows_toggled()
        self.start_with_windows_button.configure(
            text="On" if self.start_with_windows_var.get() else "Off"
        )
        manager = getattr(self, "_plugin_manager", None)
        if manager is not None:
            manager.refresh_start_with_windows_controls()

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
        log_text = getattr(self, "_log_text", None)
        if log_text is not None:
            self._style_log_text(log_text)
        log_window = getattr(self, "_log_window", None)
        if log_window is not None:
            try:
                if log_window.winfo_exists():
                    apply_window_chrome(log_window, self.dark_mode)
            except tk.TclError:
                pass
        if self._overlay is not None:
            self._render_overlay("apply_theme", self.dark_mode, self.high_contrast)
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
        self._cancel_route_repeats()
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
        getattr(self, "_ready_route_ids", set()).clear()
        self.current_volume = None
        self.target_volume = None
        self._pending_target_volume = None
        self._control_unavailable_reason = "Display configuration changed; revalidating configured routes."
        self._update_hotkey_state()
        self._set_displayed_volume(None)
        if self._listener is not None:
            self._listener.reset_unavailable_notice()
        self._set_status("Display configuration changed. Revalidating configured routes...")
        self._apply_control_state()
        self._refresh_retry_index = 0
        self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)

    def _handle_listener_error_from_thread(self, exc: Exception) -> None:
        self._post_to_ui(lambda error=exc: self._handle_listener_error(error))

    def _handle_listener_error(self, exc: Exception) -> None:
        LOGGER.error("Volume-key listener failed (%s).", exc.__class__.__name__)
        self._cancel_route_repeats()
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
        return (
            not self._closing
            and self._display_listener_available()
            and self._topology_valid.is_set()
            and (getattr(self, "_plugin_manager", None) is not None or self.selected_key is not None)
        )

    def _routes_changed(self) -> None:
        """Rebuild readiness from route instances; never fall back to legacy state."""
        self._ensure_relevant_volume_statuses()
        self._ready_route_ids.clear()
        self._hotkeys_ready = False
        self._topology_valid.clear()
        self._cancel_route_repeats()
        self._update_hotkey_state()
        self.refresh_configured_routes()

    def _queue_hotkey_delta(self, delta: int) -> None:
        if not self._closing:
            LOGGER.info("Volume-key input received: adjustment=%+d.", delta)
            self._hotkey_delta_queue.put(delta)

    def _queue_route_input_delta(self, route_id: str, delta: int) -> None:
        if not self._closing and isinstance(delta, int) and not isinstance(delta, bool) and -100 <= delta <= 100 and delta != 0:
            LOGGER.info("Route shortcut input received: route=%s, adjustment=%+d.", route_id, delta)
            self._route_input_queue.put((route_id, delta))

    def _queue_route_input_volume(self, route_id: str, volume: int) -> None:
        if not self._closing and isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100:
            LOGGER.info("Route input received: route=%s, target volume=%d.", route_id, volume)
            self._route_volume_queue.put((route_id, volume))

    def _queue_route_input_key(self, route_id: str, delta: int, pressed: bool) -> None:
        if not self._closing and delta in (-1, 1):
            LOGGER.info(
                "Route shortcut key %s: route=%s, adjustment=%+d.",
                "pressed" if pressed else "released",
                route_id,
                delta,
            )
            self._route_key_queue.put((route_id, delta, pressed))

    def _cancel_route_repeats(self, route_ids: set[str] | None = None) -> None:
        scheduler = getattr(self, "_route_repeat_scheduler", None)
        if scheduler is not None:
            scheduler.cancel(route_ids)
        pending = getattr(self, "_pending_route_deltas", None)
        if pending is not None:
            if route_ids is None:
                pending.clear()
            else:
                for route_id in route_ids:
                    pending.pop(route_id, None)
        optimistic = getattr(self, "_route_optimistic_targets", None)
        if optimistic is not None:
            if route_ids is None:
                optimistic.clear()
            else:
                for route_id in route_ids:
                    optimistic.pop(route_id, None)
        reconciliations = getattr(self, "_route_reconciliation_pending", None)
        if reconciliations is not None:
            if route_ids is None:
                reconciliations.clear()
            else:
                reconciliations.difference_update(route_ids)

    def _queue_unavailable_hotkey_notice(self) -> None:
        self._post_to_ui(self._show_unavailable_error)

    def _should_report_unavailable_hotkey(self) -> bool:
        return (
            not self._closing
            and self._control_unavailable_reason is not None
            and (self.selected_key is not None or self.preferred_selected_key is not None)
        )

    def _should_consume_volume_keys(self) -> bool:
        manager = getattr(self, "_plugin_manager", None)
        routed_providers = manager.volume_providers_for_input(self.WINDOWS_VOLUME_INPUT_ID) if manager is not None else ()
        ready_route_ids = getattr(self, "_ready_route_ids", None)
        return (
            self._hotkeys_enabled
            and self._topology_valid.is_set()
            and not self._closing
            and self._listener is not None
            and self._listener.is_active
            and any(ready_route_ids is None or route.route_id in ready_route_ids for route, _provider in routed_providers)
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

            pending_delta = getattr(self, "_pending_hotkey_delta", 0)
            if self._hotkeys_enabled:
                while True:
                    try:
                        pending_delta += self._hotkey_delta_queue.get_nowait()
                    except queue.Empty:
                        break
                self._pending_hotkey_delta = pending_delta
                if pending_delta and not self._busy:
                    self._pending_hotkey_delta = 0
                    try:
                        LOGGER.info("Volume-key input applied: adjustment=%+d.", pending_delta)
                        if getattr(self, "_plugin_manager", None) is None:
                            self.adjust_selected_volume(pending_delta)
                        else:
                            self._route_windows_volume_delta(pending_delta)
                    except Exception as exc:
                        self._report_ui_callback_error(exc)
            elif not self._hotkeys_enabled:
                self._pending_hotkey_delta = 0
                while True:
                    try:
                        self._hotkey_delta_queue.get_nowait()
                    except queue.Empty:
                        break
            # Passive keyboard route inputs are independent of the native
            # Windows Volume-key hook and remain available without its route.
            route_key_queue = getattr(self, "_route_key_queue", None)
            scheduler = getattr(self, "_route_repeat_scheduler", None)
            pending_routes = getattr(self, "_pending_route_deltas", None)
            if route_key_queue is not None and scheduler is not None and pending_routes is not None:
                while True:
                    try:
                        route_id, delta, pressed = route_key_queue.get_nowait()
                    except queue.Empty:
                        break
                    if not pressed:
                        self._route_reconciliation_pending.add(route_id)
                    for repeat_route_id, repeat_delta in scheduler.key_event(route_id, delta, pressed):
                        pending_routes[repeat_route_id] = pending_routes.get(repeat_route_id, 0) + repeat_delta
                for repeat_route_id, repeat_delta in scheduler.poll():
                    pending_routes[repeat_route_id] = pending_routes.get(repeat_route_id, 0) + repeat_delta
            route_input_queue = getattr(self, "_route_input_queue", None)
            if route_input_queue is not None and pending_routes is not None:
                while True:
                    try:
                        route_id, delta = route_input_queue.get_nowait()
                    except queue.Empty:
                        break
                    pending_routes[route_id] = pending_routes.get(route_id, 0) + delta
            route_volume_queue = getattr(self, "_route_volume_queue", None)
            if route_volume_queue is not None and not self._busy:
                try:
                    route_id, volume = route_volume_queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    LOGGER.info("Route input applied: route=%s, target volume=%d.", route_id, volume)
                    self._route_volume_delta((route_id,), 0, absolute_target=volume)
            if pending_routes is not None and not self._busy:
                for route_id, delta in tuple(pending_routes.items()):
                    del pending_routes[route_id]
                    if delta:
                        LOGGER.info("Route input applied: route=%s, adjustment=%+d.", route_id, delta)
                        self._route_volume_delta((route_id,), delta)
                    break
                else:
                    pending_reconciliations = self._route_reconciliation_pending
                    if pending_reconciliations:
                        route_id = pending_reconciliations.pop()
                        self._route_volume_delta((route_id,), 0, reconcile=True)
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
        LOGGER.info("Status bar: %s", message)

    def _set_widget_enabled(self, widget: ttk.Widget, enabled: bool) -> None:
        if enabled:
            widget.state(["!disabled"])
        else:
            widget.state(["disabled"])

    def _apply_control_state(self) -> None:
        # Route and action panels own their enabled states.  Main-window refresh is tray-only.
        return

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
            # The root no longer presents one authoritative volume. Keep this
            # compatibility helper for monitor/tray state only.
            if hasattr(self, "volume_var"):
                self.volume_var.set(0.0 if volume is None else float(clamp(volume, 0, 100)))
        finally:
            self._ignore_scale_events = False

        if volume is None:
            if hasattr(self, "volume_text_var"): self.volume_text_var.set("--")
        else:
            if hasattr(self, "volume_text_var"): self.volume_text_var.set(f"{clamp(volume, 0, 100)}%")
        self._sync_tray_menu_state()

    def _show_volume_overlay(self, volume: int | None = None, provider: Any | None = None) -> None:
        if self._closing or self._overlay is None:
            return
        if volume is None:
            volume = self.current_volume
        provider_id = (
            self._plugin_manager.volume_provider_id(provider)
            if provider is not None and self._plugin_manager is not None
            else self._active_overlay_provider_id()
        )
        statuses = self._get_volume_statuses()
        if statuses or volume is None:
            statuses = self._overlay.select_statuses(statuses, provider_id)
            self._render_overlay(
                "show_statuses",
                statuses,
                provider_id,
                preferred_display_device_name=self._selected_display_device_name(),
            )
            return
        self._render_overlay("show", clamp(volume, 0, 100), preferred_display_device_name=self._selected_display_device_name())

    def _show_plugin_overlay_text(self, text: str) -> None:
        """Render a plugin notification after PluginManager returns to Tk."""
        if self._closing or self._overlay is None:
            return
        show_text = getattr(self._overlay, "show_text", None)
        if callable(show_text):
            self._render_overlay(
                "show_text",
                text,
                preferred_display_device_name=self._selected_display_device_name(),
            )

    def _show_overlay_preview(self) -> None:
        """Render the real compact volume HUD for Appearance previews."""
        if self._closing or self._overlay is None:
            return
        self._render_overlay(
            "show",
            58,
            preferred_display_device_name=self._selected_display_device_name(),
        )

    def _replace_overlay_renderer(self) -> None:
        """Replace a renderer only from the Tk-thread manager callback."""
        if self._closing or self._plugin_manager is None:
            return
        previous = self._overlay
        self._overlay = self._plugin_manager.create_overlay_renderer(self.dark_mode, self.high_contrast)
        if previous is not None:
            previous.close()

    def _active_overlay_provider_id(self) -> str | None:
        return None

    def _get_volume_statuses(self) -> tuple[VolumeStatus, ...]:
        return tuple(getattr(self, "_volume_statuses", {}).values())

    def _publish_volume_status(
        self, provider: Any, confirmed_volume: int | None, reason: str | None = None
    ) -> None:
        manager = self._plugin_manager
        if manager is None:
            return
        provider_id = manager.volume_provider_id(provider)
        if provider_id is None:
            return
        relevant = manager.relevant_volume_provider_ids()
        existing = self._volume_statuses.get(provider_id)
        route_name = manager.route_name_for_provider(provider)
        display_name = route_name if isinstance(route_name, str) and route_name.strip() else str(getattr(provider, "provider_name", provider_id))
        self._volume_statuses[provider_id] = VolumeStatus(
            provider_id=provider_id,
            display_name=display_name,
            confirmed_volume=confirmed_volume,
            active=False,
            routed=manager.is_volume_provider_routed(provider_id),
            reason=reason,
        )
        # Retain only configured providers relevant to active control or an input route.
        self._volume_statuses = {
            status_id: status
            for status_id, status in self._volume_statuses.items()
            if status_id in relevant
        }
        manager.refresh_routes_panel()

    def _ensure_relevant_volume_statuses(self) -> None:
        manager = getattr(self, "_plugin_manager", None)
        if manager is None:
            return
        for route, provider in manager.relevant_volume_providers():
            existing = self._volume_statuses.get(route.route_id)
            if existing is None:
                self._publish_volume_status(provider, None, "Not yet read")

    def _show_unavailable_error(self, message: str | None = None) -> None:
        reason = message or self._control_unavailable_reason or "Selected monitor is unavailable."
        self._set_status(reason)
        if not self._closing and self._overlay is not None:
            self._render_overlay(
                "show_error",
                reason,
                preferred_display_device_name=self._selected_display_device_name(),
            )

    def _render_overlay(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Disable a failed optional renderer without interrupting route control."""
        overlay = self._overlay
        if overlay is None:
            return
        try:
            getattr(overlay, method_name)(*args, **kwargs)
        except Exception as exc:
            LOGGER.error("Volume overlay %s failed (%s).", method_name, exc.__class__.__name__)
            self._overlay = None
            self._set_status(f"Volume overlay failed: {self._format_error(exc)}. Routes remain available.")
            try:
                overlay.close()
            except Exception:
                pass

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
                signals=tuple(
                    TraySignalMenuItem(signal.tray_label, signal.signal_id)
                    for signal in getattr(getattr(self, "_plugin_manager", None), "action_signals", ())
                    if signal.tray_label is not None
                ),
            )
        )

    def _dispatch_tray_signal(self, signal_id: str) -> None:
        if self._closing:
            return
        manager = getattr(self, "_plugin_manager", None)
        if manager is not None:
            manager.dispatch_action_signal(signal_id)

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

    def refresh_configured_routes(self, automatic: bool = False) -> None:
        """Probe routed outputs on the one operation lane without touching Tk off-thread."""
        if self._refresh_after_id is not None:
            self.root.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None
        if self._busy or self._closing:
            self._refresh_requested = True
            self._refresh_requested_automatic = self._refresh_requested_automatic or automatic
            return
        manager = getattr(self, "_plugin_manager", None)
        routes = manager.relevant_volume_providers() if manager else ()
        if not isinstance(routes, tuple):
            routes = manager.volume_providers_for_input(self.WINDOWS_VOLUME_INPUT_ID) if manager else ()
        self._ready_route_ids.clear()
        self._hotkeys_ready = False
        self._topology_valid.clear()
        self._update_hotkey_state()
        if not routes:
            self._control_unavailable_reason = "Configure a Windows volume-key route."
            self._set_status(self._control_unavailable_reason)
            return
        generation = self._current_topology_generation()
        self._set_busy(True, "Checking configured routes...")
        operation_id = self._begin_ddc_operation("Route status refresh")

        def runner() -> None:
            results: list[tuple[str, Any, int | None, str | None]] = []
            for route, provider in routes:
                try:
                    activate = getattr(provider, "activate_volume_provider", None)
                    if callable(activate):
                        activate()
                    ready, reason = provider.is_volume_provider_available()
                    if not ready:
                        raise RuntimeError(reason or "Provider is unavailable")
                    results.append((route.route_id, provider, clamp(int(provider.read_volume()), 0, 100), None))
                except Exception as exc:
                    results.append((route.route_id, provider, None, self._format_error(exc)))
            self._post_to_ui(
                lambda values=tuple(results), token=generation, operation=operation_id:
                self._finish_configured_route_refresh(values, token, operation)
            )

        threading.Thread(target=runner, name="routed-volume-refresh", daemon=True).start()

    def _finish_configured_route_refresh(
        self, results: tuple[tuple[str, Any, int | None, str | None], ...], generation: int, operation_id: int
    ) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        self._busy = False
        if not self._is_topology_generation_current(generation):
            self._schedule_refresh(self.DISPLAY_CHANGE_DEBOUNCE_MS, automatic=True)
            return
        manager = self._plugin_manager
        self._ready_route_ids = {route_id for route_id, _provider, volume, reason in results if volume is not None and reason is None}
        for route_id, provider, volume, reason in results:
            self._publish_volume_status(provider, volume, reason)
        if self._display_listener_available():
            self._topology_valid.set()
        self._hotkeys_ready = bool(self._ready_route_ids and self._topology_valid.is_set())
        if self._hotkeys_ready:
            self._control_unavailable_reason = None
        else:
            failures = [
                f"{manager.route_name(route_id) or 'Route'}: {reason}"
                for route_id, _provider, _volume, reason in results
                if reason
            ]
            detail = " ".join(failures)
            self._control_unavailable_reason = (
                "No configured Windows volume-key route is ready."
                + (f" {detail}" if detail else "")
            )
        self._update_hotkey_state()
        self._set_status("Ready. Configured volume routes are active." if self._hotkeys_ready else self._control_unavailable_reason)
        self._apply_control_state()
        self._run_deferred_refresh()

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
        self.refresh_configured_routes(automatic=automatic)

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
        self.refresh_configured_routes(automatic=automatic)

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
            self._control_unavailable_reason = "Select a volume provider in Routes."
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
        self._publish_volume_status(provider, self.current_volume)
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
        provider = self._plugin_manager.active_volume_provider() if self._plugin_manager is not None else None
        if provider is not None:
            self._publish_volume_status(provider, None, self._control_unavailable_reason)
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
        self._publish_volume_status(provider, self.current_volume)
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
        provider = self._plugin_manager.active_volume_provider() if self._plugin_manager is not None else None
        if provider is not None:
            self._publish_volume_status(provider, None, self._control_unavailable_reason)
        self._show_unavailable_error(self._control_unavailable_reason)
        self._apply_control_state()

    def refresh_monitors(
        self,
        automatic: bool = False,
        selection_target: SavedMonitorSelection | None = None,
    ) -> None:
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
            if plugin_manager is not None:
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

    def _route_windows_volume_delta(self, delta: int) -> None:
        manager = self._plugin_manager
        route_ids = tuple(route.route_id for route, _provider in (manager.volume_providers_for_input(self.WINDOWS_VOLUME_INPUT_ID) if manager else ()))
        self._route_volume_delta(route_ids, delta)

    def _route_volume_delta(self, route_ids: tuple[str, ...], delta: int, reconcile: bool = False, absolute_target: int | None = None) -> None:
        """Apply one host input to every configured output on the sole worker slot."""
        manager = self._plugin_manager
        available = manager.relevant_volume_providers() if manager else ()
        if not isinstance(available, tuple):
            available = manager.volume_providers_for_input(self.WINDOWS_VOLUME_INPUT_ID) if manager else ()
        routes = tuple(
            (
                route,
                provider,
                self._volume_statuses.get(route.route_id).confirmed_volume
                if self._volume_statuses.get(route.route_id) is not None
                else None,
            )
            for route, provider in available
            if route.route_id in route_ids and route.route_id in self._ready_route_ids
        )
        if (
            not routes
            or self._closing
            or not self._display_listener_available()
            or not self._topology_valid.is_set()
            or self._busy
        ):
            self._cancel_route_repeats(set(route_ids))
            return
        generation = self._current_topology_generation()
        scheduler = getattr(self, "_route_repeat_scheduler", None)
        held_routes = {
            route_id for route_id in route_ids
            if scheduler is not None and scheduler.is_held(route_id)
        }
        self._volume_write_inflight = True
        self._set_busy(True, "Applying routed volume change...")
        operation_id = self._begin_ddc_operation("Routed volume change")

        def runner() -> None:
            results: list[tuple[str, Any, int | None, str | None, int | None]] = []
            for route, provider, confirmed_volume in routes:
                try:
                    ready, reason = provider.is_volume_provider_available()
                    if not ready:
                        raise RuntimeError(reason or "Provider is unavailable")
                    if reconcile:
                        results.append((route.route_id, provider, clamp(int(provider.read_volume()), 0, 100), None, None))
                        continue
                    fast_write = getattr(provider, "write_volume_fast", None)
                    supports_fast_write = getattr(provider, "supports_fast_volume_write", False) is True
                    optimistic = getattr(self, "_route_optimistic_targets", {}).get(route.route_id)
                    current = clamp(int(optimistic), 0, 100) if optimistic is not None else (
                        clamp(int(confirmed_volume), 0, 100) if confirmed_volume is not None
                        else clamp(int(provider.read_volume()), 0, 100)
                    )
                    target = clamp(absolute_target, 0, 100) if absolute_target is not None else clamp(current + delta, 0, 100)
                    # Do not send redundant physical writes at the volume bounds.
                    # Some DDC implementations reject an otherwise harmless set.
                    if target == current:
                        results.append((route.route_id, provider, current, None, None))
                    elif route.route_id in held_routes and supports_fast_write and callable(fast_write):
                        fast_write(target)
                        results.append((route.route_id, provider, None, None, target))
                    else:
                        results.append((route.route_id, provider, clamp(int(provider.write_volume(target)), 0, 100), None, None))
                except Exception as exc:
                    results.append((route.route_id, provider, None, self._format_error(exc), None))
            self._post_to_ui(lambda values=tuple(results), token=generation, operation=operation_id: self._finish_routed_volume_change(values, token, operation))

        threading.Thread(target=runner, name="routed-volume-change", daemon=True).start()

    def _finish_routed_volume_change(self, results: tuple[tuple[str, Any, int | None, str | None, int | None], ...], generation: int, operation_id: int) -> None:
        if self._closing or not self._accept_ddc_completion(operation_id):
            return
        self._volume_write_inflight = False
        self._busy = False
        if not self._is_topology_generation_current(generation):
            self._set_status("Display changed while applying routed volume changes.")
            self._apply_control_state()
            return
        failures: list[str] = []
        manager = self._plugin_manager
        for route_id, provider, volume, reason, optimistic_target in results:
            if optimistic_target is not None:
                # Fast sends deliberately do not claim confirmation.  The target
                # is only a baseline for the next held tick until release reads it.
                getattr(self, "_route_optimistic_targets", {})[route_id] = optimistic_target
                LOGGER.info(
                    "Route output sent: route=%s, output=%s, target volume=%d.",
                    manager.route_name(route_id) if manager is not None else route_id,
                    getattr(provider, "provider_name", provider.__class__.__name__),
                    optimistic_target,
                )
            else:
                getattr(self, "_route_optimistic_targets", {}).pop(route_id, None)
                self._publish_volume_status(provider, volume, reason)
                if volume is not None and reason is None:
                    LOGGER.info(
                        "Route output applied: route=%s, output=%s, volume=%d.",
                        manager.route_name(route_id) if manager is not None else route_id,
                        getattr(provider, "provider_name", provider.__class__.__name__),
                        volume,
                    )
            if reason is not None:
                getattr(self, "_route_reconciliation_pending", set()).discard(route_id)
                failures.append(
                    f"{manager.route_name(route_id) or 'Route'}: {reason}"
                )
        # A route write can fail after the physical device accepted the command
        # or because of a transient DDC/CI response. Keep the last successful
        # probe result so the next key press can re-read and retry the route.
        self._set_status(
            "Routed volume change completed."
            if not failures
            else "Routed volume change completed with "
            f"{len(failures)} failure(s). {' '.join(failures)}"
        )
        self._show_volume_overlay(provider=results[-1][1] if results else None)
        self._apply_control_state()

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
        self._presentation_requested_visible = True
        presentation = getattr(self, "_presentation", None)
        if presentation is None:
            if sys.platform == "darwin":
                self.root.after_idle(self.on_close)
                return
            self.root.deiconify()
            self.root.state("normal")
            apply_window_chrome(self.root, self.dark_mode)
            self.root.lift()
            self.root.focus_force()
            return
        if not presentation.launch():
            self._presentation_revision += 1

    def restore_from_tray(self) -> None:
        if self._closing or not self._in_tray:
            return
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
        self._cancel_route_repeats()
        self._closing = True
        presentation = getattr(self, "_presentation", None)
        if presentation is not None:
            presentation.shutdown()
        self._close_diagnostic_log()
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
