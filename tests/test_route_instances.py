from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import settings
from plugins import (
    ddc_volume_plugin,
    denon_marantz_volume_plugin,
    onkyo_volume_plugin,
    pioneer_elite_volume_plugin,
    sony_volume_plugin,
    windows_volume_input_plugin,
    yamaha_volume_plugin,
)
from plugin_manager import PluginManager, PluginRecord


class _Input:
    plugin_id = "test-input-plugin"
    name = "Test input"
    description = "Test route input"
    input_id = "test-input"
    input_name = "Test Input"
    def initialize(self, host): pass
    def configure(self, parent): pass
    def create_input(self, parameters): return dict(parameters)
    def shutdown(self, timeout): return True


class _OutputInstance:
    provider_name = "Test output"
    def __init__(self, parameters): self.parameters = dict(parameters)
    def is_volume_provider_available(self): return True, None
    def read_volume(self): return self.parameters.get("volume", 50)
    def write_volume(self, value): self.parameters["volume"] = value; return value
    def activate_volume_provider(self): pass
    def deactivate_volume_provider(self): pass
    def on_volume_topology_changed(self): pass
    def shutdown(self, timeout): return True


class _Output:
    plugin_id = "test-output"
    name = "Test output"
    description = "Test route output"
    provider_name = "Test output"
    def initialize(self, host): pass
    def configure(self, parent): pass
    def create_output(self, parameters): return _OutputInstance(parameters)
    def shutdown(self, timeout): return True


def _record(plugin, output=False):
    return PluginRecord("test-" + plugin.plugin_id, "test", plugin.plugin_id, plugin.name, plugin.description, plugin, is_volume_provider=output, input_id=getattr(plugin, "input_id", None), input_name=getattr(plugin, "input_name", None))


class RouteInstanceTests(unittest.TestCase):
    def test_network_route_editors_prefill_defaults_and_validate_typed_values(self):
        plugins = (
            (onkyo_volume_plugin.OnkyoVolumePlugin(), onkyo_volume_plugin.DEFAULT_PORT),
            (denon_marantz_volume_plugin.DenonMarantzVolumePlugin(), denon_marantz_volume_plugin.DEFAULT_PORT),
            (yamaha_volume_plugin.YamahaVolumePlugin(), yamaha_volume_plugin.DEFAULT_PORT),
            (pioneer_elite_volume_plugin.PioneerEliteVolumePlugin(), pioneer_elite_volume_plugin.DEFAULT_PORT),
            (sony_volume_plugin.SonyVolumePlugin(), sony_volume_plugin.DEFAULT_PORT),
        )
        for plugin, default_port in plugins:
            with self.subTest(plugin=plugin.plugin_id):
                self.assertEqual(plugin.route_output_form_values({}), {"host": "", "port": str(default_port)})
                self.assertEqual(plugin.route_output_form_values({"host": "receiver.local", "port": 1234}), {"host": "receiver.local", "port": "1234"})
                self.assertEqual(plugin.validate_route_output_form(" receiver.local ", "1234"), {"host": "receiver.local", "port": 1234})
                self.assertEqual(plugin.route_output_summary({"host": "receiver.local", "port": 1234}), "Configured: receiver.local:1234")
                self.assertEqual(plugin.route_output_summary({}), "Not configured.")
                with self.assertRaises(ValueError):
                    plugin.validate_route_output_form("", "0")

    def test_ddc_route_editor_reports_friendly_selected_monitor_status(self):
        plugin = ddc_volume_plugin.DdcVolumePlugin()
        self.assertEqual(plugin.route_output_status({}), "No monitor selected for this route.")
        self.assertEqual(
            plugin.route_output_status({"selected_monitor": {"description": "Desk display", "identity": {"device_path": "DISPLAY#1"}}}),
            "Selected monitor: Desk display",
        )
        self.assertEqual(plugin.route_output_summary({}), "No monitor selected for this route.")

    def test_ddc_route_selection_round_trips_unique_legacy_description(self):
        selection = ddc_volume_plugin.SavedMonitorSelection(
            "Desk display",
            legacy_ordinal=1,
        )

        saved = ddc_volume_plugin._selection_to_json(selection)

        self.assertEqual(ddc_volume_plugin._selection_from_json(saved), selection)

    def test_ddc_route_instance_retains_the_initialized_host_boundary(self):
        plugin = ddc_volume_plugin.DdcVolumePlugin()
        host = Mock()
        plugin.initialize(host)

        instance = plugin.create_output({"selected_monitor": {"description": "Desk display", "identity": {"device_path": "DISPLAY#1"}}})

        self.assertIs(instance._host, host)

    def test_windows_input_declares_a_summary_but_no_route_editor(self):
        plugin = windows_volume_input_plugin.WindowsVolumeInputPlugin()
        self.assertEqual(plugin.route_input_summary({}), "No settings.")
        self.assertFalse(callable(getattr(plugin, "configure_route_input", None)))

    def test_v5_routes_migrate_parameters_without_deleting_plugin_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_settings = settings.SETTINGS_PATH
            settings.SETTINGS_PATH = root / "settings.json"
            try:
                settings.SETTINGS_PATH.write_text(json.dumps({"schema_version": 5, "volume_routes": [{"route_id": "route-one", "input_id": "test-input", "provider_id": "test-output"}]}), encoding="utf-8")
                routes = settings.load_input_routes()
                self.assertEqual(routes[0].input.plugin_id, "test-input")
                settings.save_input_routes(routes)
                payload = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], 8)
                self.assertEqual(payload["volume_routes"][0]["output"]["plugin_id"], "test-output")
                self.assertEqual(payload["volume_routes"][0]["name"], "test-input to test-output")
            finally:
                settings.SETTINGS_PATH = old_settings

    def test_same_output_definition_creates_isolated_route_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            old_settings = settings.SETTINGS_PATH
            settings.SETTINGS_PATH = Path(directory) / "settings.json"
            try:
                manager = PluginManager(Mock(), lambda callback: callback(), lambda message: None, hotkey_factory=Mock)
                inputs, output = _Input(), _Output()
                with patch("plugin_manager.discover_plugins", return_value=[_record(inputs), _record(output, True)]), patch("plugin_manager.load_input_routes", return_value=()):
                    manager.start()
                self.assertTrue(manager.add_route("test-input", "test-output", output_parameters={"volume": 10}))
                self.assertTrue(manager.add_route("test-input", "test-output", output_parameters={"volume": 80}))
                providers = manager.volume_providers_for_input("test-input")
                self.assertEqual([route.name for route, _provider in providers], ["Test Input to Test output", "Test Input to Test output"])
                self.assertEqual([provider.read_volume() for _, provider in providers], [10, 80])
                self.assertTrue(
                    all(route.route_id.startswith("route-") for route, _provider in manager.relevant_volume_providers())
                )
                providers[0][1].write_volume(20)
                self.assertEqual(providers[1][1].read_volume(), 80)
            finally:
                settings.SETTINGS_PATH = old_settings
