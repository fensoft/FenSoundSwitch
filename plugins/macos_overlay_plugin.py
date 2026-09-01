from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from plugin_api import PLUGIN_API_VERSION, OverlayRenderer, PluginHostContext, VolumeStatus
from plugins.windows11_overlay_plugin import (
    AUTO_HIDE_MS,
    ERROR_AUTO_HIDE_MS,
    OverlayPalette,
    VolumeOverlay,
)


MAC_PROGRESS_STYLE = "MacVolumeOverlay.Horizontal.TProgressbar"


class MacOSVolumeOverlay(VolumeOverlay):
    """A compact, translucent macOS-inspired HUD using the same safe window path."""

    @staticmethod
    def _get_palette(dark_mode: bool, high_contrast: bool) -> OverlayPalette:
        if high_contrast:
            return OverlayPalette("SystemWindow", "SystemWindowText", "SystemWindowText", "SystemWindowText", "SystemHighlight", "SystemWindowText", "SystemWindow", 1.0)
        if dark_mode:
            return OverlayPalette("#252525", "#4A4A4A", "#FFFFFF", "#D8D8D8", "#55D6BE", "#FF8F8F", "#555555", 0.92)
        return OverlayPalette("#F2F2F2", "#B5B5B5", "#202020", "#4D4D4D", "#007A69", "#B3261E", "#D2D2D2", 0.96)

    def __init__(self, root: tk.Tk, dark_mode: bool = True, high_contrast: bool = False) -> None:
        super().__init__(root, dark_mode, high_contrast)
        self.title_label.configure(font=("Helvetica", 10, "bold"))
        self.value_label.configure(font=("Helvetica", 28, "bold"))
        self.progress.configure(style=MAC_PROGRESS_STYLE, length=220)
        self.apply_theme(dark_mode, high_contrast)

    def apply_theme(self, dark_mode: bool, high_contrast: bool = False) -> None:
        super().apply_theme(dark_mode, high_contrast)
        palette = self._palette
        self._style.configure(
            MAC_PROGRESS_STYLE,
            background=palette.accent,
            troughcolor=palette.track,
            bordercolor=palette.track,
            lightcolor=palette.accent,
            darkcolor=palette.accent,
            thickness=8,
        )

    def show(self, volume: int, preferred_display_device_name: str | None = None) -> None:
        super().show(volume, preferred_display_device_name)
        self.title_var.set("VOLUME")

    def show_error(self, message: str, preferred_display_device_name: str | None = None) -> None:
        super().show_error(message, preferred_display_device_name)
        self.title_var.set("VOLUME")

    def show_text(self, text: str, preferred_display_device_name: str | None = None) -> None:
        super().show_text(text, preferred_display_device_name)
        self.title_var.set("FENSOUNDSWITCH")

    def show_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None, preferred_display_device_name: str | None = None) -> None:
        super().show_statuses(statuses, current_provider_id, preferred_display_device_name)
        if statuses:
            self.title_var.set("VOLUME ROUTES")

    def select_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None) -> tuple[VolumeStatus, ...]:
        return statuses


class MacOSOverlayPlugin:
    plugin_id = "macos-overlay"
    name = "macOS-style overlay"
    description = "macOS-inspired translucent no-activate routed-volume overlay."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
        if self._host is None:
            raise RuntimeError("Overlay plugin has not been initialized.")
        return MacOSVolumeOverlay(self._host.ui_parent, dark_mode, high_contrast)

    def shutdown(self, timeout: float) -> bool:
        return True


def create_plugin() -> MacOSOverlayPlugin:
    return MacOSOverlayPlugin()
