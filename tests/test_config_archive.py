from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from config_archive import (
    ARCHIVE_EXTENSION,
    DEFAULT_ARCHIVE_NAME,
    ConfigurationArchiveError,
    add_to_import_history,
    configuration_directory,
    export_configuration,
    import_configuration,
    latest_configuration,
    recent_configurations,
)
from default import ensure_default_configuration
from plugins import audio_keepalive_plugin, keyboard_input_plugin


class ConfigurationArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.settings_path = self.root / "settings.json"
        self.plugins_path = self.root / "plugin-settings"

    def write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_export_and_import_replace_all_non_secret_configuration(self) -> None:
        self.write_json(self.settings_path, {"schema_version": 8, "change_speed": "fast"})
        self.write_json(self.plugins_path / "receiver.json", {"host": "living-room"})
        archive = self.root / f"backup{ARCHIVE_EXTENSION}"

        export_configuration(archive, self.settings_path, self.plugins_path)

        self.write_json(self.settings_path, {"schema_version": 8, "change_speed": "slow"})
        self.write_json(self.plugins_path / "obsolete.json", {"remove": True})
        import_configuration(archive, self.settings_path, self.plugins_path)

        self.assertEqual(json.loads(self.settings_path.read_text(encoding="utf-8"))["change_speed"], "fast")
        self.assertEqual(json.loads((self.plugins_path / "receiver.json").read_text(encoding="utf-8")), {"host": "living-room"})
        self.assertFalse((self.plugins_path / "obsolete.json").exists())

    def test_invalid_archive_does_not_change_saved_configuration(self) -> None:
        self.write_json(self.settings_path, {"schema_version": 8, "change_speed": "fast"})
        self.write_json(self.plugins_path / "receiver.json", {"host": "living-room"})
        archive = self.root / f"broken{ARCHIVE_EXTENSION}"
        with zipfile.ZipFile(archive, "w") as contents:
            contents.writestr("settings.json", "[]")

        with self.assertRaises(ConfigurationArchiveError):
            import_configuration(archive, self.settings_path, self.plugins_path)

        self.assertEqual(json.loads(self.settings_path.read_text(encoding="utf-8"))["change_speed"], "fast")
        self.assertTrue((self.plugins_path / "receiver.json").exists())

    def test_latest_ignores_the_reserved_default_archive(self) -> None:
        directory = configuration_directory(self.settings_path)
        directory.mkdir()
        default = directory / DEFAULT_ARCHIVE_NAME
        first = directory / f"first{ARCHIVE_EXTENSION}"
        latest = directory / f"latest{ARCHIVE_EXTENSION}"
        for path in (default, first, latest):
            path.write_bytes(b"archive")
        os.utime(first, (1, 1))
        os.utime(latest, (2, 2))

        self.assertEqual(latest_configuration(self.settings_path), latest)

    def test_export_saved_elsewhere_is_added_to_latest_import_history(self) -> None:
        archive = self.root / f"chosen-location{ARCHIVE_EXTENSION}"
        self.write_json(self.settings_path, {"schema_version": 8})
        export_configuration(archive, self.settings_path, self.plugins_path)

        history_archive = add_to_import_history(archive, self.settings_path)

        self.assertNotEqual(history_archive, archive)
        self.assertTrue(history_archive.is_file())
        self.assertEqual(latest_configuration(self.settings_path), history_archive)

    def test_recent_configurations_returns_at_most_five_newest_archives(self) -> None:
        directory = configuration_directory(self.settings_path)
        directory.mkdir()
        archives = [directory / f"archive-{index}{ARCHIVE_EXTENSION}" for index in range(6)]
        for index, archive in enumerate(archives):
            archive.write_bytes(b"archive")
            os.utime(archive, (index, index))

        self.assertEqual(recent_configurations(settings_path=self.settings_path), tuple(reversed(archives[-5:])))

    def test_bundled_default_archive_has_requested_routes_and_plugin_settings(self) -> None:
        archive = ensure_default_configuration(self.settings_path)
        imported_settings = self.root / "imported" / "settings.json"
        imported_plugins = imported_settings.parent / "plugin-settings"

        import_configuration(archive, imported_settings, imported_plugins)

        payload = json.loads(imported_settings.read_text(encoding="utf-8"))
        routes = payload["volume_routes"]
        self.assertEqual([route["name"] for route in routes], ["Output", "Voice"])
        self.assertEqual(routes[1]["input"]["plugin_id"], "keyboard-keys")
        signals = payload["action_signals"]
        self.assertEqual([signal["signal_id"] for signal in signals], ["cycle-playback", "cycle-input"])
        self.assertEqual(signals[0]["slots"][0]["action_id"], "cycle-playback")
        voice_keys = routes[1]["input"]["parameters"]
        self.assertEqual(voice_keys["volume_down"], {"modifiers": 3, "virtual_key": 0x78})
        self.assertEqual(voice_keys["volume_up"], {"modifiers": 3, "virtual_key": 0x79})
        self.assertEqual(
            audio_keepalive_plugin.validate_settings(
                json.loads((imported_plugins / "audio-keepalive.json").read_text(encoding="utf-8"))
            )["mode"],
            "recent-mouse",
        )
        keyboard_input_plugin.validate_parameters(voice_keys)
        self.assertEqual(
            json.loads((imported_plugins / "action-plugin-state.json").read_text(encoding="utf-8"))["disabled_plugin_ids"],
            ["discord-output"],
        )

        self.assertEqual(ensure_default_configuration(self.settings_path), archive)
