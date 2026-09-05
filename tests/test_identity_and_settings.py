from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings
import windows_platform
from ddc import (
    DDCError,
    MonitorIdentity,
    MonitorRef,
    SavedMonitorSelection,
    SelectionMatchStatus,
    enumerate_monitors,
    match_selected_monitor,
)
from windows_platform import WindowsDdcMonitor, WindowsMonitorIdentity, parse_edid_identity


class FakeVCP:
    def __init__(self, description: str) -> None:
        self.description = description


class FakeMonitor:
    def __init__(self, description: str) -> None:
        self.vcp = FakeVCP(description)


def make_ref(
    index: int,
    description: str,
    identity: MonitorIdentity | None,
) -> MonitorRef:
    return MonitorRef(
        index=index,
        monitor=FakeMonitor(description),
        description=description,
        description_ordinal=1,
        identity=identity,
        display_device_name=rf"\\.\DISPLAY{index}",
    )


def make_edid(
    manufacturer: str = "DEL",
    product_code: int = 0x1234,
    numeric_serial: int = 0,
    descriptor_serial: str | None = None,
) -> bytes:
    edid = bytearray(128)
    edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    manufacturer_value = 0
    for letter in manufacturer:
        manufacturer_value = (manufacturer_value << 5) | (ord(letter) - ord("A") + 1)
    edid[8:10] = manufacturer_value.to_bytes(2, "big")
    edid[10:12] = product_code.to_bytes(2, "little")
    edid[12:16] = numeric_serial.to_bytes(4, "little")
    if descriptor_serial is not None:
        encoded = descriptor_serial.encode("ascii")[:13].ljust(13, b" ")
        edid[54:72] = b"\x00\x00\x00\xff\x00" + encoded
    return bytes(edid)


class EDIDTests(unittest.TestCase):
    def test_text_serial_is_preferred_and_normalized(self) -> None:
        self.assertEqual(
            parse_edid_identity(make_edid(numeric_serial=123, descriptor_serial=" ab-c12\n")),
            ("DEL", 0x1234, "AB-C12"),
        )

    def test_numeric_serial_is_used_when_text_is_missing(self) -> None:
        self.assertEqual(
            parse_edid_identity(make_edid(numeric_serial=123456)),
            ("DEL", 0x1234, "123456"),
        )

    def test_placeholder_and_malformed_serials_are_absent(self) -> None:
        self.assertEqual(
            parse_edid_identity(make_edid(numeric_serial=0, descriptor_serial="00000000")),
            ("DEL", 0x1234, None),
        )
        self.assertEqual(parse_edid_identity(b"not-edid"), (None, None, None))


class SelectionMatchingTests(unittest.TestCase):
    def test_native_ddc_inventory_pairs_handle_and_identity_in_one_callback(self) -> None:
        identity = WindowsMonitorIdentity(r"\\.\DISPLAY3", "path-3")

        def enumerate_displays(_hdc, _clip, callback, _data):
            return callback(101, None, None, 0)

        def get_physical(_hmonitor, count, physical):
            self.assertEqual(count, 1)
            physical[0].handle = 55
            physical[0].description = "Desk"
            return True

        with patch.object(windows_platform.user32, "EnumDisplayMonitors", side_effect=enumerate_displays), patch.object(
            windows_platform, "_identity_slots_for_hmonitor", return_value=[identity]
        ), patch.object(windows_platform.dxva2, "GetPhysicalMonitorsFromHMONITOR", side_effect=get_physical):
            records = windows_platform.enumerate_windows_ddc_monitors()

        self.assertEqual(records, [WindowsDdcMonitor(55, "Desk", identity)])

    def test_native_ddc_inventory_ignores_a_null_non_ddc_handle(self) -> None:
        identity = WindowsMonitorIdentity(r"\\.\DISPLAY3", "path-3")

        def enumerate_displays(_hdc, _clip, callback, _data):
            return callback(101, None, None, 0) and callback(102, None, None, 0)

        handles = iter((None, 55))
        def get_physical(_hmonitor, _count, physical):
            physical[0].handle = next(handles)
            physical[0].description = "Output"
            return True

        with patch.object(windows_platform.user32, "EnumDisplayMonitors", side_effect=enumerate_displays), patch.object(
            windows_platform, "_identity_slots_for_hmonitor", side_effect=[[None], [identity]]
        ), patch.object(windows_platform.dxva2, "GetPhysicalMonitorsFromHMONITOR", side_effect=get_physical):
            records = windows_platform.enumerate_windows_ddc_monitors()

        self.assertEqual(records, [WindowsDdcMonitor(55, "Output", identity)])

    def test_discovery_correlates_windows_numbers_without_assuming_list_order(self) -> None:
        identities = [
            WindowsMonitorIdentity(r"\\.\DISPLAY1", "path-1", "DEL", 1, "ONE", "Second"),
            WindowsMonitorIdentity(r"\\.\DISPLAY3", "path-3", "DEL", 3, "THREE", "First"),
        ]
        records = [WindowsDdcMonitor(1, "First", identities[1]), WindowsDdcMonitor(2, "Second", identities[0])]
        with patch("ddc.enumerate_windows_ddc_monitors", return_value=records), patch(
            "ddc._monitor_from_windows_record", side_effect=[FakeMonitor("First"), FakeMonitor("Second")]
        ), patch("ddc.enumerate_windows_monitor_identities", side_effect=[identities, list(reversed(identities))]):
            monitors = enumerate_monitors()

        self.assertEqual([monitor.windows_display_number for monitor in monitors], [1, 3])
        self.assertEqual([monitor.identity.device_path for monitor in monitors], ["path-1", "path-3"])

    def test_discovery_orders_labels_by_windows_display_number(self) -> None:
        identities = [
            WindowsMonitorIdentity(r"\\.\DISPLAY1", "path-1"),
            WindowsMonitorIdentity(r"\\.\DISPLAY2", "path-2"),
            WindowsMonitorIdentity(r"\\.\DISPLAY3", "path-3"),
        ]
        records = [
            WindowsDdcMonitor(1, "One", identities[0]),
            WindowsDdcMonitor(2, "Two", identities[1]),
            WindowsDdcMonitor(3, "Three", identities[2]),
        ]
        monitor_by_handle = {1: FakeMonitor("One"), 2: FakeMonitor("Two"), 3: FakeMonitor("Three")}
        with patch("ddc.enumerate_windows_ddc_monitors", return_value=records), patch(
            "ddc._monitor_from_windows_record", side_effect=lambda record: monitor_by_handle[record.handle]
        ), patch("ddc.enumerate_windows_monitor_identities", side_effect=[identities, identities]):
            monitors = enumerate_monitors()

        self.assertEqual([monitor.windows_display_number for monitor in monitors], [1, 2, 3])
        self.assertEqual([monitor.identity.device_path for monitor in monitors], ["path-1", "path-2", "path-3"])

    def test_same_pass_discovery_keeps_duplicate_descriptions_distinct(self) -> None:
        identities = [
            WindowsMonitorIdentity(r"\\.\DISPLAY1", "path-1", monitor_description="Same"),
            WindowsMonitorIdentity(r"\\.\DISPLAY2", "path-2", monitor_description="Same"),
        ]
        records = [WindowsDdcMonitor(1, "Same", identities[0]), WindowsDdcMonitor(2, "Same", identities[1])]
        with patch("ddc.enumerate_windows_ddc_monitors", return_value=records), patch(
            "ddc._monitor_from_windows_record", side_effect=[FakeMonitor("Same"), FakeMonitor("Same")]
        ), patch("ddc.enumerate_windows_monitor_identities", side_effect=[identities, identities]):
            monitors = enumerate_monitors()

        self.assertEqual([monitor.windows_display_number for monitor in monitors], [1, 2])
        self.assertEqual([monitor.identity.device_path for monitor in monitors], ["path-1", "path-2"])

    def test_monitor_label_uses_windows_settings_display_number(self) -> None:
        monitor = MonitorRef(
            index=1,
            monitor=FakeMonitor("Desk"),
            description="Desk",
            description_ordinal=1,
            identity=MonitorIdentity("path", "DEL", 1, "SERIAL"),
            display_device_name=r"\\.\DISPLAY3",
        )

        self.assertEqual(monitor.windows_display_number, 3)
        self.assertEqual(monitor.display_name, "Display 3: Desk - S/N SERIAL")

    def test_monitor_label_falls_back_to_ddc_order_without_windows_mapping(self) -> None:
        monitor = MonitorRef(2, FakeMonitor("Desk"), "Desk", 1)

        self.assertIsNone(monitor.windows_display_number)
        self.assertEqual(monitor.display_name, "DDC 2: Desk - identity unavailable")

    def test_unique_serial_follows_monitor_to_a_new_device_path(self) -> None:
        saved = SavedMonitorSelection(
            "Monitor",
            MonitorIdentity("old-path", "DEL", 1, "SERIAL-A"),
        )
        monitors = [
            make_ref(1, "Monitor", MonitorIdentity("new-path", "DEL", 1, "SERIAL-A")),
        ]
        match = match_selected_monitor(monitors, saved)
        self.assertEqual((match.status, match.index), (SelectionMatchStatus.FOUND, 0))

    def test_duplicate_serial_requires_the_saved_device_path(self) -> None:
        saved = SavedMonitorSelection(
            "Monitor",
            MonitorIdentity("path-b", "DEL", 1, "DUPLICATE"),
        )
        monitors = [
            make_ref(1, "Monitor", MonitorIdentity("path-a", "DEL", 1, "DUPLICATE")),
            make_ref(2, "Monitor", MonitorIdentity("path-b", "DEL", 1, "DUPLICATE")),
        ]
        match = match_selected_monitor(monitors, saved)
        self.assertEqual((match.status, match.index), (SelectionMatchStatus.FOUND, 1))

        moved = SavedMonitorSelection(
            "Monitor",
            MonitorIdentity("missing-path", "DEL", 1, "DUPLICATE"),
        )
        self.assertEqual(
            match_selected_monitor(monitors, moved).status,
            SelectionMatchStatus.AMBIGUOUS,
        )

    def test_no_serial_requires_an_exact_case_insensitive_path(self) -> None:
        saved = SavedMonitorSelection("Monitor", MonitorIdentity("DEVICE-PATH"))
        monitors = [make_ref(1, "Monitor", MonitorIdentity("device-path"))]
        self.assertEqual(match_selected_monitor(monitors, saved).index, 0)

        missing = SavedMonitorSelection("Monitor", MonitorIdentity("another-path"))
        self.assertEqual(
            match_selected_monitor(monitors, missing).status,
            SelectionMatchStatus.MISSING,
        )

    def test_legacy_description_promotes_only_when_unique(self) -> None:
        legacy = SavedMonitorSelection("Monitor", legacy_ordinal=2)
        unique = [make_ref(1, "Monitor", MonitorIdentity("path-a"))]
        match = match_selected_monitor(unique, legacy)
        self.assertEqual(match.status, SelectionMatchStatus.FOUND)
        self.assertTrue(match.should_promote_legacy)

        duplicate = unique + [make_ref(2, "Monitor", MonitorIdentity("path-b"))]
        self.assertEqual(
            match_selected_monitor(duplicate, legacy).status,
            SelectionMatchStatus.AMBIGUOUS,
        )

    def test_unique_legacy_description_matches_without_windows_identity(self) -> None:
        legacy = SavedMonitorSelection("Monitor", legacy_ordinal=1)
        monitors = [make_ref(1, "Monitor", None)]

        match = match_selected_monitor(monitors, legacy)

        self.assertEqual(match.status, SelectionMatchStatus.FOUND)
        self.assertEqual(match.index, 0)
        self.assertFalse(match.should_promote_legacy)

    def test_unverifiable_identity_and_multi_monitor_first_run_fail_closed(self) -> None:
        unverifiable = [make_ref(1, "Monitor", None)]
        self.assertEqual(
            match_selected_monitor(unverifiable, None).status,
            SelectionMatchStatus.UNVERIFIABLE,
        )

        single = [make_ref(1, "Monitor", MonitorIdentity("path-a"))]
        self.assertEqual(match_selected_monitor(single, None).index, 0)

        multiple = [
            make_ref(1, "A", MonitorIdentity("path-a")),
            make_ref(2, "B", MonitorIdentity("path-b")),
        ]
        self.assertEqual(
            match_selected_monitor(multiple, None).status,
            SelectionMatchStatus.NEEDS_SELECTION,
        )

    def test_discovery_rejects_an_identity_snapshot_change(self) -> None:
        fake_monitor = FakeMonitor("Monitor")
        identity_a = object()
        identity_b = object()
        with patch("ddc.enumerate_windows_ddc_monitors", return_value=[WindowsDdcMonitor(1, "Only", identity_a)]), patch(
            "ddc._monitor_from_windows_record", return_value=fake_monitor
        ), patch("ddc.enumerate_windows_monitor_identities", side_effect=[[identity_a], [identity_b]]):
            with self.assertRaisesRegex(DDCError, "Display configuration changed"):
                enumerate_monitors()


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings_path = Path(self.temp_dir.name) / "settings.json"
        self.path_patch = patch.object(settings, "SETTINGS_PATH", self.settings_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def write_json(self, value: object) -> None:
        self.settings_path.write_text(json.dumps(value), encoding="utf-8")

    def test_schema_v2_round_trip(self) -> None:
        selection = SavedMonitorSelection(
            description="Monitor",
            identity=MonitorIdentity("device-path", "DEL", 0x1234, "SERIAL-A"),
        )
        settings.save_selected_monitor_key(selection)
        self.assertEqual(settings.load_selected_monitor_key(), selection)
        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], settings.SCHEMA_VERSION)
        self.assertEqual(payload["change_speed"], "slow")

    def test_absent_current_settings_are_copied_from_legacy_without_mutation(self) -> None:
        current_directory = Path(self.temp_dir.name) / "fensoundswitch"
        legacy_path = Path(self.temp_dir.name) / "windows-ddc" / "settings.json"
        legacy_path.parent.mkdir()
        legacy_payload = {"schema_version": 8, "change_speed": "fast"}
        legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
        with patch.object(settings, "USER_DATA_DIRECTORY", current_directory), patch.object(
            settings, "SETTINGS_PATH", current_directory / "settings.json"
        ), patch.object(settings, "LEGACY_SETTINGS_PATH", legacy_path):
            self.assertEqual(settings.load_change_speed(), "fast")
        self.assertEqual(
            json.loads((current_directory / "settings.json").read_text(encoding="utf-8")),
            legacy_payload,
        )
        self.assertEqual(json.loads(legacy_path.read_text(encoding="utf-8")), legacy_payload)

    def test_change_speed_round_trip_preserves_monitor_selection(self) -> None:
        selection = SavedMonitorSelection(
            description="Monitor",
            identity=MonitorIdentity("device-path", "DEL", 0x1234, "SERIAL-A"),
        )
        settings.save_selected_monitor_key(selection)

        settings.save_change_speed("Medium")

        self.assertEqual(settings.load_change_speed(), "medium")
        self.assertEqual(settings.load_selected_monitor_key(), selection)

        settings.save_change_speed("fast")
        settings.save_selected_monitor_key(selection)
        self.assertEqual(settings.load_change_speed(), "fast")

    def test_change_speed_can_be_saved_before_monitor_selection(self) -> None:
        settings.save_change_speed("medium")

        self.assertEqual(settings.load_change_speed(), "medium")
        self.assertIsNone(settings.load_selected_monitor_key())
        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, {"schema_version": settings.SCHEMA_VERSION, "change_speed": "medium"})

    def test_active_volume_provider_round_trip_preserves_legacy_selection(self) -> None:
        self.write_json({"schema_version": 2, "selected_monitor": {"description": "Monitor", "identity": {"device_path": "path"}}})
        settings.save_active_volume_provider_id("ddc-volume")
        self.assertEqual(settings.load_active_volume_provider_id(), "ddc-volume")
        self.assertIsNotNone(settings.load_selected_monitor_key())

    def test_input_routes_round_trip_preserves_provider_monitor_and_speed(self) -> None:
        selection = SavedMonitorSelection("Monitor", MonitorIdentity("path", "DEL", 1, "SERIAL"))
        settings.save_selected_monitor_key(selection)
        settings.save_change_speed("fast")
        settings.save_active_volume_provider_id("ddc-volume")
        route = settings.VolumeRoute("route-test", "Desk receiver", settings.RouteEndpoint("windows-volume-keys", {}), settings.RouteEndpoint("onkyo-volume", {"host": "receiver"}))
        settings.save_input_routes([route])

        self.assertEqual(settings.load_input_routes(), (route,))
        self.assertEqual(settings.load_active_volume_provider_id(), "ddc-volume")
        self.assertEqual(settings.load_selected_monitor_key(), selection)
        self.assertEqual(settings.load_change_speed(), "fast")

    def test_route_type_round_trips_and_old_routes_default_to_other(self) -> None:
        self.assertEqual(
            tuple(settings.ROUTE_TYPE_LABELS.values()),
            ("Voice", "Headset", "Headphones", "Earbuds", "Speakers", "Soundbar", "TV", "AVR", "Amplifier", "Microphone", "Line-in", "Line-out", "Mixer", "Monitor", "Other"),
        )
        typed = settings.VolumeRoute("route-avr", "Receiver", settings.RouteEndpoint("windows-volume-keys", {}), settings.RouteEndpoint("onkyo-volume", {}), "avr")
        settings.save_input_routes([typed])

        self.assertEqual(settings.load_input_routes(), (typed,))
        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["volume_routes"][0]["route_type"], "avr")

        payload["schema_version"] = 10
        payload["volume_routes"][0].pop("route_type")
        self.write_json(payload)
        self.assertEqual(settings.load_input_routes()[0].route_type, "other")

        payload["volume_routes"][0]["route_type"] = "invalid"
        self.write_json(payload)
        self.assertEqual(settings.load_input_routes(), ())
        payload["volume_routes"][0]["route_type"] = []
        self.write_json(payload)
        self.assertEqual(settings.load_input_routes(), ())

    def test_action_signals_round_trip_preserves_routes_and_ordered_slots(self) -> None:
        route = settings.VolumeRoute("route-test", "Desk", settings.RouteEndpoint("windows-volume-keys", {}), settings.RouteEndpoint("ddc-volume", {}))
        settings.save_input_routes([route])
        signal = settings.ActionSignal(
            "signal-movie",
            "Movie mode",
            settings.ActionHotkeyBinding(settings.HotkeySpec(0, 0x70), False),
            "Movie mode",
            (
                settings.ActionSlot("windows-default-device", "cycle-playback", {}),
                settings.WaitSlot(750),
                settings.ActionSlot("windows-default-device", "cycle-voice-output", {"profile": "voice"}),
            ),
            True,
            (settings.PluginSignalTrigger("mqtt-input", "mqtt-ha", {"profile_id": "p-home", "ha_name": "Movie", "ha_id": "movie"}),),
        )

        settings.save_action_signals([signal])

        self.assertEqual(settings.load_action_signals(), (signal,))
        self.assertEqual(settings.load_input_routes(), (route,))
        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], settings.SCHEMA_VERSION)
        self.assertEqual([slot["kind"] for slot in payload["action_signals"][0]["slots"]], ["action", "wait", "action"])
        self.assertTrue(payload["action_signals"][0]["on_start"])
        self.assertEqual(payload["action_signals"][0]["plugin_triggers"][0]["trigger_id"], "mqtt-ha")

    def test_app_start_is_a_valid_automation_trigger_by_itself(self) -> None:
        signal = settings.ActionSignal(
            "signal-startup",
            "Startup audio",
            settings.ActionHotkeyBinding(None),
            None,
            (settings.WaitSlot(1),),
            True,
        )
        settings.save_action_signals([signal])
        self.assertEqual(settings.load_action_signals(), (signal,))

    def test_action_signals_fail_closed_for_invalid_trigger_wait_or_slots(self) -> None:
        invalid_values = (
            [{"signal_id": "signal-test", "name": "Test", "hotkey": None, "forward_keys": True, "tray_label": None, "slots": [{"kind": "wait", "milliseconds": 1}]}],
            [{"signal_id": "signal-test", "name": "Test", "hotkey": None, "forward_keys": True, "tray_label": "Test", "slots": [{"kind": "wait", "milliseconds": settings.MAX_WAIT_MILLISECONDS + 1}]}],
            [{"signal_id": "signal-test", "name": "Test", "hotkey": None, "forward_keys": True, "tray_label": "Test", "slots": []}],
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.write_json({"schema_version": settings.SCHEMA_VERSION, "action_signals": value})
                self.assertEqual(settings.load_action_signals(), ())

    def test_legacy_overlay_mode_is_read_then_removed_without_touching_other_settings(self) -> None:
        self.write_json({"schema_version": 7, "overlay_mode": "current", "change_speed": "fast"})
        self.assertEqual(settings.load_legacy_overlay_mode(), "current")
        settings.clear_legacy_overlay_mode()
        self.assertIsNone(settings.load_legacy_overlay_mode())
        self.assertEqual(settings.load_change_speed(), "fast")
        self.write_json({"schema_version": 4, "overlay_mode": "everything"})
        self.assertIsNone(settings.load_legacy_overlay_mode())

    def test_invalid_input_routes_fail_closed(self) -> None:
        self.write_json({"schema_version": 4, "input_routes": {"ok": "provider", "bad": "", 1: "provider"}})
        self.assertEqual(settings.load_input_routes()[0].input_id, "ok")
        with self.assertRaises(ValueError):
            settings.save_input_routes([settings.VolumeRoute("route-test", "Test", settings.RouteEndpoint("", {}), settings.RouteEndpoint("provider", {}))])

    def test_schema_v6_routes_receive_deterministic_names_and_preserve_settings(self) -> None:
        self.write_json({"schema_version": 6, "change_speed": "fast", "unrelated": {"keep": True}, "volume_routes": [{"route_id": "route-test", "input": {"plugin_id": "windows-volume-keys", "parameters": {}}, "output": {"plugin_id": "onkyo-volume", "parameters": {}}}]})

        routes = settings.load_input_routes()
        self.assertEqual(routes[0].name, "windows-volume-keys to onkyo-volume")
        settings.save_input_routes(routes)

        payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], settings.SCHEMA_VERSION)
        self.assertEqual(payload["volume_routes"][0]["name"], "windows-volume-keys to onkyo-volume")
        self.assertEqual(payload["change_speed"], "fast")
        self.assertEqual(payload["unrelated"], {"keep": True})

    def test_route_names_are_normalized_and_bounded(self) -> None:
        self.assertEqual(settings.normalize_route_name("  Living\n room  "), "Living room")
        self.assertIsNone(settings.normalize_route_name("\t"))
        self.assertIsNone(settings.normalize_route_name("x" * (settings.MAX_ROUTE_NAME_LENGTH + 1)))
        self.assertEqual(settings.copied_route_name("x" * settings.MAX_ROUTE_NAME_LENGTH)[-5:], " copy")
        with self.assertRaises(ValueError):
            settings.VolumeRoute("route-test", " ", settings.RouteEndpoint("input", {}), settings.RouteEndpoint("output", {}))

    def test_change_speed_preserves_legacy_selection(self) -> None:
        self.write_json({"selected_monitor": {"description": "Monitor", "ordinal": 2}})

        settings.save_change_speed("fast")

        self.assertEqual(settings.load_change_speed(), "fast")
        self.assertEqual(
            settings.load_selected_monitor_key(),
            SavedMonitorSelection("Monitor", legacy_ordinal=2),
        )

    def test_change_speed_defaults_safely_and_rejects_invalid_saves(self) -> None:
        self.assertEqual(settings.load_change_speed(), "slow")

        for value in (None, True, 2, "", "turbo"):
            with self.subTest(value=value):
                self.write_json({"change_speed": value})
                self.assertEqual(settings.load_change_speed(), "slow")

        with self.assertRaises(ValueError):
            settings.save_change_speed("turbo")

    def test_legacy_settings_load_but_boolean_ordinal_does_not(self) -> None:
        self.write_json({"selected_monitor": {"description": "Monitor", "ordinal": 2}})
        self.assertEqual(
            settings.load_selected_monitor_key(),
            SavedMonitorSelection("Monitor", legacy_ordinal=2),
        )

        self.write_json({"selected_monitor": {"description": "Monitor", "ordinal": True}})
        self.assertIsNone(settings.load_selected_monitor_key())

    def test_non_object_unknown_and_malformed_values_are_absent(self) -> None:
        self.assertIsNone(settings.load_selected_monitor_key())
        self.settings_path.write_text("{not-json", encoding="utf-8")
        self.assertIsNone(settings.load_selected_monitor_key())

        for value in ([], "text", 1, True, None):
            with self.subTest(value=value):
                self.write_json(value)
                self.assertIsNone(settings.load_selected_monitor_key())

        self.write_json(
            {
                "schema_version": 99,
                "selected_monitor": {"description": "Monitor", "ordinal": 1},
            }
        )
        self.assertIsNone(settings.load_selected_monitor_key())

        self.write_json(
            {
                "schema_version": 2,
                "selected_monitor": {"description": "Monitor", "identity": []},
            }
        )
        self.assertIsNone(settings.load_selected_monitor_key())

    def test_stable_identity_is_required_for_saving(self) -> None:
        with self.assertRaises(ValueError):
            settings.save_selected_monitor_key(
                SavedMonitorSelection("Monitor", legacy_ordinal=1)
            )


if __name__ == "__main__":
    unittest.main()
