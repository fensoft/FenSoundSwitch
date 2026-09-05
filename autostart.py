from __future__ import annotations

import subprocess
import sys
import os
import plistlib
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "FenSoundSwitch"
LEGACY_RUN_VALUE_NAME = "windows-ddc"
MAX_RUN_COMMAND_LENGTH = 260
SOURCE_ENTRYPOINT = Path(__file__).resolve().with_name("app.py")
MACOS_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.fensoundswitch.app.plist"


class AutostartUnavailableError(RuntimeError):
    pass


class AutostartCommandError(ValueError):
    pass


def build_autostart_command(
    launch_target: Path,
    python_executable: Path | None = None,
) -> str:
    target = launch_target.resolve()
    if target.suffix.casefold() == ".exe":
        arguments = [str(target)]
    else:
        if python_executable is None:
            raise AutostartCommandError(
                "A Python executable is required for a source launch target."
            )
        arguments = [str(python_executable.resolve()), str(target)]
    command = subprocess.list2cmdline(arguments)
    if len(command) > MAX_RUN_COMMAND_LENGTH:
        raise AutostartCommandError(
            f"The startup command exceeds Windows' {MAX_RUN_COMMAND_LENGTH}-character limit."
        )
    return command


def current_autostart_command() -> str:
    process_target = Path(sys.argv[0]).resolve()
    if process_target.suffix.casefold() == ".exe":
        return build_autostart_command(process_target)

    python_executable = Path(sys.executable).resolve()
    if python_executable.name.casefold() == "python.exe":
        pythonw_executable = python_executable.with_name("pythonw.exe")
        if pythonw_executable.is_file():
            python_executable = pythonw_executable
    return build_autostart_command(SOURCE_ENTRYPOINT, python_executable)


def _require_winreg():
    if winreg is None:
        raise AutostartUnavailableError("Start with Windows is unavailable on this platform.")
    return winreg


def is_start_with_windows_enabled() -> bool:
    if sys.platform == "darwin" and winreg is None:
        return MACOS_LAUNCH_AGENT.is_file()
    registry = _require_winreg()
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            registry.KEY_QUERY_VALUE,
        ) as key:
            try:
                registry.QueryValueEx(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                # Read the old value for compatibility; only an explicit checkbox
                # action writes or removes Run values.
                registry.QueryValueEx(key, LEGACY_RUN_VALUE_NAME)
    except FileNotFoundError:
        return False
    return True


def set_start_with_windows(enabled: bool) -> None:
    if sys.platform == "darwin" and winreg is None:
        _set_start_with_macos(enabled)
        return
    registry = _require_winreg()
    if enabled:
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            registry.KEY_SET_VALUE,
        ) as key:
            registry.SetValueEx(
                key,
                RUN_VALUE_NAME,
                0,
                registry.REG_SZ,
                current_autostart_command(),
            )
            try:
                registry.DeleteValue(key, LEGACY_RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        return

    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            registry.KEY_SET_VALUE,
        ) as key:
            try:
                registry.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
            try:
                registry.DeleteValue(key, LEGACY_RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _set_start_with_macos(enabled: bool) -> None:
    """Persist a current-user launchd agent without loading it in this process."""
    if not enabled:
        try:
            MACOS_LAUNCH_AGENT.unlink()
        except FileNotFoundError:
            pass
        return
    target = Path(sys.argv[0]).resolve()
    arguments = [str(target)] if target.suffix.casefold() == ".app" else [str(Path(sys.executable).resolve()), str(SOURCE_ENTRYPOINT)]
    destination = MACOS_LAUNCH_AGENT
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        plistlib.dump({"Label": "com.fensoundswitch.app", "ProgramArguments": arguments, "RunAtLoad": True}, stream)
    os.replace(temporary, destination)
