from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import autostart


class AutostartCommandTests(unittest.TestCase):
    def test_packaged_command_quotes_the_executable_without_python(self) -> None:
        executable = Path(r"C:\Program Files\windows-ddc\windows-ddc.exe")

        command = autostart.build_autostart_command(executable)

        self.assertEqual(command, subprocess.list2cmdline([str(executable)]))

    def test_source_command_quotes_python_and_app_paths(self) -> None:
        python_executable = Path(r"C:\Program Files\Python\pythonw.exe")
        entrypoint = Path(r"C:\Users\Example User\windows-ddc\app.py")

        command = autostart.build_autostart_command(entrypoint, python_executable)

        self.assertEqual(
            command,
            subprocess.list2cmdline([str(python_executable), str(entrypoint)]),
        )

    def test_source_command_requires_a_python_executable(self) -> None:
        with self.assertRaisesRegex(autostart.AutostartCommandError, "Python executable"):
            autostart.build_autostart_command(Path("app.py"))

    def test_command_longer_than_the_windows_run_limit_is_rejected(self) -> None:
        long_target = Path("C:/") / ("nested/" * 40) / "windows-ddc.exe"

        with self.assertRaisesRegex(
            autostart.AutostartCommandError,
            "260-character limit",
        ):
            autostart.build_autostart_command(long_target)

    def test_current_source_command_prefers_pythonw(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            python_executable = directory / "python.exe"
            pythonw_executable = directory / "pythonw.exe"
            source_entrypoint = directory / "project with spaces" / "app.py"
            python_executable.touch()
            pythonw_executable.touch()
            with patch.object(autostart.sys, "argv", [str(source_entrypoint)]), patch.object(
                autostart.sys,
                "executable",
                str(python_executable),
            ), patch.object(autostart, "SOURCE_ENTRYPOINT", source_entrypoint):
                command = autostart.current_autostart_command()
                expected = subprocess.list2cmdline(
                    [str(pythonw_executable.resolve()), str(source_entrypoint.resolve())]
                )

            self.assertEqual(command, expected)

    def test_current_packaged_command_uses_the_original_argv_path(self) -> None:
        executable = Path(r"C:\Program Files\windows-ddc\windows-ddc.exe")
        with patch.object(autostart.sys, "argv", [str(executable)]), patch.object(
            autostart.sys,
            "executable",
            r"C:\Temp\onefile_123\windows-ddc.exe",
        ):
            command = autostart.current_autostart_command()

        self.assertEqual(command, subprocess.list2cmdline([str(executable)]))


class AutostartRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = MagicMock()
        self.registry.HKEY_CURRENT_USER = object()
        self.registry.KEY_QUERY_VALUE = 1
        self.registry.KEY_SET_VALUE = 2
        self.registry.REG_SZ = 1
        self.key = self.registry.OpenKey.return_value.__enter__.return_value

    def test_existing_value_is_enabled(self) -> None:
        self.registry.QueryValueEx.return_value = ("command", self.registry.REG_SZ)

        with patch.object(autostart, "winreg", self.registry):
            enabled = autostart.is_start_with_windows_enabled()

        self.assertTrue(enabled)
        self.registry.OpenKey.assert_called_once_with(
            self.registry.HKEY_CURRENT_USER,
            autostart.RUN_KEY_PATH,
            0,
            self.registry.KEY_QUERY_VALUE,
        )
        self.registry.QueryValueEx.assert_called_once_with(self.key, autostart.RUN_VALUE_NAME)

    def test_missing_key_or_value_is_disabled(self) -> None:
        for missing_call in ("OpenKey", "QueryValueEx"):
            with self.subTest(missing_call=missing_call):
                registry = MagicMock()
                registry.HKEY_CURRENT_USER = object()
                registry.KEY_QUERY_VALUE = 1
                key = registry.OpenKey.return_value.__enter__.return_value
                getattr(registry, missing_call).side_effect = FileNotFoundError
                with patch.object(autostart, "winreg", registry):
                    self.assertFalse(autostart.is_start_with_windows_enabled())
                if missing_call == "QueryValueEx":
                    registry.QueryValueEx.assert_called_once_with(
                        key,
                        autostart.RUN_VALUE_NAME,
                    )

    def test_enabling_writes_the_current_user_run_value(self) -> None:
        key = self.registry.CreateKeyEx.return_value.__enter__.return_value
        with patch.object(autostart, "winreg", self.registry), patch(
            "autostart.current_autostart_command",
            return_value='"C:\\Program Files\\windows-ddc.exe"',
        ):
            autostart.set_start_with_windows(True)

        self.registry.SetValueEx.assert_called_once_with(
            key,
            autostart.RUN_VALUE_NAME,
            0,
            self.registry.REG_SZ,
            '"C:\\Program Files\\windows-ddc.exe"',
        )
        self.registry.CreateKeyEx.assert_called_once_with(
            self.registry.HKEY_CURRENT_USER,
            autostart.RUN_KEY_PATH,
            0,
            self.registry.KEY_SET_VALUE,
        )

    def test_disabling_deletes_the_value_and_missing_state_is_nonfatal(self) -> None:
        with patch.object(autostart, "winreg", self.registry):
            autostart.set_start_with_windows(False)

        self.registry.DeleteValue.assert_called_once_with(
            self.key,
            autostart.RUN_VALUE_NAME,
        )

        value_missing_registry = MagicMock()
        value_missing_registry.HKEY_CURRENT_USER = object()
        value_missing_registry.KEY_SET_VALUE = 2
        value_missing_registry.DeleteValue.side_effect = FileNotFoundError
        with patch.object(autostart, "winreg", value_missing_registry):
            autostart.set_start_with_windows(False)

        missing_registry = MagicMock()
        missing_registry.HKEY_CURRENT_USER = object()
        missing_registry.KEY_SET_VALUE = 2
        missing_registry.OpenKey.side_effect = FileNotFoundError
        with patch.object(autostart, "winreg", missing_registry):
            autostart.set_start_with_windows(False)

    def test_unavailable_platform_is_reported(self) -> None:
        with patch.object(autostart, "winreg", None):
            with self.assertRaises(autostart.AutostartUnavailableError):
                autostart.is_start_with_windows_enabled()


if __name__ == "__main__":
    unittest.main()
