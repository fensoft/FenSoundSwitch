from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import ddc_volume_plugin
from ddc import MonitorIdentity, SavedMonitorSelection, SelectionMatch, SelectionMatchStatus


def make_selection() -> SavedMonitorSelection:
    return SavedMonitorSelection("Monitor", MonitorIdentity("device-path", "DEL", 1, "SERIAL"))


class DdcVolumeProviderTests(unittest.TestCase):
    def test_read_uses_the_currently_matched_monitor_and_normalizes_result(self) -> None:
        plugin = ddc_volume_plugin.DdcVolumePlugin()
        plugin._selection = make_selection()
        monitor = Mock(selection_key=make_selection(), identity=Mock(device_path="device-path"))
        with patch("ddc_volume_plugin.enumerate_monitors", return_value=[monitor]), patch(
            "ddc_volume_plugin.match_selected_monitor",
            return_value=SelectionMatch(SelectionMatchStatus.FOUND, 0),
        ), patch("ddc_volume_plugin.read_monitor_volume", return_value=48) as read, patch.object(
            plugin, "_schedule_audio_reconciliation"
        ) as reconcile:
            self.assertEqual(plugin.read_volume(), 48)
        read.assert_called_once_with(monitor)
        reconcile.assert_called_once_with([monitor], monitor)

    def test_write_revalidates_a_fresh_monitor_before_calling_ddc(self) -> None:
        plugin = ddc_volume_plugin.DdcVolumePlugin()
        plugin._selection = make_selection()
        fresh_monitor = Mock(selection_key=make_selection(), identity=Mock(device_path="device-path"))
        with patch("ddc_volume_plugin.enumerate_monitors", return_value=[fresh_monitor]), patch(
            "ddc_volume_plugin.match_selected_monitor",
            return_value=SelectionMatch(SelectionMatchStatus.FOUND, 0),
        ), patch("ddc_volume_plugin.set_monitor_volume", return_value=51) as write, patch.object(
            plugin, "_schedule_audio_reconciliation"
        ):
            self.assertEqual(plugin.write_volume(51), 51)
        write.assert_called_once_with(fresh_monitor, 51)

    def test_audio_policy_does_not_run_while_the_provider_is_inactive(self) -> None:
        plugin = ddc_volume_plugin.DdcVolumePlugin()
        monitor = Mock(identity=Mock(device_path="device-path"))
        with patch("ddc_volume_plugin.threading.Thread") as thread:
            plugin._schedule_audio_reconciliation([monitor], monitor)
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
