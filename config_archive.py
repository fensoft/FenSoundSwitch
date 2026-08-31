from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
import zipfile
from pathlib import Path

from settings import SETTINGS_PATH


ARCHIVE_EXTENSION = ".fsc"
ARCHIVE_DIRECTORY_NAME = "configurations"
DEFAULT_ARCHIVE_NAME = f"default{ARCHIVE_EXTENSION}"
SETTINGS_ARCHIVE_NAME = "settings.json"
PLUGIN_SETTINGS_DIRECTORY_NAME = "plugin-settings"


class ConfigurationArchiveError(ValueError):
    """A configuration archive could not be safely read or applied."""


def configuration_directory(settings_path: Path = SETTINGS_PATH) -> Path:
    return settings_path.parent / ARCHIVE_DIRECTORY_NAME


def plugin_settings_directory(settings_path: Path = SETTINGS_PATH) -> Path:
    return settings_path.parent / PLUGIN_SETTINGS_DIRECTORY_NAME


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationArchiveError(f"Invalid configuration file: {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigurationArchiveError(f"Configuration file must contain a JSON object: {path.name}")
    return value


def _write_json_object(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _archive_member_plugin_name(name: str) -> str | None:
    prefix = f"{PLUGIN_SETTINGS_DIRECTORY_NAME}/"
    if not name.startswith(prefix):
        return None
    filename = name[len(prefix):]
    if not filename.endswith(".json") or Path(filename).name != filename:
        return None
    return filename


def export_configuration(
    destination: Path,
    settings_path: Path = SETTINGS_PATH,
    plugin_settings_path: Path | None = None,
) -> None:
    """Write all non-secret application configuration to one archive."""
    plugin_settings_path = plugin_settings_path or plugin_settings_directory(settings_path)
    settings_payload = _read_json_object(settings_path) if settings_path.exists() else {}
    plugin_payloads: dict[str, dict[str, object]] = {}
    if plugin_settings_path.is_dir():
        for path in plugin_settings_path.glob("*.json"):
            plugin_payloads[path.name] = _read_json_object(path)

    write_configuration_archive(destination, settings_payload, plugin_payloads)


def write_configuration_archive(
    destination: Path,
    settings_payload: dict[str, object],
    plugin_payloads: dict[str, dict[str, object]],
) -> None:
    """Write validated JSON configuration payloads to an archive."""
    try:
        settings_payload = json.loads(json.dumps(settings_payload))
        plugin_payloads = {
            filename: json.loads(json.dumps(payload))
            for filename, payload in plugin_payloads.items()
        }
    except (TypeError, ValueError) as exc:
        raise ConfigurationArchiveError("Configuration archive contains non-JSON values.") from exc
    if not isinstance(settings_payload, dict) or any(
        Path(filename).name != filename or not filename.endswith(".json") or not isinstance(payload, dict)
        for filename, payload in plugin_payloads.items()
    ):
        raise ConfigurationArchiveError("Configuration archive contains invalid settings.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(SETTINGS_ARCHIVE_NAME, json.dumps(settings_payload, indent=2))
            for filename, payload in plugin_payloads.items():
                archive.writestr(
                    f"{PLUGIN_SETTINGS_DIRECTORY_NAME}/{filename}",
                    json.dumps(payload, indent=2),
                )
        os.replace(temporary, destination)
    except (OSError, zipfile.BadZipFile) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigurationArchiveError(f"Could not export configuration: {destination.name}") from exc


def _read_archive(source: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if SETTINGS_ARCHIVE_NAME not in names:
                raise ConfigurationArchiveError("Archive does not contain settings.json.")
            settings_payload = json.loads(archive.read(SETTINGS_ARCHIVE_NAME).decode("utf-8"))
            if not isinstance(settings_payload, dict):
                raise ConfigurationArchiveError("Archive settings.json must contain a JSON object.")
            plugin_payloads: dict[str, dict[str, object]] = {}
            for name in names:
                filename = _archive_member_plugin_name(name)
                if filename is None:
                    continue
                payload = json.loads(archive.read(name).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ConfigurationArchiveError(f"Archive plugin settings must be JSON objects: {filename}")
                plugin_payloads[filename] = payload
            return settings_payload, plugin_payloads
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ConfigurationArchiveError(f"Invalid configuration archive: {source.name}") from exc


def import_configuration(
    source: Path,
    settings_path: Path = SETTINGS_PATH,
    plugin_settings_path: Path | None = None,
) -> None:
    """Replace saved non-secret configuration after fully validating an archive."""
    settings_payload, plugin_payloads = _read_archive(source)
    plugin_settings_path = plugin_settings_path or plugin_settings_directory(settings_path)
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="fensoundswitch-config-", dir=settings_path.parent))
    except OSError as exc:
        raise ConfigurationArchiveError(f"Could not import configuration: {source.name}") from exc
    try:
        staged_settings = staging_root / SETTINGS_ARCHIVE_NAME
        _write_json_object(staged_settings, settings_payload)
        staged_plugins = staging_root / PLUGIN_SETTINGS_DIRECTORY_NAME
        for filename, payload in plugin_payloads.items():
            _write_json_object(staged_plugins / filename, payload)

        backup_plugins = plugin_settings_path.with_name(f"{plugin_settings_path.name}.previous")
        if backup_plugins.exists():
            shutil.rmtree(backup_plugins)
        if plugin_settings_path.exists():
            plugin_settings_path.replace(backup_plugins)
        try:
            if staged_plugins.exists():
                staged_plugins.replace(plugin_settings_path)
            _write_json_object(settings_path, settings_payload)
        except OSError:
            if plugin_settings_path.exists():
                shutil.rmtree(plugin_settings_path)
            if backup_plugins.exists():
                backup_plugins.replace(plugin_settings_path)
            raise
        else:
            if backup_plugins.exists():
                shutil.rmtree(backup_plugins)
    except OSError as exc:
        raise ConfigurationArchiveError(f"Could not import configuration: {source.name}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def recent_configurations(limit: int = 5, settings_path: Path = SETTINGS_PATH) -> tuple[Path, ...]:
    """Return the newest non-default archives from the import history."""
    if limit < 1:
        return ()
    directory = configuration_directory(settings_path)
    try:
        candidates = [
            path for path in directory.glob(f"*{ARCHIVE_EXTENSION}")
            if path.name.casefold() != DEFAULT_ARCHIVE_NAME.casefold()
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return tuple(candidates[:limit])
    except OSError:
        return ()


def latest_configuration(settings_path: Path = SETTINGS_PATH) -> Path | None:
    recent = recent_configurations(1, settings_path)
    return recent[0] if recent else None


def add_to_import_history(source: Path, settings_path: Path = SETTINGS_PATH) -> Path:
    """Copy an exported archive into the directory used by latest import."""
    directory = configuration_directory(settings_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if source.parent.resolve() == directory.resolve():
            return source
        destination = directory / (
            f"FenSoundSwitch-{datetime.now():%Y%m%d-%H%M%S-%f}{ARCHIVE_EXTENSION}"
        )
        shutil.copyfile(source, destination)
        return destination
    except OSError as exc:
        raise ConfigurationArchiveError("Configuration was exported, but could not be added to import history.") from exc
