from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Mapping

from diagnostics import get_logger
from plugin_api import PLUGIN_API_VERSION, OverlayRenderer, PluginHostContext, VolumeStatus, plugin_ui_document, plugin_ui_result
from windows_platform import (
    DisplayArea,
    configure_no_activate_window,
    get_overlay_display_area,
    get_toplevel_window_handle,
    show_window_no_activate,
)


OVERLAY_BG = "#171A1F"
OVERLAY_BORDER = "#343C49"
OVERLAY_TEXT = "#F6F8FB"
OVERLAY_SUBTEXT = "#9AA5B5"
OVERLAY_ACCENT = "#62A7FF"
OVERLAY_ERROR = "#FF6B6B"
OVERLAY_TRACK = "#0B0E12"
LIGHT_OVERLAY_BG = "#FFFFFF"
LIGHT_OVERLAY_BORDER = "#D5DAE1"
LIGHT_OVERLAY_TEXT = "#191A1C"
LIGHT_OVERLAY_SUBTEXT = "#404040"
LIGHT_OVERLAY_ACCENT = "#0067C0"
LIGHT_OVERLAY_ERROR = "#B10E1E"
LIGHT_OVERLAY_TRACK = "#D8D8D8"
AUTO_HIDE_MS = 1400
ERROR_AUTO_HIDE_MS = 2800
OVERLAY_BOTTOM_MARGIN = 88
OVERLAY_SIDE_MARGIN = 24
OVERLAY_CONTENT_PADX = 16
OVERLAY_CONTENT_PADY = 13
OVERLAY_LABEL_FONT = ("Segoe UI Variable", 9)
OVERLAY_VALUE_FONT = ("Segoe UI Variable", 22, "bold")
OVERLAY_BAR_LENGTH = 196
OVERLAY_BAR_THICKNESS = 6
OVERLAY_ERROR_WRAP = 240
PROGRESS_STYLE = "VolumeOverlay.Horizontal.TProgressbar"
LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class OverlayGeometry:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class OverlayPalette:
    background: str
    border: str
    text: str
    subtext: str
    accent: str
    error: str
    track: str
    alpha: float


@dataclass
class StatusRowWidgets:
    frame: tk.Frame
    name_label: tk.Label
    value_label: tk.Label
    progress: ttk.Progressbar | None


def order_volume_statuses(
    statuses: tuple[VolumeStatus, ...], current_provider_id: str | None
) -> tuple[VolumeStatus, ...]:
    """Keep the changed route first without relying on provider state."""
    return tuple(
        sorted(
            statuses,
            key=lambda status: (
                status.provider_id != current_provider_id,
                status.display_name.casefold(),
            ),
        )
    )


def format_volume_status(status: VolumeStatus) -> str:
    if status.confirmed_volume is not None:
        return f"{status.confirmed_volume}%"
    return "Unavailable" + (f" ({status.reason})" if status.reason else "")


def format_volume_status_rows(
    statuses: tuple[VolumeStatus, ...], current_provider_id: str | None
) -> tuple[str, ...]:
    """Format immutable host snapshots without reading Tk or provider state."""
    ordered = order_volume_statuses(statuses, current_provider_id)
    return tuple(
        f"{status.display_name}: {format_volume_status(status)}"
        for status in ordered
    )


def calculate_overlay_geometry(
    requested_width: int,
    requested_height: int,
    display_area: DisplayArea,
) -> OverlayGeometry:
    work_area = display_area.work_area
    scale_percent = max(100, display_area.scale_percent)
    side_margin = round(OVERLAY_SIDE_MARGIN * scale_percent / 100)
    bottom_margin = round(OVERLAY_BOTTOM_MARGIN * scale_percent / 100)
    top_margin = round(32 * scale_percent / 100)

    available_width = max(1, work_area.width - (2 * side_margin))
    available_height = max(1, work_area.height - top_margin - bottom_margin)
    width = min(max(1, requested_width), available_width)
    height = min(max(1, requested_height), available_height)
    x = work_area.left + ((work_area.width - width) // 2)
    y = work_area.bottom - bottom_margin - height
    y = max(work_area.top + top_margin, min(y, work_area.bottom - height))
    return OverlayGeometry(x=x, y=y, width=width, height=height)


class VolumeOverlay:
    def __init__(
        self,
        root: tk.Tk,
        dark_mode: bool = True,
        high_contrast: bool = False,
        mode: str = "all",
    ) -> None:
        self.root = root
        self._hide_after_id: str | None = None
        self._style = ttk.Style(root)
        self._palette = self._get_palette(dark_mode, high_contrast)
        self._mode = mode

        self.window = tk.Toplevel(root, takefocus=False)
        self.window.withdraw()
        self.window.overrideredirect(True)
        try:
            self.window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.title_var = tk.StringVar(value="Volume")
        self.route_var = tk.StringVar(value="")
        self.error_var = tk.StringVar(value="")

        self.border = tk.Frame(self.window, bd=0, highlightthickness=0)
        self.border.pack()

        self.content = tk.Frame(
            self.border,
            padx=OVERLAY_CONTENT_PADX,
            pady=OVERLAY_CONTENT_PADY,
        )
        self.content.pack(padx=1, pady=1)

        self.title_label = tk.Label(
            self.content,
            textvariable=self.title_var,
            font=OVERLAY_LABEL_FONT,
            anchor="center",
        )
        self.title_label.pack(fill="x")

        self.route_label = tk.Label(
            self.content,
            textvariable=self.route_var,
            font=OVERLAY_LABEL_FONT,
        )

        self.value_var = tk.StringVar(value="0%")
        self.value_label = tk.Label(
            self.content,
            textvariable=self.value_var,
            font=OVERLAY_VALUE_FONT,
        )

        self.error_label = tk.Label(
            self.content,
            textvariable=self.error_var,
            font=OVERLAY_LABEL_FONT,
            justify="left",
            wraplength=OVERLAY_ERROR_WRAP,
        )

        self.progress = ttk.Progressbar(
            self.content,
            style=PROGRESS_STYLE,
            orient=tk.HORIZONTAL,
            mode="determinate",
            length=OVERLAY_BAR_LENGTH,
            maximum=100,
        )
        self.progress.pack(fill="x")

        self.status_rows = tk.Frame(self.content)
        self._status_row_widgets: list[StatusRowWidgets] = []

        self.apply_theme(dark_mode, high_contrast)
        self.window.update_idletasks()
        self._configure_no_activate()

    @staticmethod
    def _get_palette(dark_mode: bool, high_contrast: bool) -> OverlayPalette:
        if high_contrast:
            return OverlayPalette(
                background="SystemWindow",
                border="SystemWindowText",
                text="SystemWindowText",
                subtext="SystemWindowText",
                accent="SystemHighlight",
                error="SystemWindowText",
                track="SystemWindow",
                alpha=1.0,
            )
        if dark_mode:
            return OverlayPalette(
                background=OVERLAY_BG,
                border=OVERLAY_BORDER,
                text=OVERLAY_TEXT,
                subtext=OVERLAY_SUBTEXT,
                accent=OVERLAY_ACCENT,
                error=OVERLAY_ERROR,
                track=OVERLAY_TRACK,
                alpha=0.96,
            )
        return OverlayPalette(
            background=LIGHT_OVERLAY_BG,
            border=LIGHT_OVERLAY_BORDER,
            text=LIGHT_OVERLAY_TEXT,
            subtext=LIGHT_OVERLAY_SUBTEXT,
            accent=LIGHT_OVERLAY_ACCENT,
            error=LIGHT_OVERLAY_ERROR,
            track=LIGHT_OVERLAY_TRACK,
            alpha=0.97,
        )

    def apply_theme(self, dark_mode: bool, high_contrast: bool = False) -> None:
        self._palette = self._get_palette(dark_mode, high_contrast)
        palette = self._palette
        self.window.configure(bg=palette.border)
        self.border.configure(bg=palette.border)
        self.content.configure(bg=palette.background)
        self.title_label.configure(bg=palette.background, fg=palette.subtext)
        self.route_label.configure(bg=palette.background, fg=palette.subtext)
        self.value_label.configure(bg=palette.background, fg=palette.text)
        self.error_label.configure(bg=palette.background, fg=palette.text)
        self.status_rows.configure(bg=palette.background)
        self._style.configure(
            PROGRESS_STYLE,
            background=palette.accent,
            troughcolor=palette.track,
            bordercolor=palette.track,
            lightcolor=palette.accent,
            darkcolor=palette.accent,
            thickness=OVERLAY_BAR_THICKNESS,
        )
        for row in self._status_row_widgets:
            row.frame.configure(bg=palette.background)
            row.name_label.configure(bg=palette.background, fg=palette.subtext)
            row.value_label.configure(
                bg=palette.background,
                fg=palette.text if row.progress is not None else palette.error,
            )
        try:
            self.window.attributes("-alpha", palette.alpha)
        except tk.TclError:
            pass

    def show(
        self,
        volume: int,
        preferred_display_device_name: str | None = None,
    ) -> None:
        volume = max(0, min(volume, 100))
        self._hide_status_rows()
        self._hide_route_name()
        self.title_var.set("Volume")
        self.value_var.set(f"{volume}%")
        self.value_label.configure(fg=self._palette.text)
        if not self.value_label.winfo_manager():
            self.value_label.pack(anchor="w", pady=(1, 8))
        self.error_var.set("")
        self.error_label.pack_forget()
        if not self.progress.winfo_manager():
            self.progress.pack(fill="x")
        self.progress.configure(value=volume)
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def show_error(
        self,
        message: str,
        preferred_display_device_name: str | None = None,
    ) -> None:
        self._hide_status_rows()
        self._hide_route_name()
        self.title_var.set("Volume")
        self.value_var.set("Unavailable")
        self.value_label.configure(fg=self._palette.error)
        if not self.value_label.winfo_manager():
            self.value_label.pack(anchor="w", pady=(1, 8))
        self.error_var.set(message.strip() or "Selected monitor is unavailable.")
        self.progress.pack_forget()
        if not self.error_label.winfo_manager():
            self.error_label.pack(anchor="w")
        self._show_window(ERROR_AUTO_HIDE_MS, preferred_display_device_name)

    def show_text(
        self,
        text: str,
        preferred_display_device_name: str | None = None,
    ) -> None:
        self._hide_status_rows()
        self._hide_route_name()
        self.title_var.set("FenSoundSwitch")
        self.value_var.set(text.strip() or "Done")
        self.value_label.configure(fg=self._palette.text)
        if not self.value_label.winfo_manager():
            self.value_label.pack(anchor="w", pady=(1, 8))
        self.error_var.set("")
        self.error_label.pack_forget()
        self.progress.pack_forget()
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def show_statuses(
        self,
        statuses: tuple[VolumeStatus, ...],
        current_provider_id: str | None,
        preferred_display_device_name: str | None = None,
    ) -> None:
        statuses = self.select_statuses(statuses, current_provider_id)
        if not statuses:
            self.show_error("No routed volume providers are available.", preferred_display_device_name)
            return
        if self._mode == "current":
            self._show_current_status(statuses[0])
        else:
            self._show_all_statuses(order_volume_statuses(statuses, current_provider_id))
        self._show_window(AUTO_HIDE_MS, preferred_display_device_name)

    def _show_current_status(self, status: VolumeStatus) -> None:
        self._hide_status_rows()
        self.title_var.set("Volume")
        self.route_var.set(status.display_name)
        if not self.route_label.winfo_manager():
            self.route_label.pack(anchor="w", pady=(1, 3))
        self.error_var.set(status.reason or "Selected route is unavailable.")
        self.error_label.pack_forget()
        if status.confirmed_volume is None:
            self.progress.pack_forget()
            self.value_var.set("Unavailable")
            self.value_label.configure(fg=self._palette.error)
            if not self.value_label.winfo_manager():
                self.value_label.pack(anchor="w", pady=(1, 3))
            if not self.error_label.winfo_manager():
                self.error_label.pack(anchor="w")
            return
        self.value_var.set(f"{status.confirmed_volume}%")
        self.value_label.configure(fg=self._palette.text)
        if not self.value_label.winfo_manager():
            self.value_label.pack(anchor="w", pady=(1, 8))
        if not self.progress.winfo_manager():
            self.progress.pack(fill="x")
        self.progress.configure(value=status.confirmed_volume)

    def _show_all_statuses(self, statuses: tuple[VolumeStatus, ...]) -> None:
        self.title_var.set("Volume")
        self.route_var.set("")
        self.error_var.set("")
        self._hide_route_name()
        self.value_label.pack_forget()
        self.error_label.pack_forget()
        self.progress.pack_forget()
        self._update_status_rows(statuses)
        if not self.status_rows.winfo_manager():
            self.status_rows.pack(fill="x")

    def _hide_route_name(self) -> None:
        self.route_label.pack_forget()

    def _hide_status_rows(self) -> None:
        self.status_rows.pack_forget()

    def _update_status_rows(self, statuses: tuple[VolumeStatus, ...]) -> None:
        while len(self._status_row_widgets) > len(statuses):
            row = self._status_row_widgets.pop()
            row.frame.destroy()
        for index, status in enumerate(statuses):
            if index == len(self._status_row_widgets):
                self._status_row_widgets.append(self._create_status_row())
            self._configure_status_row(self._status_row_widgets[index], status)

    def _create_status_row(self) -> StatusRowWidgets:
        frame = tk.Frame(self.status_rows)
        frame.pack(fill="x", pady=(0, 6))
        name_label = tk.Label(frame, font=OVERLAY_LABEL_FONT)
        name_label.pack(anchor="w")
        value_label = tk.Label(frame, font=OVERLAY_VALUE_FONT, anchor="w")
        self._apply_status_row_theme(frame, name_label, value_label, None)
        return StatusRowWidgets(frame, name_label, value_label, None)

    def _configure_status_row(self, row: StatusRowWidgets, status: VolumeStatus) -> None:
        row.name_label.configure(text=status.display_name)
        if status.confirmed_volume is None:
            if row.progress is not None:
                row.progress.destroy()
                row.progress = None
            row.value_label.configure(text=format_volume_status(status))
            if not row.value_label.winfo_manager():
                row.value_label.pack(anchor="w", pady=(3, 0))
            self._apply_status_row_theme(row.frame, row.name_label, row.value_label, None)
            return
        if row.progress is None:
            row.progress = ttk.Progressbar(
                row.frame,
                style=PROGRESS_STYLE,
                orient=tk.HORIZONTAL,
                mode="determinate",
                length=OVERLAY_BAR_LENGTH,
                maximum=100,
            )
            row.progress.pack(fill="x", pady=(3, 0))
        row.value_label.configure(text=f"{status.confirmed_volume}%")
        if not row.value_label.winfo_manager():
            row.value_label.pack(anchor="w", pady=(1, 3))
        row.progress.configure(value=status.confirmed_volume)
        self._apply_status_row_theme(row.frame, row.name_label, row.value_label, row.progress)

    def _apply_status_row_theme(
        self,
        frame: tk.Frame,
        name_label: tk.Label,
        value_label: tk.Label,
        progress: ttk.Progressbar | None,
    ) -> None:
        frame.configure(bg=self._palette.background)
        name_label.configure(bg=self._palette.background, fg=self._palette.subtext)
        value_label.configure(
            bg=self._palette.background,
            fg=self._palette.text if progress is not None else self._palette.error,
        )

    def select_statuses(
        self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None
    ) -> tuple[VolumeStatus, ...]:
        if self._mode == "current" and current_provider_id is not None:
            return tuple(status for status in statuses if status.provider_id == current_provider_id)
        return statuses

    def _show_window(
        self,
        auto_hide_ms: int,
        preferred_display_device_name: str | None,
    ) -> None:
        if self._hide_after_id is not None:
            self.window.after_cancel(self._hide_after_id)
            self._hide_after_id = None

        placement = self._position_window(preferred_display_device_name)
        hwnd = self._configure_no_activate()
        if placement is None or not hwnd:
            self.window.withdraw()
            return

        self.window.deiconify()
        if not show_window_no_activate(
            hwnd,
            placement.x,
            placement.y,
            placement.width,
            placement.height,
        ):
            LOGGER.warning("Showing the volume overlay without activation failed.")
            self.window.withdraw()
            return
        self._hide_after_id = self.window.after(auto_hide_ms, self.hide)

    def _configure_no_activate(self) -> int:
        try:
            hwnd = get_toplevel_window_handle(self.window.winfo_id())
        except tk.TclError:
            return 0
        if not configure_no_activate_window(hwnd):
            LOGGER.warning("Applying the volume overlay no-activate style failed.")
            return 0
        return hwnd

    def _position_window(
        self,
        preferred_display_device_name: str | None,
    ) -> OverlayGeometry | None:
        self.window.update_idletasks()
        display_area = get_overlay_display_area(preferred_display_device_name)
        if display_area is None:
            LOGGER.warning("No Windows display work area is available for the volume overlay.")
            return None
        placement = calculate_overlay_geometry(
            self.window.winfo_reqwidth(),
            self.window.winfo_reqheight(),
            display_area,
        )
        self.window.geometry(
            f"{placement.width}x{placement.height}{placement.x:+d}{placement.y:+d}"
        )
        return placement

    def hide(self) -> None:
        self._hide_after_id = None
        self.window.withdraw()

    def close(self) -> None:
        if self._hide_after_id is not None:
            self.window.after_cancel(self._hide_after_id)
            self._hide_after_id = None
        if self.window.winfo_exists():
            self.window.destroy()


class OverlayPlugin:
    """Bundled renderer definition; the host owns all Tk-thread calls."""

    plugin_id = "windows11-overlay"
    name = "Windows 11 overlay"
    description = "Windows 11-inspired no-activate routed-volume overlay."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._mode = "all"

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        settings = host.load_plugin_settings()
        mode = settings.get("mode")
        if mode == "all-routed":
            mode = "all"
        if mode not in ("all", "current"):
            legacy_mode = host.load_legacy_overlay_mode()
            self._mode = legacy_mode or "all"
            if legacy_mode is not None:
                host.save_plugin_settings({"schema_version": 1, "mode": self._mode})
                host.clear_legacy_overlay_mode()
        else:
            self._mode = mode

    def get_plugin_ui(self) -> dict[str, object]:
        return plugin_ui_document("Windows 11 overlay settings", [
            {"id": "mode", "type": "choice", "label": "Show", "value": self._mode, "options": [{"label": "Every route", "value": "all"}, {"label": "Only the route that changed", "value": "current"}]},
        ], [{"id": "save", "label": "Save", "kind": "submit", "async": False}], "Choose which routed volume statuses appear in the overlay.")

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        if action_id != "save":
            raise ValueError(f"Unknown Windows 11 overlay UI action {action_id!r}.")
        mode = values.get("mode")
        if mode not in ("all", "current"):
            raise ValueError("Windows 11 overlay mode must be all or current.")
        if self._host is None:
            raise RuntimeError("Overlay plugin has not been initialized.")
        self._mode = str(mode)
        settings = {"schema_version": 1, "mode": self._mode}
        self._host.save_plugin_settings(settings)
        return plugin_ui_result("save", values=settings)

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
        if self._host is None:
            raise RuntimeError("Overlay plugin has not been initialized.")
        return VolumeOverlay(
            self._host.ui_parent,
            dark_mode=dark_mode,
            high_contrast=high_contrast,
            mode=self._mode,
        )

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> OverlayPlugin:
    return OverlayPlugin()
