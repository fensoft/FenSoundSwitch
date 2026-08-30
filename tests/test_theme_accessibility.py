from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from gui import MonitorVolumeApp
from overlay import VolumeOverlay
from theme import (
    DARK_BG,
    DARK_STATUS_BG,
    LIGHT_BG,
    LIGHT_TEXT,
    WindowsThemeState,
    apply_color_scheme,
    apply_theme,
    apply_window_chrome,
    read_windows_theme_state,
)
from windows_platform import (
    HCF_HIGHCONTRASTON,
    USER_DEFAULT_SCREEN_DPI,
    get_window_dpi,
    is_high_contrast_enabled,
)


class WindowsThemeTests(unittest.TestCase):
    def test_high_contrast_overrides_the_custom_dark_theme(self) -> None:
        with patch("theme.is_high_contrast_enabled", return_value=True), patch(
            "theme.is_windows_dark_mode_enabled",
            return_value=True,
        ):
            state = read_windows_theme_state()

        self.assertEqual(state, WindowsThemeState(dark_mode=False, high_contrast=True))

        style = Mock()
        style.theme_names.return_value = ("vista", "clam")
        self.assertEqual(apply_theme(style, state.dark_mode, state.high_contrast), "vista")
        style.theme_use.assert_called_once_with("vista")
        style.theme_create.assert_not_called()

    def test_dark_theme_styles_the_plugin_treeview(self) -> None:
        style = Mock()
        style.theme_names.return_value = ("clam",)
        apply_theme(style, True, False)
        settings = style.theme_create.call_args.kwargs["settings"]
        self.assertIn("Treeview", settings)
        self.assertIn("Treeview.Heading", settings)

    def test_color_scheme_can_return_from_dark_to_system_colors(self) -> None:
        root = Mock()
        status_bar = Mock()

        apply_color_scheme(root, status_bar, dark_mode=True)
        root.configure.assert_called_with(bg=DARK_BG)
        status_bar.configure.assert_called_with(bg=DARK_STATUS_BG, fg="#F2F2F2")

        apply_color_scheme(root, status_bar, dark_mode=False)
        root.configure.assert_called_with(bg=LIGHT_BG)
        status_bar.configure.assert_called_with(bg=LIGHT_BG, fg=LIGHT_TEXT)

    def test_light_theme_clears_dark_title_bar_chrome(self) -> None:
        root = Mock()
        root.winfo_id.return_value = 7
        with patch("theme.get_toplevel_window_handle", return_value=42), patch(
            "theme.set_window_dark_mode"
        ) as set_dark_mode:
            apply_window_chrome(root, False)

        set_dark_mode.assert_called_once_with(42, False)

    def test_live_gui_theme_update_restyles_every_surface(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._theme_after_id = "theme-timer"
        app.style = Mock()
        app.root = Mock()
        app.status_bar = Mock()
        app._overlay = Mock()
        app._plugin_manager = Mock()
        app._apply_scaled_layout = Mock()
        app._resize_for_content = Mock()
        state = WindowsThemeState(dark_mode=False, high_contrast=True)

        with patch("gui.read_windows_theme_state", return_value=state), patch(
            "gui.apply_theme",
            return_value="vista",
        ) as apply_ttk_theme, patch("gui.apply_color_scheme") as apply_colors, patch(
            "gui.apply_window_chrome"
        ) as apply_chrome:
            app._apply_live_theme()

        self.assertEqual(app.theme_state, state)
        self.assertFalse(app.dark_mode)
        self.assertTrue(app.high_contrast)
        self.assertEqual(app.active_theme, "vista")
        apply_ttk_theme.assert_called_once_with(app.style, False, True)
        apply_colors.assert_called_once_with(app.root, app.status_bar, False, True)
        app._overlay.apply_theme.assert_called_once_with(False, True)
        app._plugin_manager.apply_theme.assert_called_once_with(False)
        apply_chrome.assert_called_once_with(app.root, False)
        app._apply_scaled_layout.assert_called_once_with()
        app._resize_for_content.assert_called_once_with()


class NativeAccessibilityTests(unittest.TestCase):
    def test_high_contrast_query_reads_the_native_flag(self) -> None:
        def populate_high_contrast(
            _action: int,
            _size: int,
            high_contrast_pointer: object,
            _flags: int,
        ) -> bool:
            high_contrast_pointer._obj.dwFlags = HCF_HIGHCONTRASTON  # type: ignore[attr-defined]
            return True

        with patch(
            "windows_platform.user32.SystemParametersInfoW",
            side_effect=populate_high_contrast,
        ):
            self.assertTrue(is_high_contrast_enabled())

    def test_window_dpi_has_a_safe_default_and_uses_the_native_value(self) -> None:
        self.assertEqual(get_window_dpi(0), USER_DEFAULT_SCREEN_DPI)
        with patch("windows_platform.user32.GetDpiForWindow", return_value=144):
            self.assertEqual(get_window_dpi(42), 144)
        with patch("windows_platform.user32.GetDpiForWindow", return_value=0):
            self.assertEqual(get_window_dpi(42), USER_DEFAULT_SCREEN_DPI)


class KeyboardAccessibilityTests(unittest.TestCase):
    def test_keyboard_shortcuts_cover_every_primary_control(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app.volume_scale = Mock()
        app.change_speed_combo = Mock()
        app.refresh_button = Mock()
        app.configure_plugins_button = Mock()

        app._bind_keyboard_shortcuts()

        sequences = {call.args[0] for call in app.root.bind.call_args_list}
        self.assertEqual(
            sequences,
            {
                "<Alt-v>",
                "<Alt-c>",
                "<Alt-r>",
                "<Control-r>",
                "<F5>",
                "<Alt-p>",
                "<Escape>",
            },
        )

    def test_focus_and_invoke_helpers_skip_disabled_controls(self) -> None:
        enabled = Mock()
        enabled.instate.return_value = True
        self.assertEqual(MonitorVolumeApp._focus_control(enabled), "break")
        self.assertEqual(MonitorVolumeApp._invoke_control(enabled), "break")
        enabled.focus_set.assert_called_once_with()
        enabled.invoke.assert_called_once_with()

        disabled = Mock()
        disabled.instate.return_value = False
        MonitorVolumeApp._focus_control(disabled)
        MonitorVolumeApp._invoke_control(disabled)
        disabled.focus_set.assert_not_called()
        disabled.invoke.assert_not_called()

    def test_home_end_and_page_keys_use_explicit_volume_actions(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._busy = False
        app._volume_write_inflight = False
        app._control_ready = Mock(return_value=True)
        app._current_target_volume = Mock(return_value=50)
        app._request_volume_target = Mock()
        app._show_volume_overlay = Mock()
        app._show_unavailable_error = Mock()
        app.adjust_selected_volume = Mock()

        self.assertEqual(app._set_volume_from_keyboard(100), "break")
        app._request_volume_target.assert_called_once_with(100)
        self.assertEqual(app._adjust_volume_from_keyboard(-10), "break")
        app.adjust_selected_volume.assert_called_once_with(-10)


class ScalingTests(unittest.TestCase):
    def test_pixel_scaling_tracks_the_current_window_dpi(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._ui_dpi = 144
        self.assertEqual(app._scaled_px(10), 15)

    def test_configure_refresh_reflows_only_when_dpi_changes(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._scale_after_id = "scale-timer"
        app._ui_dpi = 96
        app.root = Mock()
        app._apply_scaled_layout = Mock()
        app._resize_for_content = Mock()

        with patch("gui.get_tk_window_dpi", return_value=144):
            app._refresh_ui_scaling()

        self.assertEqual(app._ui_dpi, 144)
        app._apply_scaled_layout.assert_called_once_with()
        app._resize_for_content.assert_called_once_with()


class OverlayThemeTests(unittest.TestCase):
    def test_high_contrast_overlay_is_opaque_and_uses_system_colors(self) -> None:
        palette = VolumeOverlay._get_palette(dark_mode=True, high_contrast=True)
        self.assertEqual(palette.background, "SystemWindow")
        self.assertEqual(palette.text, "SystemWindowText")
        self.assertEqual(palette.alpha, 1.0)


if __name__ == "__main__":
    unittest.main()
