from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

try:
    import winreg
except ImportError:
    winreg = None

from windows_platform import (
    get_toplevel_window_handle,
    get_window_dpi,
    is_high_contrast_enabled,
    set_window_dark_mode,
)


PREFERRED_THEMES = ("vista", "xpnative", "winnative")
APP_ICON_PATH = Path(__file__).resolve().with_name("windows-ddc.ico")
DARK_THEME_NAME = "windows_ddc_dark"
WINDOWS_THEME_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WINDOWS_APPS_USE_LIGHT_THEME = "AppsUseLightTheme"
DARK_BG = "#202020"
DARK_SURFACE = "#2B2B2B"
DARK_BORDER = "#3C3C3C"
DARK_TEXT = "#F2F2F2"
DARK_DISABLED_TEXT = "#8A8A8A"
DARK_ACCENT = "#3A7BD5"
DARK_STATUS_BG = "#191919"
LIGHT_BG = "SystemButtonFace"
LIGHT_TEXT = "SystemWindowText"
LIGHT_LIST_BG = "SystemWindow"
LIGHT_SELECTION_BG = "SystemHighlight"
LIGHT_SELECTION_TEXT = "SystemHighlightText"


@dataclass(frozen=True)
class WindowsThemeState:
    dark_mode: bool
    high_contrast: bool


def choose_preferred_theme(theme_names: tuple[str, ...] | list[str]) -> str | None:
    available = set(theme_names)
    for theme_name in PREFERRED_THEMES:
        if theme_name in available:
            return theme_name
    return None


def is_windows_dark_mode_enabled() -> bool:
    if winreg is None:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_THEME_REG_PATH) as key:
            value, value_type = winreg.QueryValueEx(key, WINDOWS_APPS_USE_LIGHT_THEME)
    except OSError:
        return False

    if value_type != winreg.REG_DWORD:
        return False
    return int(value) == 0


def read_windows_theme_state() -> WindowsThemeState:
    high_contrast = is_high_contrast_enabled()
    return WindowsThemeState(
        dark_mode=is_windows_dark_mode_enabled() and not high_contrast,
        high_contrast=high_contrast,
    )


def ensure_dark_theme(style: ttk.Style) -> None:
    if DARK_THEME_NAME in style.theme_names():
        return

    parent_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
    style.theme_create(
        DARK_THEME_NAME,
        parent=parent_theme,
        settings={
            ".": {
                "configure": {
                    "background": DARK_BG,
                    "foreground": DARK_TEXT,
                    "fieldbackground": DARK_SURFACE,
                    "selectbackground": DARK_ACCENT,
                    "selectforeground": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "focuscolor": DARK_ACCENT,
                }
            },
            "TFrame": {"configure": {"background": DARK_BG}},
            "TLabel": {"configure": {"background": DARK_BG, "foreground": DARK_TEXT}},
            "TCheckbutton": {
                "configure": {
                    "background": DARK_BG,
                    "foreground": DARK_TEXT,
                    "focuscolor": DARK_ACCENT,
                },
                "map": {
                    "background": [("active", DARK_BG), ("disabled", DARK_BG)],
                    "foreground": [("disabled", DARK_DISABLED_TEXT)],
                },
            },
            "TButton": {
                "configure": {
                    "background": DARK_SURFACE,
                    "foreground": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "focuscolor": DARK_ACCENT,
                    "padding": 4,
                },
                "map": {
                    "background": [
                        ("active", "#343434"),
                        ("pressed", "#171717"),
                        ("disabled", DARK_SURFACE),
                    ],
                    "foreground": [("disabled", DARK_DISABLED_TEXT)],
                },
            },
            "TCombobox": {
                "configure": {
                    "foreground": DARK_TEXT,
                    "fieldbackground": DARK_SURFACE,
                    "background": DARK_SURFACE,
                    "arrowcolor": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "padding": 4,
                },
                "map": {
                    "foreground": [("readonly", DARK_TEXT), ("disabled", DARK_DISABLED_TEXT)],
                    "fieldbackground": [("readonly", DARK_SURFACE), ("disabled", DARK_SURFACE)],
                    "background": [("readonly", DARK_SURFACE), ("disabled", DARK_SURFACE)],
                    "arrowcolor": [("disabled", DARK_DISABLED_TEXT)],
                },
            },
            "Treeview": {
                "configure": {
                    "background": DARK_SURFACE,
                    "fieldbackground": DARK_SURFACE,
                    "foreground": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "rowheight": 24,
                },
                "map": {
                    "background": [("selected", DARK_ACCENT)],
                    "foreground": [("selected", DARK_TEXT)],
                },
            },
            "Treeview.Heading": {
                "configure": {
                    "background": DARK_BG,
                    "foreground": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                },
            },
            "Horizontal.TScale": {
                "configure": {
                    "background": DARK_BG,
                    "troughcolor": "#151515",
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                }
            },
        },
    )


def apply_theme(
    style: ttk.Style,
    dark_mode: bool,
    high_contrast: bool = False,
) -> str:
    if dark_mode and not high_contrast:
        ensure_dark_theme(style)
        style.theme_use(DARK_THEME_NAME)
        return DARK_THEME_NAME

    preferred_theme = choose_preferred_theme(style.theme_names())
    if preferred_theme is not None:
        style.theme_use(preferred_theme)
        return preferred_theme
    return style.theme_use()


def apply_color_scheme(
    root: tk.Tk,
    status_bar: tk.Label,
    dark_mode: bool,
    high_contrast: bool = False,
) -> None:
    if dark_mode and not high_contrast:
        root_bg = DARK_BG
        list_bg = DARK_SURFACE
        text = DARK_TEXT
        selection_bg = DARK_ACCENT
        selection_text = DARK_TEXT
        status_bg = DARK_STATUS_BG
    else:
        root_bg = LIGHT_BG
        list_bg = LIGHT_LIST_BG
        text = LIGHT_TEXT
        selection_bg = LIGHT_SELECTION_BG
        selection_text = LIGHT_SELECTION_TEXT
        status_bg = LIGHT_BG

    root.configure(bg=root_bg)
    root.option_add("*TCombobox*Listbox.background", list_bg)
    root.option_add("*TCombobox*Listbox.foreground", text)
    root.option_add("*TCombobox*Listbox.selectBackground", selection_bg)
    root.option_add("*TCombobox*Listbox.selectForeground", selection_text)
    status_bar.configure(bg=status_bg, fg=text)


def apply_window_chrome(root: tk.Tk, dark_mode: bool) -> None:
    try:
        root.update_idletasks()
        hwnd = get_toplevel_window_handle(root.winfo_id())
    except tk.TclError:
        return
    set_window_dark_mode(hwnd, dark_mode)


def get_tk_window_dpi(root: tk.Tk) -> int:
    try:
        root.update_idletasks()
        hwnd = get_toplevel_window_handle(root.winfo_id())
    except tk.TclError:
        return 96
    return get_window_dpi(hwnd)


def get_app_icon_path() -> Path | None:
    if APP_ICON_PATH.is_file():
        return APP_ICON_PATH
    return None


def apply_app_icon(root: tk.Tk) -> Path | None:
    icon_path = get_app_icon_path()
    if icon_path is None:
        return None

    try:
        root.iconbitmap(default=str(icon_path))
    except tk.TclError:
        pass
    return icon_path
