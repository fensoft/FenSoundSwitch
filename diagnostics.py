from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "fensoundswitch"
LOG_MAX_BYTES = 512 * 1024
LOG_BACKUP_COUNT = 2
LOG_BASE_PATH = Path(
    os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home()
)
LOG_PATH = LOG_BASE_PATH / "fensoundswitch" / "fensoundswitch.log"
_HANDLER_MARKER = "_fensoundswitch_managed_handler"
_CONFIGURATION_LOCK = threading.Lock()
_BASE_LOGGER = logging.getLogger(LOGGER_NAME)
_BASE_LOGGER.addHandler(logging.NullHandler())


def get_logger(component: str) -> logging.Logger:
    component_name = component.rsplit(".", 1)[-1]
    return logging.getLogger(f"{LOGGER_NAME}.{component_name}")


def configure_logging(log_path: Path | None = None) -> logging.Logger:
    logger = _BASE_LOGGER
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with _CONFIGURATION_LOCK:
        if any(getattr(handler, _HANDLER_MARKER, False) for handler in logger.handlers):
            return logger

        destination = log_path or LOG_PATH
        candidate: logging.Handler | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate = RotatingFileHandler(
                destination,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            candidate.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
            handler = candidate
        except Exception:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            handler = logging.NullHandler()

        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)
    return logger


def read_log_contents(log_path: Path | None = None) -> str:
    """Return the active diagnostic log and retained rotations, oldest first."""
    destination = log_path or LOG_PATH
    entries: list[str] = []
    for backup_index in range(LOG_BACKUP_COUNT, -1, -1):
        path = destination if backup_index == 0 else Path(f"{destination}.{backup_index}")
        try:
            contents = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError as exc:
            entries.append(f"Could not read {path.name}: {exc}")
            continue
        if backup_index:
            entries.append(f"--- {path.name} ---\n{contents}")
        else:
            entries.append(contents)
    if not entries:
        return "No diagnostic log entries are available yet."
    return "\n".join(entries)


def close_logging() -> None:
    logger = _BASE_LOGGER
    with _CONFIGURATION_LOCK:
        managed_handlers = [
            handler
            for handler in logger.handlers
            if getattr(handler, _HANDLER_MARKER, False)
        ]
        for handler in managed_handlers:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
