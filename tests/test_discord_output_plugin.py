from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from plugins import discord_output_plugin as discord
from plugin_api import PluginHostContext


class DiscordPureFunctionTests(unittest.TestCase):
    def test_alternative_selection_prefers_first_non_current_concrete_device(self) -> None:
        output = {
            "device_id": "current",
            "available_devices": [
                {"id": "current"},
                {"id": "default"},
                {"id": "speaker"},
                {"id": "headset"},
            ],
        }
        self.assertEqual(discord._choose_alternative(output), ("current", "speaker"))

    def test_default_route_alone_is_not_treated_as_a_concrete_alternative(self) -> None:
        with self.assertRaises(discord.DiscordRpcError):
            discord._choose_alternative(
                {"device_id": "speaker", "available_devices": [{"id": "default"}]}
            )

    def test_plugin_declares_only_its_named_host_owned_shortcut_action(self) -> None:
        plugin = discord.DiscordOutputPlugin()
        self.assertEqual(
            plugin.get_shortcut_actions()[0].action_id,
            "switch-output",
        )


class DiscordCredentialTests(unittest.TestCase):
    def test_valid_legacy_credential_is_copied_to_the_plugin_target_without_deletion(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 99.0,
        }
        with patch("plugins.discord_output_plugin._read_credential", side_effect=(None, saved)), patch(
            "plugins.discord_output_plugin._write_credential"
        ) as write, patch("plugins.discord_output_plugin._delete_credential") as delete:
            result = discord._load_saved_oauth()

        self.assertEqual(result, saved)
        write.assert_called_once_with(discord._CREDENTIAL_TARGET, saved)
        delete.assert_not_called()

    def test_token_response_rotates_refresh_token_and_calculates_expiry(self) -> None:
        configuration = discord._saved_client_configuration(
            "123456789012345", "secret"
        )
        with patch("plugins.discord_output_plugin.time.time", return_value=100.0):
            saved = discord._saved_oauth_from_token_response(
                configuration,
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 60},
            )
        self.assertEqual(saved["refresh_token"], "new-refresh")
        self.assertEqual(saved["expires_at"], 130.0)

    def test_unexpired_access_token_is_reused_without_refresh(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 200.0,
        }
        client = Mock()
        with patch("plugins.discord_output_plugin.time.time", return_value=100.0), patch(
            "plugins.discord_output_plugin._authenticate"
        ) as authenticate, patch("plugins.discord_output_plugin._refresh_saved_oauth") as refresh:
            self.assertIs(discord._authenticate_saved_oauth(client, saved), saved)
        authenticate.assert_called_once_with(client, "access")
        refresh.assert_not_called()

    def test_expired_access_token_refreshes_and_persists_rotation(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 50.0,
        }
        refreshed = dict(saved, access_token="new-access", refresh_token="new-refresh", expires_at=200.0)
        client = Mock()
        with patch("plugins.discord_output_plugin.time.time", return_value=100.0), patch(
            "plugins.discord_output_plugin._refresh_saved_oauth", return_value=refreshed
        ), patch("plugins.discord_output_plugin._authenticate") as authenticate, patch(
            "plugins.discord_output_plugin._save_oauth"
        ) as save:
            self.assertEqual(discord._authenticate_saved_oauth(client, saved), refreshed)
        authenticate.assert_called_once_with(client, "new-access")
        save.assert_called_once_with(refreshed)

    def test_revoked_refresh_grant_requires_explicit_reauthorization(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "old-access",
            "refresh_token": "revoked",
            "expires_at": 50.0,
        }
        with patch("plugins.discord_output_plugin.time.time", return_value=100.0), patch(
            "plugins.discord_output_plugin._refresh_saved_oauth",
            side_effect=discord.DiscordRpcError("revoked"),
        ):
            with self.assertRaisesRegex(discord.DiscordRpcError, "reset authorization"):
                discord._authenticate_saved_oauth(Mock(), saved)


class DiscordSwitchTests(unittest.TestCase):
    def make_client(self) -> Mock:
        client = Mock()
        client.request.side_effect = [
            {
                "output": {
                    "device_id": "initial",
                    "available_devices": [{"id": "initial"}, {"id": "temporary"}],
                }
            },
            {"output": {"device_id": "temporary"}},
            {"output": {"device_id": "initial"}},
        ]
        return client

    def test_output_is_switched_then_restored_exactly(self) -> None:
        client = self.make_client()
        stop_event = Mock()

        discord._switch_authenticated_output(client, 1.0, stop_event)

        self.assertEqual(
            client.request.call_args_list,
            [
                call("GET_VOICE_SETTINGS"),
                call("SET_VOICE_SETTINGS", {"output": {"device_id": "temporary"}}),
                call("SET_VOICE_SETTINGS", {"output": {"device_id": "initial"}}),
            ],
        )
        stop_event.wait.assert_called_once_with(1.0)

    def test_restoration_is_attempted_when_the_wait_is_interrupted(self) -> None:
        client = self.make_client()
        stop_event = Mock()
        stop_event.wait.side_effect = RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            discord._switch_authenticated_output(client, 1.0, stop_event)
        self.assertEqual(client.request.call_args_list[-1], call(
            "SET_VOICE_SETTINGS", {"output": {"device_id": "initial"}}
        ))

    def test_restoration_failure_is_reported(self) -> None:
        client = Mock()
        client.request.side_effect = [
            {
                "output": {
                    "device_id": "initial",
                    "available_devices": [{"id": "initial"}, {"id": "temporary"}],
                }
            },
            {"output": {"device_id": "temporary"}},
            {"output": {"device_id": "wrong"}},
        ]
        with self.assertRaisesRegex(discord.DiscordRpcError, "restoration"):
            discord._switch_authenticated_output(client, 1.0, Mock())


class DiscordPluginLifecycleTests(unittest.TestCase):
    def make_host(self, path: Path) -> PluginHostContext:
        return PluginHostContext(
            plugin_id="discord-output",
            ui_parent=Mock(),
            logger=Mock(),
            post_to_ui=lambda callback: callback(),
            report_status=Mock(),
            prepare_window=Mock(),
        )

    def test_existing_grant_validates_off_the_initializing_thread(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 99.0,
        }
        plugin = discord.DiscordOutputPlugin()
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "plugins.discord_output_plugin._load_saved_oauth", return_value=saved
        ), patch.object(plugin, "_start_worker", return_value=True) as start_worker:
            plugin.initialize(self.make_host(Path(temporary_directory) / "settings.json"))
        start_worker.assert_called_once_with(plugin._validate_authorization, False)

    def test_first_setup_saves_client_configuration_then_authorizes(self) -> None:
        saved = discord._saved_client_configuration("123456789012345", "secret")
        plugin = discord.DiscordOutputPlugin()
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "plugins.discord_output_plugin._load_saved_oauth", return_value=None
        ), patch.object(plugin, "_show_setup_dialog", return_value=saved), patch(
            "plugins.discord_output_plugin._save_oauth"
        ) as save, patch.object(plugin, "_start_worker", return_value=True) as start_worker:
            plugin.initialize(self.make_host(Path(temporary_directory) / "settings.json"))
        save.assert_called_once_with(saved)
        start_worker.assert_called_once_with(plugin._validate_authorization, True)

    def test_repeat_trigger_is_ignored_while_an_operation_is_active(self) -> None:
        plugin = discord.DiscordOutputPlugin()
        plugin._operation_lock.acquire()
        try:
            with patch("plugins.discord_output_plugin._load_saved_oauth") as load:
                plugin.trigger_shortcut("switch-output")
            load.assert_not_called()
        finally:
            plugin._operation_lock.release()

    def test_shutdown_signals_an_active_swap_before_using_disconnect_fallback(self) -> None:
        plugin = discord.DiscordOutputPlugin()
        client = Mock()
        plugin._clients.add(client)
        plugin._operation_lock.acquire()

        def release_after_signal() -> None:
            self.assertTrue(plugin._shutdown.wait(1.0))
            plugin._operation_lock.release()

        worker = threading.Thread(target=release_after_signal)
        worker.start()
        self.assertTrue(plugin.shutdown(1.0))
        worker.join(1.0)
        client.close.assert_not_called()

    def test_shutdown_closes_the_pipe_when_the_operation_misses_its_budget(self) -> None:
        plugin = discord.DiscordOutputPlugin()
        client = Mock()
        plugin._clients.add(client)
        plugin._operation_lock.acquire()
        try:
            self.assertFalse(plugin.shutdown(0.0))
        finally:
            plugin._operation_lock.release()
        client.close.assert_called_once_with()

    def test_missing_discord_marks_only_the_plugin_unavailable(self) -> None:
        saved = {
            "version": 1,
            "client_id": "123456789012345",
            "client_secret": "secret",
            "redirect_uri": "https://127.0.0.1",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 99.0,
        }
        plugin = discord.DiscordOutputPlugin()
        report_status = Mock()
        plugin._host = PluginHostContext(
            plugin_id="discord-output",
            ui_parent=Mock(),
            logger=Mock(),
            post_to_ui=lambda callback: callback(),
            report_status=report_status,
            prepare_window=Mock(),
        )
        client = Mock()
        client.connect.side_effect = discord.DiscordRpcError("Discord is not running")
        with patch("plugins.discord_output_plugin._load_saved_oauth", return_value=saved), patch(
            "plugins.discord_output_plugin.DiscordRpcClient", return_value=client
        ):
            plugin._validate_authorization(False)
        self.assertIn("Authorization failed", plugin._current_status())
        report_status.assert_called()


if __name__ == "__main__":
    unittest.main()
