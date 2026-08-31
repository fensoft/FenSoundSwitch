from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

import core_audio
from plugin_api import PluginHostContext
from plugins.audio_keepalive_plugin import (
    MODE_ALWAYS,
    MODE_RECENT_MOUSE,
    AudioKeepalivePlugin,
    validate_settings,
)


class AudioKeepalivePluginTests(unittest.TestCase):
    def test_settings_default_to_disabled_and_reject_invalid_values(self) -> None:
        self.assertEqual(
            validate_settings({}),
            {
                "schema_version": 1,
                "playback_output": False,
                "voice_output": False,
                "mode": MODE_ALWAYS,
                "mouse_seconds": 60,
            },
        )
        for value in (
            {"playback_output": 1},
            {"mode": "idle"},
            {"mouse_seconds": 0},
            {"unexpected": True},
        ):
            with self.assertRaises(ValueError):
                validate_settings(value)

    def test_initialize_loads_settings_and_reports_enabled_targets(self) -> None:
        plugin = AudioKeepalivePlugin()
        report_status = Mock()
        plugin._run = Mock()  # type: ignore[method-assign]
        host = PluginHostContext(
            plugin_id=plugin.plugin_id,
            ui_parent=Mock(),
            logger=Mock(),
            post_to_ui=lambda callback: callback(),
            report_status=report_status,
            prepare_window=lambda _window: None,
            load_plugin_settings=lambda: {
                "playback_output": True,
                "voice_output": True,
                "mode": MODE_RECENT_MOUSE,
                "mouse_seconds": 30,
            },
        )

        plugin.initialize(host)

        self.assertEqual(plugin._settings["mode"], MODE_RECENT_MOUSE)
        report_status.assert_called_with("Active after mouse movement: Playback, Voice output")
        self.assertTrue(plugin.shutdown(0.2))

    def test_save_settings_persists_normalized_values_and_wakes_worker(self) -> None:
        plugin = AudioKeepalivePlugin()
        saved = Mock()
        plugin._host = PluginHostContext(
            plugin_id=plugin.plugin_id,
            ui_parent=Mock(),
            logger=Mock(),
            post_to_ui=lambda callback: callback(),
            report_status=Mock(),
            prepare_window=lambda _window: None,
            save_plugin_settings=saved,
        )

        plugin._save_settings({"playback_output": True, "voice_output": False, "mode": MODE_ALWAYS, "mouse_seconds": 12})

        saved.assert_called_once_with(
            {"schema_version": 1, "playback_output": True, "voice_output": False, "mode": MODE_ALWAYS, "mouse_seconds": 12}
        )
        self.assertTrue(plugin._changed.is_set())

    def test_role_worker_uses_the_requested_windows_default_role(self) -> None:
        plugin = AudioKeepalivePlugin()
        stop = threading.Event()
        with patch("plugins.audio_keepalive_plugin.core_audio.get_default_audio_endpoint_id", return_value="voice-id") as default_id, patch(
            "plugins.audio_keepalive_plugin.core_audio.keep_endpoint_active"
        ) as keep_active:
            plugin._keep_role_active(core_audio.ROLE_COMMUNICATIONS, stop)

        default_id.assert_called_once_with(core_audio.E_RENDER, core_audio.ROLE_COMMUNICATIONS)
        keep_active.assert_called_once_with("voice-id", stop)

    def test_role_worker_reports_a_nonfatal_failure(self) -> None:
        plugin = AudioKeepalivePlugin()
        report_status = Mock()
        logger = Mock()
        plugin._host = PluginHostContext(
            plugin_id=plugin.plugin_id,
            ui_parent=Mock(),
            logger=logger,
            post_to_ui=lambda callback: callback(),
            report_status=report_status,
            prepare_window=lambda _window: None,
        )
        with patch("plugins.audio_keepalive_plugin.core_audio.get_default_audio_endpoint_id", side_effect=core_audio.CoreAudioError("unavailable")):
            plugin._keep_role_active(core_audio.ROLE_CONSOLE, threading.Event())

        report_status.assert_called_once_with("Playback keep-alive failed: unavailable")
        logger.exception.assert_called_once_with("%s keep-alive worker failed.", "Playback")


if __name__ == "__main__":
    unittest.main()
