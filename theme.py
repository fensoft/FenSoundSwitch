from __future__ import annotations

import tkinter as tk
import sys
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

try:
    import winreg
except ImportError:
    winreg = None

from native_platform import (
    get_toplevel_window_handle,
    get_window_dpi,
    is_high_contrast_enabled,
    set_window_dark_mode,
)


PREFERRED_THEMES = ("vista", "xpnative", "winnative")
APP_ICON_PATH = Path(__file__).resolve().with_name("FenSoundSwitch.ico")
DARK_THEME_NAME = "fensoundswitch_dark"
WINDOWS_THEME_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WINDOWS_APPS_USE_LIGHT_THEME = "AppsUseLightTheme"
DARK_BG = "#11151A"
DARK_SIDEBAR = "#171B21"
DARK_SURFACE = "#1D222A"
DARK_SURFACE_ALT = "#242A34"
DARK_BORDER = "#343C49"
DARK_TEXT = "#F2F2F2"
DARK_MUTED_TEXT = "#9AA5B5"
DARK_DISABLED_TEXT = "#697382"
DARK_ACCENT = "#62A7FF"
DARK_ACCENT_HOVER = "#78B4FF"
DARK_ACCENT_PRESSED = "#4B91E8"
DARK_ACCENT_SURFACE = "#183C66"
DARK_SUCCESS = "#55D6A1"
DARK_STATUS_BG = "#0E1115"
if sys.platform == "win32":
    LIGHT_BG = "SystemButtonFace"
    LIGHT_TEXT = "SystemWindowText"
    LIGHT_LIST_BG = "SystemWindow"
    LIGHT_SELECTION_BG = "SystemHighlight"
    LIGHT_SELECTION_TEXT = "SystemHighlightText"
else:
    LIGHT_BG = "#ECECEC"
    LIGHT_TEXT = "#1F1F1F"
    LIGHT_LIST_BG = "#FFFFFF"
    LIGHT_SELECTION_BG = "#0A84FF"
    LIGHT_SELECTION_TEXT = "#FFFFFF"
LIGHT_SURFACE = "#FFFFFF"
LIGHT_SURFACE_ALT = "#F4F6F8"
LIGHT_SIDEBAR = "#F0F3F7"
LIGHT_BORDER = "#D5DAE1"
LIGHT_MUTED_TEXT = "#5F6B7A"
LIGHT_ACCENT = "#0F6CBD"
LIGHT_ACCENT_HOVER = "#115EA3"
LIGHT_ACCENT_PRESSED = "#0C3B5E"
LIGHT_ACCENT_SURFACE = "#DCEBFA"
LIGHT_SUCCESS = "#107C41"


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
            "TLabelframe": {
                "configure": {
                    "background": DARK_SURFACE,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "relief": "solid",
                }
            },
            "TLabelframe.Label": {
                "configure": {
                    "background": DARK_SURFACE,
                    "foreground": DARK_TEXT,
                }
            },
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
                    "padding": (10, 6),
                },
                "map": {
                    "background": [
                        ("active", "#303641"),
                        ("pressed", "#171B21"),
                        ("disabled", DARK_SURFACE),
                    ],
                    "foreground": [("disabled", DARK_DISABLED_TEXT)],
                },
            },
            "TEntry": {
                "configure": {
                    "foreground": DARK_TEXT,
                    "fieldbackground": DARK_SURFACE_ALT,
                    "insertcolor": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "padding": (8, 6),
                },
                "map": {
                    "bordercolor": [("focus", DARK_ACCENT), ("disabled", DARK_BORDER)],
                    "foreground": [("disabled", DARK_DISABLED_TEXT)],
                    "fieldbackground": [("disabled", DARK_SURFACE)],
                },
            },
            "TRadiobutton": {
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
            "TCombobox": {
                "configure": {
                    "foreground": DARK_TEXT,
                    "fieldbackground": DARK_SURFACE,
                    "background": DARK_SURFACE,
                    "arrowcolor": DARK_TEXT,
                    "bordercolor": DARK_BORDER,
                    "lightcolor": DARK_BORDER,
                    "darkcolor": DARK_BORDER,
                    "padding": (8, 6),
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
                    "rowheight": 32,
                    "padding": 2,
                },
                "map": {
                    "background": [("selected", DARK_ACCENT)],
                    "foreground": [("selected", DARK_TEXT)],
                },
            },
            "Treeview.Heading": {
                "configure": {
                    "background": DARK_SURFACE,
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


def configure_app_styles(
    style: ttk.Style,
    dark_mode: bool,
    high_contrast: bool = False,
) -> None:
    """Configure the named styles shared by the main shell and plugin dialogs."""
    if high_contrast:
        background = LIGHT_BG
        sidebar = LIGHT_BG
        surface = LIGHT_LIST_BG
        surface_alt = LIGHT_LIST_BG
        border = LIGHT_TEXT
        text = LIGHT_TEXT
        muted = LIGHT_TEXT
        accent = LIGHT_SELECTION_BG
        accent_hover = LIGHT_SELECTION_BG
        accent_pressed = LIGHT_SELECTION_BG
        accent_surface = LIGHT_LIST_BG
        success = LIGHT_TEXT
    elif dark_mode:
        background = DARK_BG
        sidebar = DARK_SIDEBAR
        surface = DARK_SURFACE
        surface_alt = DARK_SURFACE_ALT
        border = DARK_BORDER
        text = DARK_TEXT
        muted = DARK_MUTED_TEXT
        accent = DARK_ACCENT
        accent_hover = DARK_ACCENT_HOVER
        accent_pressed = DARK_ACCENT_PRESSED
        accent_surface = DARK_ACCENT_SURFACE
        success = DARK_SUCCESS
    else:
        background = LIGHT_BG
        sidebar = LIGHT_SIDEBAR
        surface = LIGHT_SURFACE
        surface_alt = LIGHT_SURFACE_ALT
        border = LIGHT_BORDER
        text = LIGHT_TEXT
        muted = LIGHT_MUTED_TEXT
        accent = LIGHT_ACCENT
        accent_hover = LIGHT_ACCENT_HOVER
        accent_pressed = LIGHT_ACCENT_PRESSED
        accent_surface = LIGHT_ACCENT_SURFACE
        success = LIGHT_SUCCESS

    style.configure("App.TFrame", background=background)
    style.configure("Sidebar.TFrame", background=sidebar)
    style.configure("Content.TFrame", background=background)
    style.configure("Card.TFrame", background=surface, relief="solid", borderwidth=1)
    style.configure(
        "Card.TLabelframe",
        background=surface,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        relief="solid",
        borderwidth=1,
        padding=14,
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=surface,
        foreground=text,
        font=("Segoe UI Variable", 10, "bold"),
    )
    style.configure(
        "AppTitle.TLabel",
        background=sidebar,
        foreground=text,
        font=("Segoe UI Variable", 15, "bold"),
    )
    style.configure(
        "AppSubtitle.TLabel",
        background=sidebar,
        foreground=muted,
        font=("Segoe UI Variable", 8),
    )
    style.configure(
        "PageTitle.TLabel",
        background=background,
        foreground=text,
        font=("Segoe UI Variable", 18, "bold"),
    )
    style.configure(
        "PageSubtitle.TLabel",
        background=background,
        foreground=muted,
        font=("Segoe UI Variable", 9),
    )
    style.configure("SectionTitle.TLabel", foreground=text, font=("Segoe UI Variable", 10, "bold"))
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Card.TLabel", background=surface, foreground=text)
    style.configure("CardMuted.TLabel", background=surface, foreground=muted)
    style.configure(
        "Stat.TFrame",
        background=surface,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        relief="solid",
        borderwidth=1,
        padding=14,
    )
    style.configure(
        "StatLabel.TLabel",
        background=surface,
        foreground=muted,
        font=("Segoe UI Variable", 8, "bold"),
    )
    style.configure(
        "StatValue.TLabel",
        background=surface,
        foreground=text,
        font=("Segoe UI Variable", 12, "bold"),
    )
    style.configure(
        "RouteCard.TFrame",
        background=surface_alt,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        relief="solid",
        borderwidth=1,
        padding=13,
    )
    style.configure(
        "Selected.RouteCard.TFrame",
        background=surface_alt,
        bordercolor=accent,
        lightcolor=accent,
        darkcolor=accent,
        relief="solid",
        borderwidth=1,
        padding=13,
    )
    style.configure(
        "RouteName.TLabel",
        background=surface_alt,
        foreground=text,
        font=("Segoe UI Variable", 10, "bold"),
    )
    style.configure("RouteMuted.TLabel", background=surface_alt, foreground=muted)
    style.configure(
        "RouteValue.TLabel",
        background=surface_alt,
        foreground=text,
        font=("Segoe UI Variable", 14, "bold"),
    )
    style.configure(
        "RouteState.TLabel",
        background=surface_alt,
        foreground=success,
        font=("Segoe UI Variable", 8, "bold"),
    )
    style.configure(
        "Selected.RouteName.TLabel",
        background=surface_alt,
        foreground=text,
        font=("Segoe UI Variable", 10, "bold"),
    )
    style.configure("Selected.RouteMuted.TLabel", background=surface_alt, foreground=muted)
    style.configure(
        "Selected.RouteValue.TLabel",
        background=surface_alt,
        foreground=text,
        font=("Segoe UI Variable", 14, "bold"),
    )
    style.configure(
        "Selected.RouteState.TLabel",
        background=surface_alt,
        foreground=success,
        font=("Segoe UI Variable", 8, "bold"),
    )
    style.configure(
        "Card.TCheckbutton",
        background=surface,
        foreground=text,
        focuscolor=accent,
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", surface), ("disabled", surface)],
        foreground=[("disabled", muted)],
    )
    style.configure("Success.TLabel", foreground=success, font=("Segoe UI Variable", 9, "bold"))
    style.configure(
        "Nav.TButton",
        background=sidebar,
        foreground=muted,
        borderwidth=0,
        focusthickness=1,
        focuscolor=accent,
        anchor="w",
        padding=(12, 9),
        font=("Segoe UI Variable", 9),
    )
    style.map(
        "Nav.TButton",
        background=[("active", surface_alt), ("pressed", accent_surface)],
        foreground=[("active", text), ("pressed", text), ("disabled", muted)],
    )
    style.configure(
        "Selected.Nav.TButton",
        background=accent_surface,
        foreground=text,
        borderwidth=0,
        anchor="w",
        padding=(12, 9),
        font=("Segoe UI Variable", 9, "bold"),
    )
    style.map(
        "Selected.Nav.TButton",
        background=[("active", accent_surface), ("pressed", accent_surface)],
        foreground=[("active", text), ("pressed", text)],
    )
    style.configure(
        "Accent.TButton",
        background=accent,
        foreground="#07111D" if dark_mode and not high_contrast else LIGHT_SELECTION_TEXT,
        bordercolor=accent,
        lightcolor=accent,
        darkcolor=accent,
        padding=(12, 7),
        font=("Segoe UI Variable", 9, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[("active", accent_hover), ("pressed", accent_pressed), ("disabled", surface_alt)],
        bordercolor=[("active", accent_hover), ("pressed", accent_pressed)],
        foreground=[("disabled", muted)],
    )
    style.configure(
        "Toggle.TButton",
        background=accent,
        foreground="#07111D" if dark_mode and not high_contrast else LIGHT_SELECTION_TEXT,
        bordercolor=accent,
        lightcolor=accent,
        darkcolor=accent,
        padding=(10, 4),
        font=("Segoe UI Variable", 8, "bold"),
    )
    style.map(
        "Toggle.TButton",
        background=[("active", accent_hover), ("pressed", accent_pressed)],
        bordercolor=[("active", accent_hover), ("pressed", accent_pressed)],
    )
    style.configure("Quiet.TButton", padding=(10, 6))
    style.configure("Dialog.TFrame", background=background)
    style.configure("DialogTitle.TLabel", foreground=text, font=("Segoe UI Variable", 14, "bold"))
    style.configure("DialogSubtitle.TLabel", foreground=muted, font=("Segoe UI Variable", 9))
    style.configure("DialogActions.TFrame", background=background)
    style.configure("Inset.TFrame", background=surface_alt, relief="solid", borderwidth=1)
    style.configure("Inset.TLabel", background=surface_alt, foreground=text)
    style.configure("InsetMuted.TLabel", background=surface_alt, foreground=muted)
    style.configure("Badge.TLabel", background=accent_surface, foreground=text, padding=(8, 3))
    style.configure("Modern.Treeview", background=surface, fieldbackground=surface, foreground=text, rowheight=34)
    style.map(
        "Modern.Treeview",
        background=[("selected", accent_surface)],
        foreground=[("selected", text)],
    )
    style.configure("Modern.Treeview.Heading", background=surface_alt, foreground=text, padding=(8, 7))


def configure_style_metrics(style: ttk.Style, dpi: int) -> None:
    """Scale named-style geometry for the DPI of the main Tk window."""
    scale = max(96, dpi) / 96

    def px(value: int) -> int:
        return max(1, round(value * scale))

    style.configure("Card.TLabelframe", padding=px(14))
    style.configure("Stat.TFrame", padding=px(14))
    style.configure("RouteCard.TFrame", padding=px(13))
    style.configure("Selected.RouteCard.TFrame", padding=px(13))
    style.configure("Nav.TButton", padding=(px(12), px(9)))
    style.configure("Selected.Nav.TButton", padding=(px(12), px(9)))
    style.configure("Accent.TButton", padding=(px(12), px(7)))
    style.configure("Toggle.TButton", padding=(px(10), px(4)))
    style.configure("Quiet.TButton", padding=(px(10), px(6)))
    style.configure("Badge.TLabel", padding=(px(8), px(3)))
    style.configure("Modern.Treeview", rowheight=px(34))
    style.configure("Modern.Treeview.Heading", padding=(px(8), px(7)))


def apply_theme(
    style: ttk.Style,
    dark_mode: bool,
    high_contrast: bool = False,
) -> str:
    if dark_mode and not high_contrast:
        ensure_dark_theme(style)
        style.theme_use(DARK_THEME_NAME)
        configure_app_styles(style, True, False)
        return DARK_THEME_NAME

    preferred_theme = choose_preferred_theme(style.theme_names())
    if preferred_theme is not None:
        style.theme_use(preferred_theme)
        configure_app_styles(style, False, high_contrast)
        return preferred_theme
    active_theme = style.theme_use()
    configure_app_styles(style, False, high_contrast)
    return active_theme


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
