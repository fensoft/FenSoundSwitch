from __future__ import annotations

import ctypes
import json
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch

from plugin_api import MOD_ALT, MOD_CONTROL, HotkeySpec, OverlayRenderer, PluginHostContext, ShortcutAction, SlotAction, VolumeStatus
from plugin_hotkeys import HotkeyConflictError, HotkeyRegistrationError, PluginHotkeyController
from windows_platform import HC_ACTION, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP
from plugin_manager import (
    PluginManager,
    PluginRecord,
    adjacent_external_plugins_directory,
    discover_plugins,
    migrate_legacy_plugin_settings,
)
import plugin_manager
import settings


PLUGIN_SOURCE = """
from plugin_api import PLUGIN_API_VERSION
class Plugin:
    plugin_id = {plugin_id!r}
    name = {name!r}
    description = "external test plugin"
    def initialize(self, host): self.host = host
    def get_shortcut_actions(self): return []
    def trigger_shortcut(self, action_id): raise ValueError(action_id)
    def shutdown(self, timeout): return True
def create_plugin(): return Plugin()
"""


class PluginDiscoveryTests(unittest.TestCase):
    def test_bundled_external_and_user_plugins_load_in_deterministic_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adjacent = root / "adjacent"
            user = root / "user"
            adjacent.mkdir()
            user.mkdir()
            (adjacent / "zeta.py").write_text(
                PLUGIN_SOURCE.format(plugin_id="zeta", name="Zeta"), encoding="utf-8"
            )
            (adjacent / "alpha.py").write_text(
                PLUGIN_SOURCE.format(plugin_id="alpha", name="Alpha"), encoding="utf-8"
            )
            (user / "beta.py").write_text(
                PLUGIN_SOURCE.format(plugin_id="beta", name="Beta"), encoding="utf-8"
            )

            records = discover_plugins([adjacent, user])

        self.assertEqual(
            [record.plugin_id for record in records],
            [
                "windows11-overlay",
                "macos-overlay",
                "discord-output",
                "audio-keepalive",
                "windows-default-device",
                "ddc-input-source",
                "ddc-volume",
                "onkyo-volume",
                "denon-marantz-volume",
                "yamaha-volume",
                "pioneer-elite-volume",
                "sony-volume",
                "windows-volume-input",
                "keyboard-input",
                "mqtt-input",
                "windows-soundcard-volume",
                "windows-microphone-gain",
                "alpha",
                "zeta",
                "beta",
            ],
        )
        self.assertEqual(records[0].source, "Bundled")
        self.assertTrue(records[0].is_overlay_renderer)
        self.assertFalse(records[0].is_volume_provider)
        self.assertIsNone(records[0].input_id)

    def test_unconfigured_shortcut_label_is_blank(self) -> None:
        self.assertEqual(PluginRecord(key="plugin", source="Bundled").shortcut_label, "")

    def test_adjacent_external_plugins_are_separate_from_the_bundled_package(self) -> None:
        with patch("plugin_manager._runtime_base_directory", return_value=Path("C:/runtime")):
            directory = adjacent_external_plugins_directory()

        self.assertEqual(directory, Path("C:/runtime/external-plugins"))
        self.assertNotEqual(directory.name, "plugins")

    def test_legacy_plugin_settings_are_copied_without_changing_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = root / "windows-ddc" / "plugin-settings"
            current = root / "fensoundswitch" / "plugin-settings"
            legacy.mkdir(parents=True)
            source = legacy / "example.json"
            source.write_text('{"schema_version": 1, "host": "receiver"}', encoding="utf-8")

            migrate_legacy_plugin_settings(current, legacy)

            self.assertEqual(
                (current / "example.json").read_text(encoding="utf-8"),
                source.read_text(encoding="utf-8"),
            )

    def test_action_plugin_state_rejects_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "action-plugin-state.json"
            path.write_text('{"schema_version": 1, "disabled_plugin_ids": ["valid", "valid"]}', encoding="utf-8")
            self.assertEqual(plugin_manager._load_disabled_action_plugin_ids(path), set())

    def test_bad_version_import_failure_and_duplicate_id_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "bad_version.py").write_text(
                "PLUGIN_API_VERSION = 99\ndef create_plugin(): return object()\n",
                encoding="utf-8",
            )
            (directory / "broken.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
            (directory / "duplicate.py").write_text(
                PLUGIN_SOURCE.format(plugin_id="discord-output", name="Duplicate"),
                encoding="utf-8",
            )

            records = discover_plugins([directory])

        failures = [record for record in records if record.is_failure]
        self.assertEqual(len(failures), 3)
        self.assertTrue(any("unsupported plugin api version" in record.status.lower() for record in failures))
        self.assertTrue(any("broken import" in record.status.lower() for record in failures))
        self.assertTrue(any("duplicate plugin id" in record.status.lower() for record in failures))


class _FakePlugin:
    plugin_id = "fake"
    name = "Fake"
    description = "Fake plugin"

    def __init__(self, hotkey: HotkeySpec | None = None, fail_initialize: bool = False) -> None:
        self.hotkey = hotkey
        self.fail_initialize = fail_initialize
        self.host = None
        self.trigger_entered = threading.Event()
        self.release_trigger = threading.Event()
        self.trigger_count = 0
        self.shutdown_count = 0

    def initialize(self, host: object) -> None:
        if self.fail_initialize:
            raise RuntimeError("initialization failed")
        self.host = host

    def configure(self, parent: object) -> None:
        pass

    def get_shortcut_actions(self) -> list[ShortcutAction]:
        return [ShortcutAction("shortcut", "Shortcut")]

    def trigger_shortcut(self, action_id: str) -> None:
        if action_id != "shortcut":
            raise ValueError(action_id)
        self.trigger_count += 1
        self.trigger_entered.set()
        self.release_trigger.wait(1.0)

    def shutdown(self, timeout: float) -> bool:
        self.shutdown_count += 1
        self.release_trigger.set()
        return True


class _FakeVolumePlugin(_FakePlugin):
    plugin_id = "volume-fake"
    provider_name = "Fake volume"

    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.topology_notifications = 0

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        return True, None

    def read_volume(self) -> int:
        return 42

    def write_volume(self, target_volume: int) -> int:
        return target_volume

    def activate_volume_provider(self) -> None:
        self.active = True

    def deactivate_volume_provider(self) -> None:
        self.active = False

    def on_volume_topology_changed(self) -> None:
        self.topology_notifications += 1

    def create_output(self, parameters: object) -> "_FakeVolumePlugin":
        return self


class _FakeHotkeys:
    def __init__(self, on_hotkey: object, on_error: object) -> None:
        self.on_hotkey = on_hotkey
        self.on_error = on_error
        self.started = False
        self.bindings: list[tuple[str, HotkeySpec | None]] = []
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def set_binding(self, plugin_id: str, hotkey: HotkeySpec | None) -> None:
        self.bindings.append((plugin_id, hotkey))

    def stop(self, timeout: float) -> bool:
        self.stopped = True
        return True


def _record(plugin: _FakePlugin) -> PluginRecord:
    return PluginRecord(
        key="plugin-fake",
        source="test",
        plugin_id=plugin.plugin_id,
        name=plugin.name,
        description=plugin.description,
        plugin=plugin,
        is_volume_provider=isinstance(plugin, _FakeVolumePlugin),
        input_id=getattr(plugin, "input_id", None),
        input_name=getattr(plugin, "input_name", None),
    )


class PluginManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        root = Path(self._temporary_directory.name)
        self._settings_patch = patch.object(settings, "SETTINGS_PATH", root / "settings.json")
        self._plugin_settings_patch = patch(
            "plugin_manager.plugin_settings_directory",
            return_value=root / "plugin-settings",
        )
        self._shortcut_settings_patch = patch(
            "plugin_manager.shortcut_settings_path",
            return_value=root / "plugin-settings" / "shortcuts.json",
        )
        self._settings_patch.start()
        self._plugin_settings_patch.start()
        self._shortcut_settings_patch.start()
        self.addCleanup(self._settings_patch.stop)
        self.addCleanup(self._plugin_settings_patch.stop)
        self.addCleanup(self._shortcut_settings_patch.stop)

    def make_manager(self, plugin: _FakePlugin) -> tuple[PluginManager, list[str]]:
        notices: list[str] = []
        manager = PluginManager(
            Mock(),
            post_to_ui=lambda callback: callback(),
            on_notice=notices.append,
            hotkey_factory=_FakeHotkeys,
        )
        discover_patch = patch("plugin_manager.discover_plugins", return_value=[_record(plugin)])
        discover_patch.start()
        self.addCleanup(discover_patch.stop)
        return manager, notices

    def test_prepared_window_centers_over_its_parent(self) -> None:
        parent = Mock()
        parent.winfo_rootx.return_value = 100
        parent.winfo_rooty.return_value = 200
        parent.winfo_width.return_value = 800
        parent.winfo_height.return_value = 600
        window = Mock()
        window.master = parent
        window.winfo_reqwidth.return_value = 300
        window.winfo_reqheight.return_value = 200

        PluginManager._center_window_over_parent(window)

        window.geometry.assert_called_once_with("+350+400")

    def test_initialization_and_no_default_shortcut_are_nonfatal(self) -> None:
        plugin = _FakePlugin()
        manager, _ = self.make_manager(plugin)

        manager.start()

        self.assertIsNotNone(plugin.host)
        self.assertEqual(manager.records[0].status, "Ready")
        self.assertEqual(manager._hotkeys.bindings, [("fake/shortcut", None)])
        self.assertIsNone(manager.records[0].active_hotkeys["fake/shortcut"])

    def test_plugin_context_exposes_immutable_host_volume_statuses(self) -> None:
        plugin = _FakePlugin()
        statuses = (VolumeStatus("volume-fake", "Fake volume", 42, active=True),)
        manager = PluginManager(
            Mock(),
            post_to_ui=lambda callback: callback(),
            on_notice=lambda _message: None,
            hotkey_factory=_FakeHotkeys,
            get_volume_statuses=lambda: statuses,
        )
        with patch("plugin_manager.discover_plugins", return_value=[_record(plugin)]):
            manager.start()

        self.assertEqual(plugin.host.get_volume_statuses(), statuses)
        with self.assertRaises(FrozenInstanceError):
            plugin.host.get_volume_statuses()[0].confirmed_volume = 50  # type: ignore[misc]

    def test_bundled_overlay_plugin_hands_its_renderer_to_the_manager(self) -> None:
        from plugins.windows11_overlay_plugin import OverlayPlugin

        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        parent = Mock(name="tk-parent")
        renderer = Mock(spec=OverlayRenderer)
        plugin = OverlayPlugin()
        record = PluginRecord(
            key="plugin-volume-overlay",
            source="Bundled",
            plugin_id=plugin.plugin_id,
            name=plugin.name,
            description=plugin.description,
            plugin=plugin,
            is_overlay_renderer=True,
        )
        manager = PluginManager(
            parent,
            post_to_ui=lambda callback: callback(),
            on_notice=lambda _message: None,
            hotkey_factory=_FakeHotkeys,
            plugin_settings_path=Path(temporary_directory.name),
        )

        with patch("plugin_manager.discover_plugins", return_value=[record]), patch(
            "plugins.windows11_overlay_plugin.VolumeOverlay", return_value=renderer
        ) as create_overlay:
            manager.start()
            result = manager.create_overlay_renderer(True, False)

        self.assertIs(result, renderer)
        self.assertTrue(record.initialized)
        self.assertIs(plugin._host.ui_parent, parent)
        create_overlay.assert_called_once_with(parent, dark_mode=True, high_contrast=False, mode="all")

    def test_windows_overlay_migrates_legacy_mode_to_plugin_owned_settings(self) -> None:
        from plugins.windows11_overlay_plugin import OverlayPlugin

        saved: list[dict[str, object]] = []
        cleared: list[bool] = []
        plugin = OverlayPlugin()
        plugin.initialize(PluginHostContext(
            plugin_id=plugin.plugin_id, ui_parent=Mock(), logger=Mock(), post_to_ui=lambda callback: callback(),
            report_status=lambda _status: None, prepare_window=lambda _window: None,
            load_plugin_settings=lambda: {}, save_plugin_settings=saved.append,
            load_legacy_overlay_mode=lambda: "current", clear_legacy_overlay_mode=lambda: cleared.append(True),
        ))

        self.assertEqual(plugin._mode, "current")
        self.assertEqual(saved, [{"schema_version": 1, "mode": "current"}])
        self.assertEqual(cleared, [True])

    def test_overlay_selection_persists_and_handoffs_between_renderers(self) -> None:
        class FakeOverlay(_FakePlugin):
            def __init__(self, plugin_id: str, name: str) -> None:
                super().__init__()
                self.plugin_id = plugin_id
                self.name = name
                self.renderer = Mock(spec=OverlayRenderer)

            def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
                return self.renderer

        windows = FakeOverlay("windows11-overlay", "Windows 11 overlay")
        macos = FakeOverlay("macos-overlay", "macOS-style overlay")
        records = [_record(windows), _record(macos)]
        for record in records:
            record.is_overlay_renderer = True
        handoffs: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(
                Mock(), post_to_ui=lambda callback: callback(), on_notice=lambda _message: None,
                hotkey_factory=_FakeHotkeys, plugin_settings_path=Path(directory),
                on_overlay_renderer_changed=lambda: handoffs.append("changed"),
            )
            with patch("plugin_manager.discover_plugins", return_value=records):
                manager.start()
            self.assertIs(manager.create_overlay_renderer(True, False), windows.renderer)
            self.assertTrue(manager.set_active_overlay_plugin_id("macos-overlay"))
            self.assertEqual(handoffs, ["changed"])
            self.assertIs(manager.create_overlay_renderer(True, False), macos.renderer)
            payload = json.loads((Path(directory) / "active-overlay.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["plugin_id"], "macos-overlay")

    def test_bundled_overlay_creation_failure_is_reported_without_disabling_routes(self) -> None:
        from plugins.windows11_overlay_plugin import OverlayPlugin

        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        notices: list[str] = []
        plugin = OverlayPlugin()
        record = PluginRecord(
            key="plugin-volume-overlay",
            source="Bundled",
            plugin_id=plugin.plugin_id,
            name=plugin.name,
            description=plugin.description,
            plugin=plugin,
            is_overlay_renderer=True,
        )
        manager = PluginManager(
            Mock(),
            post_to_ui=lambda callback: callback(),
            on_notice=notices.append,
            hotkey_factory=_FakeHotkeys,
            plugin_settings_path=Path(temporary_directory.name),
        )

        with patch("plugin_manager.discover_plugins", return_value=[record]), patch(
            "plugins.windows11_overlay_plugin.VolumeOverlay", side_effect=RuntimeError("native window failed")
        ):
            manager.start()
            self.assertIsNone(manager.create_overlay_renderer(True, False))

        self.assertEqual(record.status, "Overlay creation failed: native window failed")
        self.assertIn("Volume overlay unavailable: native window failed. Routes remain available.", notices)
        self.assertTrue(record.initialized)

    def test_input_route_requires_a_ready_provider_and_resolves_it(self) -> None:
        input_plugin = _FakePlugin()
        input_plugin.plugin_id = "input-fake"
        input_plugin.input_id = "input-fake-source"
        input_plugin.input_name = "Fake input"
        input_plugin.create_input = lambda parameters: dict(parameters)  # type: ignore[attr-defined]
        provider = _FakeVolumePlugin()
        records = [_record(input_plugin), _record(provider)]
        records[1].key = "plugin-volume-fake"
        manager, _ = self.make_manager(input_plugin)
        with patch("plugin_manager.discover_plugins", return_value=records), patch("plugin_manager.load_input_routes", return_value=()):
            manager.start()
        self.assertEqual([(record.plugin_id, record.initialized) for record in manager.records], [("input-fake", True), ("volume-fake", True)])
        self.assertTrue(manager.records[1].is_volume_provider)
        self.assertEqual(manager.records[0].input_id, "input-fake-source")
        with patch.object(plugin_manager, "save_input_routes"):
            self.assertTrue(manager.add_route("input-fake-source", "volume-fake"))
        self.assertEqual(manager.input_routes[0].output.plugin_id, "volume-fake")
        self.assertTrue(manager.add_route("input-fake-source", "volume-fake"))
        self.assertFalse(manager.add_route("input-fake-source", "missing"))
        self.assertEqual(manager.volume_providers_for_input("missing"), ())

    def test_keyboard_route_conflicts_are_rejected_not_broadcast(self) -> None:
        from plugins.keyboard_input_plugin import KeyboardInputPlugin
        keyboard = KeyboardInputPlugin()
        provider = _FakeVolumePlugin()
        records = [_record(keyboard), _record(provider)]
        records[1].key = "plugin-volume-fake"
        manager, notices = self.make_manager(keyboard)
        with patch("plugin_manager.discover_plugins", return_value=records), patch("plugin_manager.load_input_routes", return_value=()):
            manager.start()
        keys = {"volume_down": HotkeySpec(0, ord("J")).to_json(), "volume_up": HotkeySpec(0, ord("K")).to_json()}
        self.assertTrue(manager.add_route("keyboard-keys", "volume-fake", input_parameters=keys))
        dispatched: list[tuple[str, int]] = []
        manager._on_route_input = lambda route_id, delta: dispatched.append((route_id, delta))
        manager._dispatch_trigger(f"route/{manager.input_routes[0].route_id}/down")
        self.assertEqual(dispatched, [(manager.input_routes[0].route_id, -1)])
        self.assertFalse(manager.add_route("keyboard-keys", "volume-fake", input_parameters=keys))
        self.assertTrue(any("not broadcast" in message for message in notices))

    def test_initialization_failure_isolated_and_visible(self) -> None:
        plugin = _FakePlugin(fail_initialize=True)
        manager, notices = self.make_manager(plugin)

        manager.start()

        self.assertIn("Initialization failed", manager.records[0].status)
        self.assertTrue(any("unavailable" in notice.lower() for notice in notices))

    def test_overlapping_plugin_triggers_are_suppressed(self) -> None:
        plugin = _FakePlugin(HotkeySpec(MOD_CONTROL, ord("K")))
        manager, _ = self.make_manager(plugin)
        manager.start()

        manager._dispatch_trigger("fake/shortcut")
        self.assertTrue(plugin.trigger_entered.wait(1.0))
        manager._dispatch_trigger("fake/shortcut")
        time.sleep(0.02)
        self.assertEqual(plugin.trigger_count, 1)
        plugin.release_trigger.set()
        self.assertTrue(manager.stop(1.0))

    def test_shutdown_unregisters_hotkeys_before_plugins(self) -> None:
        order: list[str] = []

        class OrderedHotkeys(_FakeHotkeys):
            def stop(self, timeout: float) -> bool:
                order.append("hotkeys")
                return True

        plugin = _FakePlugin()
        plugin.shutdown = lambda timeout: order.append("plugin") or True  # type: ignore[method-assign]
        manager = PluginManager(
            Mock(),
            post_to_ui=lambda callback: callback(),
            on_notice=lambda message: None,
            hotkey_factory=OrderedHotkeys,
        )
        with patch("plugin_manager.discover_plugins", return_value=[_record(plugin)]):
            manager.start()
        self.assertTrue(manager.stop(1.0))
        self.assertEqual(order, ["hotkeys", "plugin"])

    def test_live_rebinding_reads_the_plugin_owned_configuration(self) -> None:
        old = HotkeySpec(MOD_CONTROL, ord("K"))
        new = HotkeySpec(MOD_ALT, ord("L"))
        plugin = _FakePlugin(old)
        manager, _ = self.make_manager(plugin)
        manager.start()
        plugin.hotkey = new

        manager.refresh_hotkey("fake")

        self.assertEqual(manager._hotkeys.bindings, [("fake/shortcut", None), ("fake/shortcut", None)])

    def test_hotkey_thread_failure_does_not_prevent_plugin_initialization(self) -> None:
        class FailedHotkeys(_FakeHotkeys):
            def start(self) -> None:
                raise RuntimeError("native hotkeys failed")

        plugin = _FakePlugin()
        notices: list[str] = []
        manager = PluginManager(
            Mock(),
            post_to_ui=lambda callback: callback(),
            on_notice=notices.append,
            hotkey_factory=FailedHotkeys,
        )
        with patch("plugin_manager.discover_plugins", return_value=[_record(plugin)]):
            manager.start()
        self.assertIsNotNone(plugin.host)
        self.assertIn("unavailable", manager.records[0].shortcut_error)
        self.assertTrue(any("shortcuts are unavailable" in notice.lower() for notice in notices))

    def test_uncooperative_plugin_shutdown_is_bounded(self) -> None:
        release = threading.Event()
        plugin = _FakePlugin()
        plugin.shutdown = lambda timeout: release.wait(1.0)  # type: ignore[method-assign]
        manager, _ = self.make_manager(plugin)
        manager.start()
        started = time.monotonic()
        try:
            self.assertFalse(manager.stop(0.02))
            self.assertLess(time.monotonic() - started, 0.25)
        finally:
            release.set()

    def test_runtime_hotkey_thread_failure_clears_active_shortcut_status(self) -> None:
        plugin = _FakePlugin(HotkeySpec(MOD_CONTROL, ord("K")))
        manager, notices = self.make_manager(plugin)
        manager.start()

        manager._hotkey_error_from_thread(RuntimeError("message loop failed"))

        self.assertIsNone(manager.records[0].active_hotkeys["fake/shortcut"])
        self.assertEqual(manager.records[0].shortcut_error, "global shortcut service stopped")
        self.assertTrue(any("shortcuts failed" in notice.lower() for notice in notices))

    def test_named_action_binding_persists_and_dispatches_the_action(self) -> None:
        class ActionPlugin(_FakePlugin):
            def get_shortcut_actions(self) -> list[ShortcutAction]:
                return [ShortcutAction("do-thing", "Do thing")]

            def trigger_shortcut(self, action_id: str) -> None:
                self.action_id = action_id
                self.trigger_entered.set()

        plugin = ActionPlugin()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shortcuts.json"
            manager, _ = self.make_manager(plugin)
            manager._shortcut_path = path
            manager.start()
            hotkey = HotkeySpec(MOD_CONTROL, ord("D"))
            manager._set_named_shortcut(manager.records[0], "do-thing", hotkey)
            self.assertEqual(manager._hotkeys.bindings[-1], ("fake/do-thing", hotkey))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["bindings"]["fake/do-thing"],
                {"hotkey": hotkey.to_json(), "forward_keys": True},
            )
            manager._dispatch_trigger("fake/do-thing")
            self.assertTrue(plugin.trigger_entered.wait(1.0))
            self.assertEqual(plugin.action_id, "do-thing")
            manager.stop(1.0)

    def test_action_signal_runs_repeated_parameterized_slots_in_order(self) -> None:
        class SignalPlugin(_FakePlugin):
            def __init__(self) -> None:
                super().__init__()
                self.calls: list[tuple[str, dict[str, object]]] = []
                self.finished = threading.Event()

            def get_slot_actions(self) -> list[SlotAction]:
                return [SlotAction("switch", "Switch device")]

            def run_slot(self, action_id: str, parameters: object) -> None:
                self.calls.append((action_id, dict(parameters)))
                if len(self.calls) == 2:
                    self.finished.set()

        plugin = SignalPlugin()
        manager, _ = self.make_manager(plugin)
        manager.start()
        self.assertTrue(manager.save_action_signal(
            None,
            "Movie mode",
            HotkeySpec(0, 0x70).to_json(),
            False,
            "Movie mode",
            [
                {"kind": "action", "target": "fake/switch", "parameters": {"device": "speakers"}},
                {"kind": "wait", "milliseconds": 10},
                {"kind": "action", "target": "fake/switch", "parameters": {"device": "headset"}},
            ],
        ))
        signal = manager.action_signals[0]

        manager._dispatch_trigger(f"signal/{signal.signal_id}")

        self.assertTrue(plugin.finished.wait(1.0))
        self.assertEqual(plugin.calls, [("switch", {"device": "speakers"}), ("switch", {"device": "headset"})])
        self.assertIn((f"signal/{signal.signal_id}", HotkeySpec(0, 0x70)), manager._hotkeys.bindings)
        manager.stop(1.0)

    def test_slot_summary_failure_is_isolated_from_the_host(self) -> None:
        class BrokenSummaryPlugin(_FakePlugin):
            def get_slot_actions(self) -> list[SlotAction]:
                return [SlotAction("switch", "Switch")]

            def run_slot(self, action_id: str, parameters: object) -> None:
                return None

            def get_slot_ui(self, action_id: str, parameters: object) -> dict[str, object]:
                return {"schema_version": 1, "title": "Step", "fields": [], "actions": []}

            def invoke_slot_ui_action(self, action_id: str, ui_action_id: str, values: object) -> dict[str, object]:
                return {"status": "save", "values": {}}

            def slot_summary(self, action_id: str, parameters: object) -> str:
                raise RuntimeError("broken summary")

        manager, _ = self.make_manager(BrokenSummaryPlugin())
        manager.start()

        self.assertEqual(manager.slot_summary("fake", "switch", {}), "Configuration unavailable")
        self.assertEqual(manager.slot_summary("missing", "switch", {}), "Unavailable")
        manager.stop(1.0)

    def test_action_signal_overlap_is_suppressed_and_wait_is_shutdown_interruptible(self) -> None:
        plugin = _FakePlugin()
        manager, _ = self.make_manager(plugin)
        manager.start()
        signal = settings.ActionSignal(
            "signal-wait",
            "Wait then act",
            settings.ActionHotkeyBinding(None),
            "Wait then act",
            (settings.WaitSlot(300_000), settings.ActionSlot("fake", "shortcut", {})),
        )
        manager._action_signals = (signal,)

        manager.dispatch_action_signal(signal.signal_id)
        manager.dispatch_action_signal(signal.signal_id)
        started = time.monotonic()
        self.assertTrue(manager.stop(0.5))

        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(plugin.trigger_count, 0)

    def test_removed_builtin_direct_shortcut_does_not_create_an_automation(self) -> None:
        class WindowsSlotPlugin(_FakePlugin):
            plugin_id = "windows-default-device"
            name = "Windows default device switch"
            get_shortcut_actions = None
            trigger_shortcut = None

            def get_slot_actions(self) -> list[SlotAction]:
                return [SlotAction("cycle-playback", "Cycle Windows playback")]

            def run_slot(self, action_id: str, parameters: object) -> None:
                return None

        plugin = WindowsSlotPlugin()
        with tempfile.TemporaryDirectory() as directory:
            shortcut_path = Path(directory) / "shortcuts.json"
            hotkey = HotkeySpec(MOD_CONTROL, ord("D"))
            plugin_manager._save_shortcut_bindings(shortcut_path, {
                "windows-default-device/cycle-playback": plugin_manager.ActionHotkeyBinding(hotkey, False),
            })
            manager, _ = self.make_manager(plugin)
            manager._shortcut_path = shortcut_path
            manager.start()

            self.assertEqual(manager.action_signals, ())
            manager.stop(1.0)

    def test_startup_automation_dispatches_once(self) -> None:
        class StartupPlugin(_FakePlugin):
            def __init__(self) -> None:
                super().__init__()
                self.runs = 0
                self.finished = threading.Event()

            def get_slot_actions(self) -> list[SlotAction]:
                return [SlotAction("initialize", "Initialize")]

            def run_slot(self, action_id: str, parameters: object) -> None:
                self.runs += 1
                self.finished.set()

        signal = settings.ActionSignal(
            "signal-startup",
            "Startup",
            settings.ActionHotkeyBinding(None),
            None,
            (settings.ActionSlot("fake", "initialize", {}),),
            True,
        )
        plugin = StartupPlugin()
        manager, _ = self.make_manager(plugin)
        with patch("plugin_manager.load_action_signals", return_value=(signal,)):
            manager.start()

        manager.dispatch_startup_automations()
        manager.dispatch_startup_automations()

        self.assertTrue(plugin.finished.wait(1.0))
        self.assertEqual(plugin.runs, 1)
        manager.stop(1.0)

    def test_plugin_signal_trigger_starts_dispatches_and_stops_with_manager(self) -> None:
        class TriggerInstance:
            def __init__(self, dispatch: object, signal_id: str) -> None:
                self.dispatch = dispatch
                self.signal_id = signal_id
                self.started = False
                self.stopped = False

            def start(self) -> None:
                self.started = True

            def shutdown(self, timeout: float) -> bool:
                self.stopped = True
                return True

        class TriggerPlugin(_FakePlugin):
            def __init__(self) -> None:
                super().__init__()
                self.instance = None

            def create_signal_trigger(self, signal_id: str, parameters: object, dispatch: object) -> object:
                self.asserted_parameters = parameters
                self.instance = TriggerInstance(dispatch, signal_id)
                return self.instance

        signal = settings.ActionSignal(
            "signal-mqtt",
            "MQTT automation",
            settings.ActionHotkeyBinding(None),
            None,
            (settings.WaitSlot(1),),
            False,
            (settings.PluginSignalTrigger("fake", "mqtt-ha", {"profile_id": "p-home"}),),
        )
        plugin = TriggerPlugin()
        manager, _ = self.make_manager(plugin)
        with patch("plugin_manager.load_action_signals", return_value=(signal,)):
            manager.start()

        self.assertTrue(plugin.instance.started)
        self.assertEqual(plugin.asserted_parameters, {"profile_id": "p-home"})
        plugin.instance.dispatch(plugin.instance.signal_id)
        self.assertTrue(manager.stop(1.0))
        self.assertTrue(plugin.instance.stopped)

    def test_duplicate_mqtt_ha_ids_fail_closed_per_shared_profile(self) -> None:
        plugin = _FakePlugin()
        manager, notices = self.make_manager(plugin)
        trigger = settings.PluginSignalTrigger("mqtt-input", "mqtt-ha", {"profile_id": "p-home", "ha_name": "One", "ha_id": "shared"})
        signals = (
            settings.ActionSignal("signal-one", "One", settings.ActionHotkeyBinding(None), None, (settings.WaitSlot(1),), False, (trigger,)),
            settings.ActionSignal("signal-two", "Two", settings.ActionHotkeyBinding(None), None, (settings.WaitSlot(1),), False, (trigger,)),
        )

        self.assertFalse(manager._save_action_signals(signals))
        self.assertIn("unique Home Assistant IDs", notices[-1])

        routes = (
            settings.VolumeRoute("route-one", "One", settings.RouteEndpoint("mqtt", {"profile_id": "p-home", "ha_name": "One", "ha_id": "shared", "max_value": 100}), settings.RouteEndpoint("fake", {})),
            settings.VolumeRoute("route-two", "Two", settings.RouteEndpoint("mqtt", {"profile_id": "p-home", "ha_name": "Two", "ha_id": "shared", "max_value": 100}), settings.RouteEndpoint("fake", {})),
        )
        self.assertFalse(manager._save_routes(routes))
        self.assertIn("unique Home Assistant IDs", notices[-1])

    def test_legacy_action_binding_migrates_to_forwarding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shortcuts.json"
            hotkey = HotkeySpec(MOD_CONTROL, ord("D"))
            path.write_text(
                json.dumps({"schema_version": 1, "bindings": {"fake/do-thing": hotkey.to_json()}}),
                encoding="utf-8",
            )
            binding = plugin_manager._load_shortcut_bindings(path)["fake/do-thing"]
        self.assertEqual(binding.hotkey, hotkey)
        self.assertTrue(binding.forward_keys)

    def test_disabled_action_plugin_skips_startup_then_can_be_toggled(self) -> None:
        plugin = _FakePlugin()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "action-plugin-state.json"
            plugin_manager._save_disabled_action_plugin_ids(state_path, {plugin.plugin_id})
            manager = PluginManager(
                Mock(),
                post_to_ui=lambda callback: callback(),
                on_notice=lambda _message: None,
                hotkey_factory=_FakeHotkeys,
                action_plugin_state_path_override=state_path,
            )
            with patch("plugin_manager.discover_plugins", return_value=[_record(plugin)]):
                manager.start()

            record = manager.records[0]
            self.assertFalse(record.enabled)
            self.assertFalse(record.initialized)
            self.assertEqual(record.status, "Disabled")
            self.assertTrue(manager.set_action_plugin_enabled(plugin.plugin_id, True))
            self.assertTrue(record.initialized)
            self.assertEqual(plugin.host.plugin_id, plugin.plugin_id)
            self.assertTrue(manager.set_action_plugin_enabled(plugin.plugin_id, False))
            self.assertFalse(record.initialized)
            self.assertEqual(record.status, "Disabled")
            self.assertEqual(plugin.shutdown_count, 1)
            self.assertEqual(plugin_manager._load_disabled_action_plugin_ids(state_path), {plugin.plugin_id})

    def test_routed_volume_provider_receives_topology_notification(self) -> None:
        plugin = _FakeVolumePlugin()
        manager, _ = self.make_manager(plugin)
        manager.start()
        manager._input_routes = (plugin_manager.VolumeRoute("route-test", "Test route", plugin_manager.RouteEndpoint("windows-volume-keys", {}), plugin_manager.RouteEndpoint(plugin.plugin_id, {})),)
        manager._route_instances = {"route-test": plugin}
        manager.notify_volume_topology_changed()
        self.assertEqual(plugin.topology_notifications, 1)

    def test_bundled_route_fixtures_start_with_isolated_settings(self) -> None:
        """Every bundled input/output shape must construct without live I/O."""
        from plugins.ddc_volume_plugin import DdcVolumePlugin
        from plugins.denon_marantz_volume_plugin import DenonMarantzVolumePlugin
        from plugins.keyboard_input_plugin import KeyboardInputPlugin
        from plugins.macos_overlay_plugin import MacOSOverlayPlugin
        from plugins.onkyo_volume_plugin import OnkyoVolumePlugin
        from plugins.pioneer_elite_volume_plugin import PioneerEliteVolumePlugin
        from plugins.sony_volume_plugin import SonyVolumePlugin
        from plugins.windows11_overlay_plugin import OverlayPlugin
        from plugins.windows_soundcard_volume_plugin import WindowsSoundcardVolumePlugin
        from plugins.windows_microphone_gain_plugin import WindowsMicrophoneGainPlugin
        from plugins.windows_volume_input_plugin import WindowsVolumeInputPlugin
        from plugins.yamaha_volume_plugin import YamahaVolumePlugin

        def record(plugin: object, *, input_id: str | None = None, overlay: bool = False) -> PluginRecord:
            return PluginRecord(
                key=f"bundled-{getattr(plugin, 'plugin_id')}", source="test",
                plugin_id=getattr(plugin, "plugin_id"), name=getattr(plugin, "name"),
                description=getattr(plugin, "description"), plugin=plugin,
                input_id=input_id, is_overlay_renderer=overlay,
            )

        down = HotkeySpec(MOD_CONTROL, ord("J")).to_json()
        up = HotkeySpec(MOD_CONTROL, ord("K")).to_json()
        outputs = (
            ("ddc-volume", {"selected_monitor": {"description": "Desk", "identity": {"device_path": "MONITOR\\TEST", "manufacturer_id": "TST", "product_code": 1, "serial_number": "1"}}}),
            ("onkyo-volume", {"host": "onkyo.local", "port": 60128}),
            ("denon-marantz-volume", {"host": "denon.local", "port": 23}),
            ("yamaha-volume", {"host": "yamaha.local", "port": 50000}),
            ("pioneer-elite-volume", {"host": "pioneer.local", "port": 8102}),
            ("sony-volume", {"host": "sony.local", "port": 10000}),
            ("windows-soundcard-volume", {"endpoint_id": "endpoint-1", "display_name": "Desk speakers"}),
            ("windows-microphone-gain", {"endpoint_id": "microphone-1", "display_name": "USB microphone"}),
        )
        routes = [
            {"route_id": f"windows-{index}", "name": f"Windows {plugin_id}", "input": {"plugin_id": "windows-volume-keys", "parameters": {}}, "output": {"plugin_id": plugin_id, "parameters": parameters}}
            for index, (plugin_id, parameters) in enumerate(outputs)
        ]
        routes.append({"route_id": "keyboard-ddc", "name": "Keyboard DDC", "input": {"plugin_id": "keyboard-keys", "parameters": {"volume_down": down, "volume_up": up}}, "output": {"plugin_id": "ddc-volume", "parameters": outputs[0][1]}})
        settings.SETTINGS_PATH.write_text(json.dumps({"schema_version": settings.SCHEMA_VERSION, "volume_routes": routes}), encoding="utf-8")
        (settings.SETTINGS_PATH.parent / "plugin-settings").mkdir()
        (settings.SETTINGS_PATH.parent / "plugin-settings" / "active-overlay.json").write_text('{"plugin_id": {"malformed": true}}', encoding="utf-8")
        records = [
            record(OverlayPlugin(), overlay=True), record(MacOSOverlayPlugin(), overlay=True),
            record(WindowsVolumeInputPlugin(), input_id="windows-volume-keys"), record(KeyboardInputPlugin(), input_id="keyboard-keys"),
            record(DdcVolumePlugin()), record(OnkyoVolumePlugin()), record(DenonMarantzVolumePlugin()), record(YamahaVolumePlugin()),
            record(PioneerEliteVolumePlugin()), record(SonyVolumePlugin()), record(WindowsSoundcardVolumePlugin()), record(WindowsMicrophoneGainPlugin()),
        ]
        manager = PluginManager(Mock(), post_to_ui=lambda callback: callback(), on_notice=lambda _message: None, hotkey_factory=_FakeHotkeys)
        with patch("plugin_manager.discover_plugins", return_value=records):
            manager.start()

        self.assertEqual(set(manager._route_instances), {route["route_id"] for route in routes})
        self.assertEqual(manager.active_overlay_plugin_id, "windows11-overlay")

    def test_malformed_persisted_keyboard_parameters_only_disable_that_route(self) -> None:
        bad = {"volume_down": {"modifiers": {"bad": 1}, "virtual_key": ord("J")}, "volume_up": HotkeySpec(0, ord("K")).to_json()}
        settings.SETTINGS_PATH.write_text(json.dumps({"schema_version": settings.SCHEMA_VERSION, "volume_routes": [
            {"route_id": "bad-keys", "name": "Bad keys", "input": {"plugin_id": "keyboard-keys", "parameters": bad}, "output": {"plugin_id": "volume-fake", "parameters": {}}},
            {"route_id": "working", "name": "Working", "input": {"plugin_id": "windows-volume-keys", "parameters": {}}, "output": {"plugin_id": "volume-fake", "parameters": {}}},
        ]}), encoding="utf-8")

        class InputPlugin(_FakePlugin):
            plugin_id = "input-fake"
            input_id = "keyboard-keys"
            input_name = "Keyboard"

            def create_input(self, parameters: object) -> object:
                if isinstance(parameters, dict) and parameters:
                    return __import__("plugins.keyboard_input_plugin", fromlist=["validate_parameters"]).validate_parameters(parameters)
                return object()

            def route_hotkeys(self, parameters: object) -> dict[str, HotkeySpec]:
                if isinstance(parameters, dict) and parameters:
                    self.create_input(parameters)
                return {}

        windows = InputPlugin()
        windows.input_id = "windows-volume-keys"
        keyboard = InputPlugin()
        plugin = _FakeVolumePlugin()
        records = [_record(windows), _record(keyboard), _record(plugin)]
        manager = PluginManager(Mock(), post_to_ui=lambda callback: callback(), on_notice=lambda _message: None, hotkey_factory=_FakeHotkeys)
        with patch("plugin_manager.discover_plugins", return_value=records):
            manager.start()

        self.assertEqual(set(manager._route_instances), {"working"})

    def test_unhashable_route_binding_direction_does_not_abort_startup(self) -> None:
        class MalformedBindings(dict):
            def items(self) -> object:
                return (({"bad": "direction"}, HotkeySpec(0, ord("J"))),)

        class InputPlugin(_FakePlugin):
            plugin_id = "input-fake"
            input_id = "keyboard-keys"
            input_name = "Keyboard"

            def create_input(self, parameters: object) -> object:
                return object()

            def route_hotkeys(self, parameters: object) -> dict[str, HotkeySpec]:
                return MalformedBindings()

        settings.save_input_routes((plugin_manager.VolumeRoute("malformed", "Malformed", plugin_manager.RouteEndpoint("keyboard-keys", {}), plugin_manager.RouteEndpoint("volume-fake", {})),))
        manager = PluginManager(Mock(), post_to_ui=lambda callback: callback(), on_notice=lambda _message: None, hotkey_factory=_FakeHotkeys)
        with patch("plugin_manager.discover_plugins", return_value=[_record(InputPlugin()), _record(_FakeVolumePlugin())]):
            manager.start()

        self.assertEqual(manager._route_instances, {})


class FluentPanelStateTests(unittest.TestCase):
    def test_start_controls_refresh_from_one_host_owned_state(self) -> None:
        manager = PluginManager.__new__(PluginManager)
        variable = Mock()
        button = Mock()
        manager._get_start_with_windows = lambda: True
        manager._start_controls = [(variable, button)]

        manager.refresh_start_with_windows_controls()

        variable.set.assert_called_once_with(True)
        button.configure.assert_called_once_with(text="On")

    def test_overlay_controls_share_the_active_renderer_label(self) -> None:
        manager = PluginManager.__new__(PluginManager)
        first = Mock()
        second = Mock()
        first_owner = Mock()
        first_owner.winfo_exists.return_value = True
        second_owner = Mock()
        second_owner.winfo_exists.return_value = True
        manager._active_overlay_plugin_id = "windows11-overlay"
        manager._records_by_id = {"windows11-overlay": Mock(name="Windows 11")}
        manager._records_by_id["windows11-overlay"].name = "Windows 11"
        manager._overlay_controls = [(first_owner, first), (second_owner, second)]

        manager._sync_overlay_controls()

        first.set.assert_called_once_with("Windows 11")
        second.set.assert_called_once_with("Windows 11")

    def test_route_panel_refreshers_drop_destroyed_secondary_windows(self) -> None:
        manager = PluginManager.__new__(PluginManager)
        alive = Mock()
        alive.winfo_exists.return_value = True
        dead = Mock()
        dead.winfo_exists.return_value = False
        rebuild = Mock()
        refresh_statuses = Mock()
        dead_rebuild = Mock()
        dead_statuses = Mock()
        manager._route_panel_refreshers = [
            (alive, rebuild, refresh_statuses),
            (dead, dead_rebuild, dead_statuses),
        ]

        manager.refresh_routes_panel()

        refresh_statuses.assert_called_once_with()
        dead_statuses.assert_not_called()
        self.assertEqual(manager._route_panel_refreshers, [(alive, rebuild, refresh_statuses)])

        manager.refresh_routes_panel(rebuild=True)
        rebuild.assert_called_once_with()


class PluginGuiIntegrationTests(unittest.TestCase):
    def test_mqtt_automation_trigger_uses_a_shared_profile_and_ha_identity(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        manager.save_action_signal.return_value = True
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = manager

        app._dispatch_web_action("signal.save", {
            "values": {
                "name": "Movie mode",
                "triggers": [{
                    "kind": "mqtt",
                    "profile_id": "p-home",
                    "ha_name": "Movie mode",
                    "ha_id": "movie_mode",
                }],
                "slots": [{"kind": "wait", "milliseconds": 1}],
            },
        })

        self.assertEqual(manager.save_action_signal.call_args.args[-1], [{
            "target": "mqtt-input/mqtt-ha",
            "parameters": {"profile_id": "p-home", "ha_name": "Movie mode", "ha_id": "movie_mode"},
        }])

    def test_configured_automation_step_round_trips_plugin_parameters(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        manager.get_slot_ui.return_value = {
            "schema_version": 1,
            "title": "Configure step",
            "description": "Choose a target.",
            "fields": [{
                "id": "target",
                "type": "select",
                "label": "Target",
                "value": 2,
                "required": True,
                "options": [{"label": "Two", "value": 2}],
            }],
            "actions": [{"id": "save", "label": "Save", "kind": "submit", "async": False}],
        }
        manager.invoke_slot_ui_action.return_value = {
            "status": "save",
            "values": {"target": 2},
        }
        manager.slot_summary.return_value = "Target: Two"
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = manager

        form = app._dispatch_web_action("slot.ui", {
            "target": "plugin/action",
            "parameters": {"target": 1},
        })
        saved = app._dispatch_web_action("slot.save", {
            "target": "plugin/action",
            "action_id": "save",
            "values": {"target": 2},
        })

        manager.get_slot_ui.assert_called_once_with("plugin", "action", {"target": 1})
        manager.invoke_slot_ui_action.assert_called_once_with("plugin", "action", "save", {"target": 2})
        self.assertEqual(form["fields"][0]["key"], "target")
        self.assertEqual(saved["parameters"], {"target": 2})
        self.assertEqual(saved["summary"], "Target: Two")

    def test_removed_automation_triggers_are_not_submitted(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        manager.save_action_signal.return_value = True
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = manager

        app._dispatch_web_action("signal.save", {
            "values": {
                "name": "Startup only",
                "triggers": [{"kind": "app-start"}],
                "slots": [{"kind": "wait", "milliseconds": 1}],
            },
        })

        manager.save_action_signal.assert_called_once_with(
            None,
            "Startup only",
            None,
            True,
            None,
            [{"kind": "wait", "milliseconds": 1}],
            True,
            [],
        )

    def test_trigger_list_combines_keyboard_tray_and_app_start(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        manager.save_action_signal.return_value = True
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = manager
        hotkey = {"modifiers": 2, "virtual_key": 77}

        app._dispatch_web_action("signal.save", {
            "values": {
                "name": "Several triggers",
                "triggers": [
                    {"kind": "app-start"},
                    {"kind": "keyboard", "hotkey": hotkey, "forward_keys": False},
                    {"kind": "tray", "label": "Run several triggers"},
                ],
                "slots": [{"kind": "wait", "milliseconds": 1}],
            },
        })

        manager.save_action_signal.assert_called_once_with(
            None,
            "Several triggers",
            hotkey,
            False,
            "Run several triggers",
            [{"kind": "wait", "milliseconds": 1}],
            True,
            [],
        )

    def test_trigger_list_rejects_duplicate_types(self) -> None:
        from gui import MonitorVolumeApp
        from web_presentation import UserActionError

        manager = Mock()
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = manager

        with self.assertRaisesRegex(UserActionError, "only once"):
            app._dispatch_web_action("signal.save", {
                "values": {
                    "name": "Duplicate",
                    "triggers": [{"kind": "app-start"}, {"kind": "app-start"}],
                    "slots": [{"kind": "wait", "milliseconds": 1}],
                },
            })

        manager.save_action_signal.assert_not_called()

    def test_embedded_panels_are_built_after_plugin_startup(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app._resize_for_content = Mock()
        app.routes_panel = Mock()
        app.plugins_panel = Mock()
        app.integrations_panel = Mock()
        app.appearance_panel = Mock()
        app._post_to_ui = Mock()
        app._set_status = Mock()
        app.start_with_windows = False
        app.set_start_with_windows_enabled = Mock()
        app._get_volume_statuses = Mock()
        app._ui_dpi = 96
        app.overlay_mode = "current"
        app.dark_mode = True
        app.high_contrast = False
        app._set_overlay_mode = Mock()
        app._ensure_relevant_volume_statuses = Mock()

        with patch("plugin_manager.PluginManager", return_value=manager):
            app._start_plugins()

        manager.start.assert_called_once_with()
        manager.create_overlay_renderer.assert_called_once_with(app.dark_mode, app.high_contrast)
        manager.build_routes_panel.assert_called_once_with(app.routes_panel)
        manager.build_action_plugins_panel.assert_called_once_with(app.integrations_panel)
        manager.build_appearance_panel.assert_called_once_with(app.appearance_panel)
        manager.dispatch_startup_automations.assert_called_once_with()

    def test_plugin_manager_startup_failure_keeps_an_actionable_routes_error_visible(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        manager.start.side_effect = RuntimeError("invalid route")
        label = Mock()
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app.routes_panel = Mock()
        app.plugins_panel = Mock()
        app.appearance_panel = Mock()
        app._post_to_ui = Mock()
        app._set_status = Mock()
        app.start_with_windows = False
        app.set_start_with_windows_enabled = Mock()
        app._get_volume_statuses = Mock()
        app._ui_dpi = 96

        with patch("plugin_manager.PluginManager", return_value=manager), patch("gui.ttk.Label", return_value=label):
            app._start_plugins()

        self.assertIsNone(app._plugin_manager)
        manager.stop.assert_called_once_with(2.0)
        message = app._set_status.call_args.args[0]
        self.assertIn("Plugin system failed: invalid route", message)
        self.assertIn("Restart the app after correcting", message)
        label.grid.assert_called_once()


class PassiveHotkeyTests(unittest.TestCase):
    def make_controller(self) -> PluginHotkeyController:
        return PluginHotkeyController(lambda plugin_id: None, lambda error: None)

    def test_unmodified_and_arbitrary_virtual_keys_are_valid(self) -> None:
        self.assertEqual(HotkeySpec(0, 0x20).label, "Space")
        self.assertEqual(HotkeySpec(0, 0xBA).label, "VK 0xBA")

    def _key_event(
        self, controller: PluginHotkeyController, message: int, virtual_key: int
    ) -> int:
        key_info = KBDLLHOOKSTRUCT()
        key_info.vkCode = virtual_key
        return controller._keyboard_proc(HC_ACTION, message, ctypes.addressof(key_info))

    def test_shortcut_is_observed_and_forwarded_to_the_foreground_application(self) -> None:
        controller = self.make_controller()
        controller._hook_handle = 99
        controller._active.set()
        controller._apply_binding("one", HotkeySpec(MOD_CONTROL, ord("K")))
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47) as next_hook:
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, 0x11), 47)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("K")), 47)

        self.assertEqual(controller._dispatch_queue.get_nowait(), "one")
        self.assertEqual(next_hook.call_count, 2)
        self.assertTrue(all(call.args[0] == 99 for call in next_hook.call_args_list))

    def test_inter_plugin_conflict_is_rejected_before_the_os_call(self) -> None:
        controller = self.make_controller()
        hotkey = HotkeySpec(MOD_CONTROL, ord("K"))
        controller._apply_binding("one", hotkey)
        with self.assertRaises(HotkeyConflictError):
            controller._apply_binding("two", hotkey)

    def test_live_rebinding_replaces_the_previous_observed_combination(self) -> None:
        controller = self.make_controller()
        old = HotkeySpec(MOD_CONTROL, ord("K"))
        new = HotkeySpec(MOD_ALT, ord("L"))
        controller._apply_binding("one", old)
        controller._apply_binding("one", new)
        self.assertEqual(controller._bindings_by_plugin["one"].hotkey, new)
        self.assertNotIn(old, controller._plugins_by_hotkey)
        self.assertEqual(controller._plugins_by_hotkey[new], "one")

    def test_consuming_action_consumes_only_its_held_key_pair(self) -> None:
        controller = self.make_controller()
        controller._hook_handle = 99
        controller._active.set()
        controller._apply_binding("one", HotkeySpec(MOD_CONTROL, ord("K")), consume=True)
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47):
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, 0x11), 47)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("K")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("K")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYUP, ord("K")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYUP, 0x11), 47)
        self.assertEqual(controller._dispatch_queue.get_nowait(), "one")

    def test_repeat_keydown_is_forwarded_but_dispatched_once_until_keyup(self) -> None:
        controller = self.make_controller()
        controller._hook_handle = 99
        controller._active.set()
        controller._apply_binding("one", HotkeySpec(0, 0x20))
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=0) as next_hook:
            self._key_event(controller, WM_KEYDOWN, 0x20)
            self._key_event(controller, WM_KEYDOWN, 0x20)
            self._key_event(controller, WM_KEYUP, 0x20)
            self._key_event(controller, WM_KEYDOWN, 0x20)
        self.assertEqual(
            [controller._dispatch_queue.get_nowait(), controller._dispatch_queue.get_nowait()],
            ["one", "one"],
        )
        self.assertEqual(next_hook.call_count, 4)

    def test_route_binding_reports_lifecycle_and_modifier_release_stops_it(self) -> None:
        events: list[tuple[str, bool]] = []
        controller = PluginHotkeyController(lambda _plugin_id: None, lambda _error: None, lambda binding, pressed: events.append((binding, pressed)))
        controller._hook_handle = 99
        controller._active.set()
        controller.set_route_binding("route/one/up", HotkeySpec(MOD_CONTROL, ord("K")))
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=0):
            self._key_event(controller, WM_KEYDOWN, 0x11)
            self._key_event(controller, WM_KEYDOWN, ord("K"))
            self._key_event(controller, WM_KEYDOWN, ord("K"))
            self._key_event(controller, WM_KEYUP, 0x11)

        self.assertEqual(events, [("route/one/up", True), ("route/one/up", False)])

    def test_forwarding_route_forwards_down_repeats_and_matching_up(self) -> None:
        events: list[tuple[str, bool]] = []
        controller = PluginHotkeyController(lambda _plugin_id: None, lambda _error: None, lambda binding, pressed: events.append((binding, pressed)))
        controller._hook_handle = 99
        controller._active.set()
        controller.set_route_binding("route/one/down", HotkeySpec(0, ord("J")))
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47) as next_hook:
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 47)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 47)
            self.assertEqual(self._key_event(controller, WM_KEYUP, ord("J")), 47)

        self.assertEqual(events, [("route/one/down", True), ("route/one/down", False)])
        self.assertEqual(next_hook.call_count, 3)

    def test_consuming_route_consumes_only_its_held_configured_key_pair(self) -> None:
        events: list[tuple[str, bool]] = []
        controller = PluginHotkeyController(lambda _plugin_id: None, lambda _error: None, lambda binding, pressed: events.append((binding, pressed)))
        controller._hook_handle = 99
        controller._active.set()
        controller.set_route_binding("route/one/down", HotkeySpec(MOD_CONTROL, ord("J")), consume=True)
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47) as next_hook:
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, 0x11), 47)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("L")), 47)
            self.assertEqual(self._key_event(controller, WM_KEYUP, ord("L")), 47)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYUP, ord("J")), 1)
            self.assertEqual(self._key_event(controller, WM_KEYUP, 0x11), 47)

        self.assertEqual(events, [("route/one/down", True), ("route/one/down", False)])
        self.assertEqual(next_hook.call_count, 4)

    def test_removing_consuming_route_releases_repeat_before_keyup_is_forwarded(self) -> None:
        events: list[tuple[str, bool]] = []
        controller = PluginHotkeyController(lambda _plugin_id: None, lambda _error: None, lambda binding, pressed: events.append((binding, pressed)))
        controller._hook_handle = 99
        controller._active.set()
        controller.set_route_binding("route/one/down", HotkeySpec(0, ord("J")), consume=True)
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47) as next_hook:
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 1)
            controller.set_route_binding("route/one/down", None)
            self.assertEqual(self._key_event(controller, WM_KEYUP, ord("J")), 47)

        self.assertEqual(events, [("route/one/down", True), ("route/one/down", False)])
        self.assertEqual(next_hook.call_count, 1)

    def test_consuming_route_releases_on_hook_error_and_shutdown(self) -> None:
        events: list[tuple[str, bool]] = []
        fail_pressed = [True]

        def route_key(binding: str, pressed: bool) -> None:
            events.append((binding, pressed))
            if pressed and fail_pressed[0]:
                raise RuntimeError("dispatch failure")

        errors: list[Exception] = []
        controller = PluginHotkeyController(lambda _plugin_id: None, errors.append, route_key)
        controller._hook_handle = 99
        controller._active.set()
        controller.set_route_binding("route/one/down", HotkeySpec(0, ord("J")), consume=True)
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47):
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("J")), 47)

        self.assertEqual(events, [("route/one/down", True), ("route/one/down", False)])
        self.assertEqual(len(errors), 1)

        events.clear()
        fail_pressed[0] = False
        controller._stopping.clear()
        controller._active.set()
        controller.set_route_binding("route/one/up", HotkeySpec(0, ord("K")), consume=True)
        with patch("plugin_hotkeys.user32.CallNextHookEx", return_value=47):
            self.assertEqual(self._key_event(controller, WM_KEYDOWN, ord("K")), 1)
        controller._request_stop()
        self.assertEqual(events, [("route/one/up", True), ("route/one/up", False)])

    def test_consuming_route_conflicts_with_passive_action_binding(self) -> None:
        controller = self.make_controller()
        controller._active.set()
        hotkey = HotkeySpec(0, ord("J"))
        controller._apply_binding("action/one", hotkey)
        with self.assertRaises(HotkeyConflictError):
            controller.set_route_binding("route/one/down", hotkey, consume=True)

    def test_binding_requires_a_running_observer(self) -> None:
        controller = self.make_controller()
        with self.assertRaises(HotkeyRegistrationError):
            controller.set_binding("one", HotkeySpec(0, 0x20))


if __name__ == "__main__":
    unittest.main()
