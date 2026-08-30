from __future__ import annotations

import ctypes
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from plugin_api import MOD_ALT, MOD_CONTROL, HotkeySpec
from plugin_hotkeys import HotkeyConflictError, HotkeyRegistrationError, PluginHotkeyController
from windows_platform import HC_ACTION, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP
from plugin_manager import PluginManager, PluginRecord, discover_plugins


PLUGIN_SOURCE = """
from plugin_api import PLUGIN_API_VERSION
class Plugin:
    plugin_id = {plugin_id!r}
    name = {name!r}
    description = "external test plugin"
    def initialize(self, host): self.host = host
    def configure(self, parent): pass
    def get_hotkey(self): return None
    def trigger(self): pass
    def shutdown(self, timeout): return True
def create_plugin(): return Plugin()
"""


class PluginDiscoveryTests(unittest.TestCase):
    def test_bundled_adjacent_and_user_plugins_load_in_deterministic_order(self) -> None:
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
            ["discord-output", "ddc-volume", "alpha", "zeta", "beta"],
        )
        self.assertEqual(records[0].source, "Bundled")

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

    def get_hotkey(self) -> HotkeySpec | None:
        return self.hotkey

    def trigger(self) -> None:
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
    )


class PluginManagerTests(unittest.TestCase):
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

    def test_initialization_and_no_default_shortcut_are_nonfatal(self) -> None:
        plugin = _FakePlugin()
        manager, _ = self.make_manager(plugin)

        manager.start()

        self.assertIsNotNone(plugin.host)
        self.assertEqual(manager.records[0].status, "Ready")
        self.assertEqual(manager._hotkeys.bindings, [("fake", None)])
        self.assertIsNone(manager.records[0].active_hotkey)

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

        manager._dispatch_trigger("fake")
        self.assertTrue(plugin.trigger_entered.wait(1.0))
        manager._dispatch_trigger("fake")
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

        self.assertEqual(manager._hotkeys.bindings, [("fake", old), ("fake", new)])
        self.assertEqual(manager.records[0].active_hotkey, new)

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

        self.assertIsNone(manager.records[0].active_hotkey)
        self.assertEqual(manager.records[0].shortcut_error, "global shortcut service stopped")
        self.assertTrue(any("shortcuts failed" in notice.lower() for notice in notices))

    def test_ready_volume_provider_can_be_activated_and_notified(self) -> None:
        plugin = _FakeVolumePlugin()
        manager, _ = self.make_manager(plugin)
        changed: list[object] = []
        manager._on_volume_provider_changed = lambda provider, _id: changed.append(provider)
        with patch("plugin_manager.load_active_volume_provider_id", return_value=None), patch(
            "plugin_manager.save_active_volume_provider_id"
        ):
            manager.start()
            self.assertTrue(manager.set_active_volume_provider(plugin.plugin_id))
        self.assertIs(manager.active_volume_provider(), plugin)
        self.assertTrue(plugin.active)
        self.assertTrue(manager.records[0].active_volume_provider)
        manager.notify_volume_topology_changed()
        self.assertEqual(plugin.topology_notifications, 1)
        self.assertTrue(changed)


class PluginGuiIntegrationTests(unittest.TestCase):
    def test_configure_button_delegates_to_the_plugin_manager(self) -> None:
        from gui import MonitorVolumeApp

        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app._plugin_manager = Mock()
        app._set_status = Mock()
        app.configure_plugins()
        app._plugin_manager.show_configuration.assert_called_once_with(app.root)

    def test_missing_plugin_manager_is_nonfatal(self) -> None:
        from gui import MonitorVolumeApp

        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._plugin_manager = None
        app._set_status = Mock()
        app.configure_plugins()
        app._set_status.assert_called_once_with("Plugin configuration is unavailable.")


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
