from __future__ import annotations

import sys
import tkinter as tk
import os
from pathlib import Path

from audio_outputs import (
    AudioOutputError,
    parse_internal_rename_request,
    run_internal_rename_helper,
)
from diagnostics import close_logging, configure_logging, get_logger
from default import ensure_default_configuration
from gui import MonitorVolumeApp
from settings import SETTINGS_PATH
from windows_platform import (
    InstanceAlreadyRunningError,
    PlatformError,
    SingleInstanceGuard,
    request_existing_instance_restore,
)
from web_presentation import CHILD_MODE_ARGUMENT


def main() -> int:
    restart_requested = False
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
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            os.execv(sys.executable, [sys.executable])
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
