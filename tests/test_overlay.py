from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ddc import MonitorIdentity, SavedMonitorSelection
from gui import MonitorVolumeApp
from plugins.windows11_overlay_plugin import (
    StatusRowWidgets,
    VolumeOverlay,
    calculate_overlay_geometry,
    format_volume_status,
    format_volume_status_rows,
)
from plugins.macos_overlay_plugin import MacOSVolumeOverlay
from plugin_api import VolumeStatus
from windows_platform import (
    DisplayArea,
    GWL_EXSTYLE,
    SWP_FRAMECHANGED,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_NOZORDER,
    SWP_SHOWWINDOW,
    ScreenRect,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    configure_no_activate_window,
    enumerate_display_areas,
    get_overlay_display_area,
    select_display_area,
    show_window_no_activate,
)


def make_display_area(
    name: str,
    bounds: tuple[int, int, int, int],
    work_area: tuple[int, int, int, int] | None = None,
    *,
    scale_percent: int = 100,
    primary: bool = False,
) -> DisplayArea:
    if work_area is None:
        work_area = bounds
    return DisplayArea(
        display_device_name=name,
        bounds=ScreenRect(*bounds),
        work_area=ScreenRect(*work_area),
        scale_percent=scale_percent,
        primary=primary,
    )


class OverlayPlacementTests(unittest.TestCase):
    def test_multi_status_rows_prioritize_current_and_preserve_unavailable_reason(self) -> None:
        rows = format_volume_status_rows(
            (
                VolumeStatus("receiver", "Receiver", 31, routed=True),
                VolumeStatus("monitor", "Monitor", None, active=True, reason="Disconnected"),
            ),
            "monitor",
        )

        self.assertEqual(rows, ("Monitor: Unavailable (Disconnected)", "Receiver: 31%"))

    def test_status_rows_keep_duplicate_output_routes_distinguishable(self) -> None:
        rows = format_volume_status_rows(
            (
                VolumeStatus("route-living-room", "Living room", 31, routed=True),
                VolumeStatus("route-office", "Office", 42, routed=True),
            ),
            "route-office",
        )

        self.assertEqual(rows, ("Office: 42%", "Living room: 31%"))

    def test_status_formatter_preserves_unavailable_reason(self) -> None:
        self.assertEqual(
            format_volume_status(VolumeStatus("route", "Office", None, reason="Offline")),
            "Unavailable (Offline)",
        )
    def test_native_display_inventory_captures_bounds_work_area_and_scale(self) -> None:
        def enumerate_monitors(_hdc: object, _clip: object, callback: object, _data: int) -> bool:
            return bool(callback(123, None, None, 0))

        def populate_monitor_info(_hmonitor: object, info_pointer: object) -> bool:
            info = info_pointer._obj  # type: ignore[attr-defined]
            info.rcMonitor.left = -1920
            info.rcMonitor.top = 0
            info.rcMonitor.right = 0
            info.rcMonitor.bottom = 1080
            info.rcWork.left = -1920
            info.rcWork.top = 0
            info.rcWork.right = 0
            info.rcWork.bottom = 1040
            info.dwFlags = 1
            info.szDevice = r"\\.\DISPLAY2"
            return True

        def populate_scale(_hmonitor: object, scale_pointer: object) -> int:
            scale_pointer._obj.value = 150  # type: ignore[attr-defined]
            return 0

        with patch(
            "windows_platform.user32.EnumDisplayMonitors",
            side_effect=enumerate_monitors,
        ), patch(
            "windows_platform.user32.GetMonitorInfoW",
            side_effect=populate_monitor_info,
        ), patch(
            "windows_platform.shcore.GetScaleFactorForMonitor",
            side_effect=populate_scale,
        ):
            display_areas = enumerate_display_areas()

        self.assertEqual(
            display_areas,
            [
                make_display_area(
                    r"\\.\DISPLAY2",
                    (-1920, 0, 0, 1080),
                    (-1920, 0, 0, 1040),
                    scale_percent=150,
                    primary=True,
                )
            ],
        )

    def test_bottom_centers_on_a_negative_coordinate_scaled_work_area(self) -> None:
        display_area = make_display_area(
            r"\\.\DISPLAY2",
            (-2560, 0, 0, 1440),
            (-2560, 0, 0, 1400),
            scale_percent=150,
        )

        placement = calculate_overlay_geometry(210, 122, display_area)

        self.assertEqual((placement.x, placement.y), (-1385, 1146))
        self.assertEqual((placement.width, placement.height), (210, 122))

    def test_oversized_overlay_is_clamped_inside_the_scaled_work_area(self) -> None:
        display_area = make_display_area(
            r"\\.\DISPLAY1",
            (100, 50, 300, 220),
            (100, 50, 300, 200),
        )

        placement = calculate_overlay_geometry(500, 500, display_area)

        self.assertEqual(
            (placement.x, placement.y, placement.width, placement.height),
            (124, 82, 152, 30),
        )

    def test_cursor_display_wins_over_the_selected_display(self) -> None:
        primary = make_display_area(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True)
        selected = make_display_area(r"\\.\DISPLAY2", (1920, 0, 3840, 1080))

        result = select_display_area(
            [primary, selected],
            r"\\.\display2",
            (100, 100),
        )

        self.assertIs(result, primary)

    def test_cursor_display_is_used_when_the_selection_is_missing(self) -> None:
        primary = make_display_area(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True)
        cursor_display = make_display_area(r"\\.\DISPLAY2", (-1920, 0, 0, 1080))

        result = select_display_area(
            [primary, cursor_display],
            r"\\.\DISPLAY9",
            (-500, 400),
        )

        self.assertIs(result, cursor_display)

    def test_selected_display_is_the_fallback_without_a_cursor(self) -> None:
        secondary = make_display_area(r"\\.\DISPLAY2", (-1920, 0, 0, 1080))
        primary = make_display_area(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True)

        self.assertIs(
            select_display_area([secondary, primary], r"\\.\DISPLAY2", None),
            secondary,
        )

    def test_native_resolver_always_reads_the_cursor_before_using_selection(self) -> None:
        primary = make_display_area(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True)
        secondary = make_display_area(r"\\.\DISPLAY2", (1920, 0, 3840, 1080))

        def set_cursor(point_pointer: object) -> bool:
            point_pointer._obj.x = 2500  # type: ignore[attr-defined]
            point_pointer._obj.y = 300  # type: ignore[attr-defined]
            return True

        with patch(
            "windows_platform.enumerate_display_areas",
            return_value=[primary, secondary],
        ), patch("windows_platform.user32.GetCursorPos", side_effect=set_cursor) as get_cursor:
            self.assertIs(get_overlay_display_area(r"\\.\DISPLAY2"), secondary)
            get_cursor.assert_called_once()

            self.assertIs(get_overlay_display_area(r"\\.\DISPLAY9"), secondary)
            self.assertEqual(get_cursor.call_count, 2)


class NoActivateWindowTests(unittest.TestCase):
    def test_no_activate_style_is_preserved_and_applied_without_activation(self) -> None:
        existing_style = 0x20
        with patch(
            "windows_platform.user32.GetWindowLongW",
            return_value=existing_style,
        ), patch(
            "windows_platform.user32.SetWindowLongW",
            return_value=existing_style,
        ) as set_style, patch(
            "windows_platform.user32.SetWindowPos",
            return_value=True,
        ) as set_position:
            self.assertTrue(configure_no_activate_window(42))

        set_style.assert_called_once_with(
            set_style.call_args.args[0],
            GWL_EXSTYLE,
            existing_style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )
        flags = set_position.call_args.args[-1]
        self.assertEqual(
            flags,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOZORDER
            | SWP_NOACTIVATE
            | SWP_FRAMECHANGED,
        )

    def test_native_show_is_topmost_but_explicitly_no_activate(self) -> None:
        with patch("windows_platform.user32.SetWindowPos", return_value=True) as set_position:
            self.assertTrue(show_window_no_activate(42, -100, 50, 210, 122))

        args = set_position.call_args.args
        self.assertEqual(args[2:6], (-100, 50, 210, 122))
        self.assertEqual(args[-1] & SWP_NOACTIVATE, SWP_NOACTIVATE)
        self.assertEqual(args[-1] & SWP_SHOWWINDOW, SWP_SHOWWINDOW)

    def test_overlay_never_deiconifies_if_no_activate_style_fails(self) -> None:
        volume_overlay = VolumeOverlay.__new__(VolumeOverlay)
        volume_overlay.window = Mock()
        volume_overlay.window.winfo_reqwidth.return_value = 210
        volume_overlay.window.winfo_reqheight.return_value = 122
        volume_overlay.window.winfo_id.return_value = 7
        volume_overlay._hide_after_id = None
        display_area = make_display_area(r"\\.\DISPLAY1", (0, 0, 1920, 1040))

        with patch("plugins.windows11_overlay_plugin.get_overlay_display_area", return_value=display_area), patch(
            "plugins.windows11_overlay_plugin.get_toplevel_window_handle",
            return_value=42,
        ), patch("plugins.windows11_overlay_plugin.configure_no_activate_window", return_value=False), patch(
            "plugins.windows11_overlay_plugin.show_window_no_activate"
        ) as show_native:
            volume_overlay._show_window(1400, r"\\.\DISPLAY1")

        volume_overlay.window.deiconify.assert_not_called()
        volume_overlay.window.withdraw.assert_called_once_with()
        show_native.assert_not_called()

    def test_overlay_show_path_has_no_focus_or_lift_call(self) -> None:
        volume_overlay = VolumeOverlay.__new__(VolumeOverlay)
        volume_overlay.window = Mock()
        volume_overlay.window.winfo_reqwidth.return_value = 210
        volume_overlay.window.winfo_reqheight.return_value = 122
        volume_overlay.window.winfo_id.return_value = 7
        volume_overlay.window.after.return_value = "hide-timer"
        volume_overlay._hide_after_id = None
        display_area = make_display_area(r"\\.\DISPLAY2", (-1920, 0, 0, 1040))

        with patch("plugins.windows11_overlay_plugin.get_overlay_display_area", return_value=display_area), patch(
            "plugins.windows11_overlay_plugin.get_toplevel_window_handle",
            return_value=42,
        ), patch("plugins.windows11_overlay_plugin.configure_no_activate_window", return_value=True), patch(
            "plugins.windows11_overlay_plugin.show_window_no_activate",
            return_value=True,
        ) as show_native:
            volume_overlay._show_window(1400, r"\\.\DISPLAY2")

        volume_overlay.window.geometry.assert_called_once_with("210x122-1065+830")
        volume_overlay.window.deiconify.assert_called_once_with()
        volume_overlay.window.lift.assert_not_called()
        volume_overlay.window.focus_force.assert_not_called()
        show_native.assert_called_once_with(42, -1065, 830, 210, 122)
        self.assertEqual(volume_overlay._hide_after_id, "hide-timer")


class OverlayGUITests(unittest.TestCase):
    def test_current_status_shows_centered_volume_header_route_value_and_ttk_bar(self) -> None:
        overlay = VolumeOverlay.__new__(VolumeOverlay)
        overlay._palette = Mock(text="text")
        overlay.title_var = Mock()
        overlay.route_var = Mock()
        overlay.value_var = Mock()
        overlay.error_var = Mock()
        overlay.route_label = Mock()
        overlay.route_label.winfo_manager.return_value = ""
        overlay.value_label = Mock()
        overlay.value_label.winfo_manager.return_value = ""
        overlay.error_label = Mock()
        overlay.progress = Mock()
        overlay.progress.winfo_manager.return_value = "pack"
        overlay._hide_status_rows = Mock()

        overlay._show_current_status(VolumeStatus("route", "Office", 42, routed=True))

        overlay.title_var.set.assert_called_once_with("Volume")
        overlay.route_var.set.assert_called_once_with("Office")
        overlay.value_var.set.assert_called_once_with("42%")
        overlay.value_label.pack.assert_called_once_with(anchor="w", pady=(1, 8))
        overlay.progress.configure.assert_called_once_with(value=42)
        overlay.error_label.pack.assert_not_called()

    def test_all_routed_statuses_create_ttk_bar_for_each_available_route(self) -> None:
        overlay = VolumeOverlay.__new__(VolumeOverlay)
        overlay._palette = Mock(background="bg", subtext="sub", text="text", error="error")
        overlay.status_rows = Mock()
        overlay._status_row_widgets = []

        rows = [
            StatusRowWidgets(Mock(), Mock(), Mock(), None),
            StatusRowWidgets(Mock(), Mock(), Mock(), None),
        ]
        with patch.object(overlay, "_create_status_row", side_effect=rows), patch(
            "plugins.windows11_overlay_plugin.ttk.Progressbar", side_effect=[Mock(), Mock()]
        ) as progressbar:
            overlay._update_status_rows(
                (
                    VolumeStatus("one", "Office", 42, routed=True),
                    VolumeStatus("two", "Living room", 31, routed=True),
                )
            )

        self.assertEqual(progressbar.call_count, 2)
        self.assertEqual(
            [row.progress.configure.call_args.kwargs["value"] for row in rows], [42, 31]
        )

    def test_all_routed_statuses_keep_the_volume_header_and_named_rows(self) -> None:
        overlay = VolumeOverlay.__new__(VolumeOverlay)
        overlay.title_var = Mock()
        overlay.route_var = Mock()
        overlay.error_var = Mock()
        overlay.route_label = Mock()
        overlay.value_label = Mock()
        overlay.error_label = Mock()
        overlay.progress = Mock()
        overlay.status_rows = Mock()
        overlay.status_rows.winfo_manager.return_value = ""
        overlay._update_status_rows = Mock()
        statuses = (VolumeStatus("route", "Office", 42, routed=True),)

        overlay._show_all_statuses(statuses)

        overlay.title_var.set.assert_called_once_with("Volume")
        overlay._update_status_rows.assert_called_once_with(statuses)
        overlay.status_rows.pack.assert_called_once_with(fill="x")

    def test_unavailable_routed_row_has_no_progress_widget(self) -> None:
        overlay = VolumeOverlay.__new__(VolumeOverlay)
        overlay._palette = Mock(background="bg", subtext="sub", text="text", error="error")
        progress = Mock()
        row = StatusRowWidgets(Mock(), Mock(), Mock(), progress)

        overlay._configure_status_row(row, VolumeStatus("route", "Office", None, reason="Offline"))

        progress.destroy.assert_called_once_with()
        self.assertIsNone(row.progress)
        row.value_label.configure.assert_any_call(text="Unavailable (Offline)")

    def test_renderer_selects_the_provider_from_a_routed_change(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        provider = Mock()
        status = VolumeStatus("route-one", "Desk monitor", 42, routed=True)
        app._closing = False
        app._overlay = Mock()
        app.current_volume = None
        app._overlay.select_statuses.return_value = (status,)
        app._plugin_manager = Mock()
        app._plugin_manager.volume_provider_id.return_value = "route-one"
        app._get_volume_statuses = lambda: (status,)
        app._selected_display_device_name = Mock(return_value=None)
        app._render_overlay = Mock()

        app._show_volume_overlay(provider=provider)

        app._render_overlay.assert_called_once_with(
            "show_statuses",
            (status,),
            "route-one",
            preferred_display_device_name=None,
        )

    def test_macos_renderer_uses_a_distinct_hud_palette_and_all_routes(self) -> None:
        palette = MacOSVolumeOverlay._get_palette(dark_mode=True, high_contrast=False)
        windows_palette = VolumeOverlay._get_palette(dark_mode=True, high_contrast=False)
        statuses = (VolumeStatus("route-one", "Desk", 42, routed=True),)
        self.assertNotEqual(palette.accent, windows_palette.accent)
        self.assertEqual(MacOSVolumeOverlay.__dict__["select_statuses"](Mock(), statuses, "route-one"), statuses)

    def test_renderer_callback_failure_updates_status_without_affecting_routes(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        overlay = Mock(spec=VolumeOverlay)
        overlay.show.side_effect = RuntimeError("native show failed")
        app._overlay = overlay
        app._set_status = Mock()

        app._render_overlay("show", 42)

        self.assertIsNone(app._overlay)
        app._set_status.assert_called_once_with(
            "Volume overlay failed: native show failed. Routes remain available."
        )
        overlay.close.assert_called_once_with()

    def test_gui_passes_the_selected_windows_display_to_the_overlay(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._overlay = Mock()
        app.current_volume = 47
        app._get_volume_statuses = lambda: ()
        app.selected_key = SavedMonitorSelection(
            "Monitor",
            MonitorIdentity("device-path", "DEL", 1, "SERIAL"),
        )
        monitor = Mock()
        monitor.selection_key = app.selected_key
        monitor.display_device_name = r"\\.\DISPLAY2"
        app.monitors = [monitor]

        app._show_volume_overlay()

        app._overlay.show.assert_called_once_with(
            47,
            preferred_display_device_name=r"\\.\DISPLAY2",
        )

    def test_gui_falls_back_to_cursor_placement_without_a_current_match(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._overlay = Mock()
        app._control_unavailable_reason = "Monitor disconnected."
        app.selected_key = SavedMonitorSelection(
            "Monitor",
            MonitorIdentity("missing-path"),
        )
        app.monitors = []
        app._set_status = Mock()

        app._show_unavailable_error()

        app._overlay.show_error.assert_called_once_with(
            "Monitor disconnected.",
            preferred_display_device_name=None,
        )


if __name__ == "__main__":
    unittest.main()
