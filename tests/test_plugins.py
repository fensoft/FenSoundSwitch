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

from plugin_api import MOD_ALT, MOD_CONTROL, HotkeySpec, OverlayRenderer, PluginHostContext, ShortcutAction, VolumeStatus
from plugin_hotkeys import HotkeyConflictError, HotkeyRegistrationError, PluginHotkeyController
from windows_platform import HC_ACTION, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP
from plugin_manager import (
    PluginManager,
    PluginRecord,
    adjacent_external_plugins_directory,
    discover_plugins,
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
    def configure(self, parent): pass
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
                "ddc-volume",
                "onkyo-volume",
                "denon-marantz-volume",
                "yamaha-volume",
                "pioneer-elite-volume",
                "sony-volume",
                "windows-volume-input",
                "alpha",
                "zeta",
                "beta",
            ],
        )
        self.assertEqual(records[0].source, "Bundled")
        self.assertTrue(records[0].is_overlay_renderer)
        self.assertFalse(records[0].is_volume_provider)
        self.assertIsNone(records[0].input_id)

    def test_adjacent_external_plugins_are_separate_from_the_bundled_package(self) -> None:
        with patch("plugin_manager._runtime_base_directory", return_value=Path("C:/runtime")):
            directory = adjacent_external_plugins_directory()

        self.assertEqual(directory, Path("C:/runtime/external-plugins"))
        self.assertNotEqual(directory.name, "plugins")

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
        create_overlay.assert_called_once_with(parent, dark_mode=True, high_contrast=False, mode="all-routed")

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
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bindings"]["fake/do-thing"], hotkey.to_json())
            manager._dispatch_trigger("fake/do-thing")
            self.assertTrue(plugin.trigger_entered.wait(1.0))
            self.assertEqual(plugin.action_id, "do-thing")
            manager.stop(1.0)

    def test_routed_volume_provider_receives_topology_notification(self) -> None:
        plugin = _FakeVolumePlugin()
        manager, _ = self.make_manager(plugin)
        manager.start()
        manager._input_routes = (plugin_manager.VolumeRoute("route-test", "Test route", plugin_manager.RouteEndpoint("windows-volume-keys", {}), plugin_manager.RouteEndpoint(plugin.plugin_id, {})),)
        manager._route_instances = {"route-test": plugin}
        manager.notify_volume_topology_changed()
        self.assertEqual(plugin.topology_notifications, 1)


class PluginGuiIntegrationTests(unittest.TestCase):
    def test_embedded_panels_are_built_after_plugin_startup(self) -> None:
        from gui import MonitorVolumeApp

        manager = Mock()
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app._resize_for_content = Mock()
        app.routes_panel = Mock()
        app.plugins_panel = Mock()
        app._post_to_ui = Mock()
        app._set_status = Mock()
        app.start_with_windows = False
        app.set_start_with_windows_enabled = Mock()
        app._get_volume_statuses = Mock()
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
        manager.build_action_plugins_panel.assert_called_once_with(app.plugins_panel)


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
        self.assertEqual(controller._bindings_by_plugin["one"], new)
        self.assertNotIn(old, controller._plugins_by_hotkey)
        self.assertEqual(controller._plugins_by_hotkey[new], "one")

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

    def test_binding_requires_a_running_observer(self) -> None:
        controller = self.make_controller()
        with self.assertRaises(HotkeyRegistrationError):
            controller.set_binding("one", HotkeySpec(0, 0x20))


if __name__ == "__main__":
    unittest.main()
