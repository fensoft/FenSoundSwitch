from __future__ import annotations

import sys
import tkinter as tk

from audio_outputs import (
    AudioOutputError,
    parse_internal_rename_request,
    run_internal_rename_helper,
)
from diagnostics import close_logging, configure_logging, get_logger
from gui import MonitorVolumeApp
from windows_platform import (
    InstanceAlreadyRunningError,
    PlatformError,
    SingleInstanceGuard,
    request_existing_instance_restore,
)


def main() -> int:
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
        root = tk.Tk()
        MonitorVolumeApp(root)
        root.mainloop()
    except Exception:
        logger.exception("Unhandled application failure.")
        raise
    finally:
        try:
            logger.info("Application process exiting.")
            close_logging()
        finally:
            instance_guard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
