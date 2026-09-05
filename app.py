from __future__ import annotations

import sys
import os
from pathlib import Path

from diagnostics import close_logging, configure_logging, get_logger
from default import ensure_default_configuration
from settings import SETTINGS_PATH
from native_platform import (
    InstanceAlreadyRunningError,
    PlatformError,
    SingleInstanceGuard,
    request_existing_instance_restore,
)


def _is_supported_platform() -> bool:
    return sys.platform in {"win32", "darwin"}


FOREGROUND_ARGUMENT = "--foreground"


def main() -> int:
    if not _is_supported_platform():
        print(f"FenSoundSwitch is supported on Windows and macOS, not {sys.platform}.", file=sys.stderr)
        return 2
    from audio_outputs import (
        AudioOutputError,
        parse_internal_rename_request,
        run_internal_rename_helper,
    )
    import tkinter as tk
    from gui import MonitorVolumeApp
    from web_presentation import CHILD_MODE_ARGUMENT
    restart_requested = False
    foreground_requested = FOREGROUND_ARGUMENT in sys.argv[1:]
    if sys.argv[1:2] == [CHILD_MODE_ARGUMENT]:
        from web_ui_host import main as web_ui_main

        return web_ui_main(sys.argv[2:])
    try:
        rename_endpoint_id = parse_internal_rename_request(sys.argv[1:])
    except AudioOutputError:
        return 2
    if rename_endpoint_id is not None:
        return run_internal_rename_helper(rename_endpoint_id)

    try:
        instance_guard = SingleInstanceGuard()
    except InstanceAlreadyRunningError:
        try:
            request_existing_instance_restore()
        except PlatformError:
            pass
        return 0

    logger = get_logger(__name__)
    try:
        configure_logging()
        logger.info("Application start requested.")
        try:
            ensure_default_configuration(SETTINGS_PATH)
        except Exception as exc:
            logger.warning("Default configuration creation failed (%s).", exc.__class__.__name__)
        root = tk.Tk()
        root.withdraw()
        application = MonitorVolumeApp(root)
        if foreground_requested:
            application.restore_from_tray()
        root.mainloop()
        restart_requested = application.restart_requested is True
    except Exception:
        logger.exception("Unhandled application failure.")
        raise
    finally:
        try:
            logger.info("Application process exiting.")
            close_logging()
        finally:
            instance_guard.close()
    if restart_requested:
        restart_arguments = [FOREGROUND_ARGUMENT] if foreground_requested else []
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            os.execv(sys.executable, [sys.executable, *restart_arguments])
        os.execv(
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *restart_arguments],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
