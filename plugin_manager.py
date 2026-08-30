from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from types import ModuleType
from typing import Any, Callable, Iterable

import discord_output_plugin
import ddc_volume_plugin
from diagnostics import get_logger
from plugin_api import (
    PLUGIN_API_VERSION,
    PLUGIN_ID_PATTERN,
    HotkeySpec,
    PluginHostContext,
    VolumeProvider,
)
from settings import load_active_volume_provider_id, save_active_volume_provider_id
from plugin_hotkeys import PluginHotkeyController
from theme import apply_app_icon, apply_window_chrome, read_windows_theme_state


LOGGER = get_logger(__name__)
PLUGIN_SETTINGS_DIRECTORY_NAME = "plugin-settings"
USER_PLUGINS_DIRECTORY_NAME = "plugins"


@dataclass
class PluginRecord:
    key: str
    source: str
    plugin_id: str | None = None
    name: str = "Unknown plugin"
    description: str = ""
    plugin: Any | None = None
    initialized: bool = False
    status: str = "Not initialized"
    configured_hotkey: HotkeySpec | None = None
    active_hotkey: HotkeySpec | None = None
    shortcut_error: str | None = None
    is_volume_provider: bool = False
    active_volume_provider: bool = False

    @property
    def is_failure(self) -> bool:
        return self.plugin is None

    @property
    def shortcut_label(self) -> str:
        if self.active_hotkey is not None:
            return self.active_hotkey.label
        if self.configured_hotkey is not None:
            return f"{self.configured_hotkey.label} (unavailable)"
        return "Not set"

    @property
    def display_status(self) -> str:
        if self.active_volume_provider:
            return f"{self.status}; active volume provider"
        if self.shortcut_error:
            return f"{self.status}; shortcut: {self.shortcut_error}"
        return self.status


def _runtime_base_directory() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


def user_data_directory() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "windows-ddc"


def adjacent_plugins_directory() -> Path:
    return _runtime_base_directory() / USER_PLUGINS_DIRECTORY_NAME


def user_plugins_directory() -> Path:
    return user_data_directory() / USER_PLUGINS_DIRECTORY_NAME


def plugin_settings_directory() -> Path:
    return user_data_directory() / PLUGIN_SETTINGS_DIRECTORY_NAME


def _failure_record(source: str, status: str, sequence: int) -> PluginRecord:
    return PluginRecord(
        key=f"failure-{sequence}",
        source=source,
        status=status,
    )


def _validate_plugin(plugin: object) -> tuple[str, str, str]:
    plugin_id = getattr(plugin, "plugin_id", None)
    name = getattr(plugin, "name", None)
    description = getattr(plugin, "description", None)
    if not isinstance(plugin_id, str) or PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None:
        raise ValueError("Plugin ID must match [a-z][a-z0-9-]{0,63}.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Plugin name must be a non-empty string.")
    if not isinstance(description, str):
        raise ValueError("Plugin description must be a string.")
    for method_name in ("initialize", "configure", "get_hotkey", "trigger", "shutdown"):
        if not callable(getattr(plugin, method_name, None)):
            raise ValueError(f"Plugin does not implement {method_name}().")
    return plugin_id, name.strip(), description.strip()


def _is_volume_provider(plugin: object) -> bool:
    if not isinstance(getattr(plugin, "provider_name", None), str) or not getattr(plugin, "provider_name").strip():
        return False
    methods = (
        "is_volume_provider_available", "read_volume", "write_volume",
        "activate_volume_provider", "deactivate_volume_provider", "on_volume_topology_changed",
    )
    return all(callable(getattr(plugin, method, None)) for method in methods)


def _record_plugin(
    plugin: object,
    source: str,
    seen_ids: set[str],
    sequence: int,
) -> PluginRecord:
    plugin_id, name, description = _validate_plugin(plugin)
    if plugin_id in seen_ids:
        raise ValueError(f"Duplicate plugin ID {plugin_id!r}; the earlier plugin remains active.")
    seen_ids.add(plugin_id)
    return PluginRecord(
        key=f"plugin-{sequence}-{plugin_id}",
        source=source,
        plugin_id=plugin_id,
        name=name,
        description=description,
        plugin=plugin,
        is_volume_provider=_is_volume_provider(plugin),
    )


def _import_external_module(path: Path) -> ModuleType:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    module_name = f"windows_ddc_external_plugin_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not create a Python import specification.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def discover_plugins(
    external_directories: Iterable[Path] | None = None,
) -> list[PluginRecord]:
    """Import bundled then external plugins, isolating every candidate failure."""

    records: list[PluginRecord] = []
    seen_ids: set[str] = set()
    sequence = 0

    for module, source in (
        (discord_output_plugin, "Bundled Discord output plugin"),
        (ddc_volume_plugin, "Bundled DDC volume plugin"),
    ):
        try:
            if module.PLUGIN_API_VERSION != PLUGIN_API_VERSION:
                raise ValueError(f"{source} has an unsupported API version.")
            records.append(_record_plugin(module.create_plugin(), "Bundled", seen_ids, sequence))
        except Exception as exc:
            records.append(_failure_record(source, f"Load failed: {str(exc).strip() or exc.__class__.__name__}", sequence))
        sequence += 1

    directories = list(external_directories) if external_directories is not None else [
        adjacent_plugins_directory(),
        user_plugins_directory(),
    ]
    seen_directories: set[str] = set()
    for directory in directories:
        normalized_directory = os.path.normcase(str(directory.resolve()))
        if normalized_directory in seen_directories:
            continue
        seen_directories.add(normalized_directory)
        try:
            candidates = sorted(
                (
                    path
                    for path in directory.glob("*.py")
                    if not path.name.startswith("_")
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            records.append(
                _failure_record(
                    str(directory),
                    f"Discovery failed: {str(exc).strip() or exc.__class__.__name__}",
                    sequence,
                )
            )
            sequence += 1
            continue

        for candidate in candidates:
            try:
                module = _import_external_module(candidate)
                api_version = getattr(module, "PLUGIN_API_VERSION", None)
                if api_version != PLUGIN_API_VERSION:
                    raise ValueError(
                        f"Unsupported plugin API version {api_version!r}; expected {PLUGIN_API_VERSION}."
                    )
                factory = getattr(module, "create_plugin", None)
                if not callable(factory):
                    raise ValueError("External plugin must export create_plugin().")
                records.append(
                    _record_plugin(factory(), str(candidate), seen_ids, sequence)
                )
            except Exception as exc:
                records.append(
                    _failure_record(
                        str(candidate),
                        f"Load failed: {str(exc).strip() or exc.__class__.__name__}",
                        sequence,
                    )
                )
            sequence += 1
    return records


class PluginManager:
    def __init__(
        self,
        root: tk.Misc,
        post_to_ui: Callable[[Callable[[], None]], None],
        on_notice: Callable[[str], None],
        on_volume_provider_changed: Callable[[VolumeProvider | None, str | None], None] | None = None,
        get_start_with_windows: Callable[[], bool] | None = None,
        set_start_with_windows: Callable[[bool], None] | None = None,
        *,
        hotkey_factory: Callable[..., PluginHotkeyController] = PluginHotkeyController,
        external_directories: Iterable[Path] | None = None,
    ) -> None:
        self.root = root
        self._post_to_ui = post_to_ui
        self._on_notice = on_notice
        self._on_volume_provider_changed = on_volume_provider_changed or (lambda _provider, _id: None)
        self._get_start_with_windows = get_start_with_windows
        self._set_start_with_windows = set_start_with_windows
        self._hotkey_factory = hotkey_factory
        self._external_directories = external_directories
        self._records: list[PluginRecord] = []
        self._records_by_id: dict[str, PluginRecord] = {}
        self._hotkeys: PluginHotkeyController | None = None
        self._closing = threading.Event()
        self._inflight_lock = threading.Lock()
        self._inflight: dict[str, threading.Thread] = {}
        self._record_lock = threading.Lock()
        self._windows: list[tk.Misc] = []
        self._started = False
        self._active_volume_provider_id: str | None = None

    @property
    def records(self) -> tuple[PluginRecord, ...]:
        with self._record_lock:
            return tuple(self._records)

    @property
    def active_volume_provider_id(self) -> str | None:
        return self._active_volume_provider_id

    def active_volume_provider(self) -> VolumeProvider | None:
        record = self._records_by_id.get(self._active_volume_provider_id or "")
        if record is None or not record.initialized or not record.is_volume_provider:
            return None
        return record.plugin  # type: ignore[return-value]

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._records = discover_plugins(self._external_directories)
        self._records_by_id = {
            record.plugin_id: record
            for record in self._records
            if record.plugin_id is not None
        }

        try:
            self._hotkeys = self._hotkey_factory(
                on_hotkey=self._dispatch_trigger,
                on_error=self._hotkey_error_from_thread,
            )
            self._hotkeys.start()
        except Exception as exc:
            self._hotkeys = None
            LOGGER.error("Plugin hotkey controller startup failed (%s).", exc.__class__.__name__)
            self._notice(f"Plugin shortcuts are unavailable: {self._format_error(exc)}")

        for record in self._records:
            if record.plugin is None or record.plugin_id is None:
                continue
            host = PluginHostContext(
                plugin_id=record.plugin_id,
                ui_parent=self.root,
                config_path=plugin_settings_directory() / f"{record.plugin_id}.json",
                logger=get_logger(f"plugin.{record.plugin_id}"),
                post_to_ui=self._post_to_ui,
                report_status=lambda status, plugin_id=record.plugin_id: self._report_status(
                    plugin_id, status
                ),
                prepare_window=self.prepare_window,
                request_volume_refresh=self.request_volume_refresh,
            )
            try:
                record.plugin.initialize(host)
                record.initialized = True
                if record.status == "Not initialized":
                    record.status = "Ready"
                self.refresh_hotkey(record.plugin_id)
            except Exception as exc:
                record.status = f"Initialization failed: {self._format_error(exc)}"
                LOGGER.error(
                    "Plugin initialization failed for %s (%s).",
                    record.plugin_id,
                    exc.__class__.__name__,
                )
                self._notice(f"Plugin {record.name} is unavailable: {self._format_error(exc)}")

        if any(record.is_volume_provider and record.initialized for record in self._records):
            requested = load_active_volume_provider_id()
            if requested is not None:
                self.set_active_volume_provider(requested, persist=False)
            if self._active_volume_provider_id is None:
                self.set_active_volume_provider("ddc-volume", persist=True, quiet=True)

    def request_volume_refresh(self) -> None:
        self._on_volume_provider_changed(self.active_volume_provider(), self._active_volume_provider_id)

    def set_active_volume_provider(
        self, plugin_id: str, *, persist: bool = True, quiet: bool = False
    ) -> bool:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or not record.is_volume_provider or record.plugin is None:
            if not quiet:
                self._notice("That plugin is not a ready volume provider.")
            return False
        provider: VolumeProvider = record.plugin
        try:
            ready, reason = provider.is_volume_provider_available()
        except Exception as exc:
            ready, reason = False, self._format_error(exc)
        if not ready:
            if not quiet:
                self._notice(reason or "That volume provider is unavailable.")
            return False
        old = self.active_volume_provider()
        if old is not None and old is not provider:
            try:
                old.deactivate_volume_provider()
            except Exception:
                pass
        try:
            provider.activate_volume_provider()
        except Exception as exc:
            if not quiet:
                self._notice(f"Could not activate {record.name}: {self._format_error(exc)}")
            return False
        self._active_volume_provider_id = plugin_id
        for candidate in self._records:
            candidate.active_volume_provider = candidate.plugin_id == plugin_id
        if persist:
            try:
                save_active_volume_provider_id(plugin_id)
            except (OSError, ValueError) as exc:
                LOGGER.warning("Saving active volume provider failed (%s).", exc.__class__.__name__)
        self.request_volume_refresh()
        return True

    def notify_volume_topology_changed(self) -> None:
        provider = self.active_volume_provider()
        if provider is not None:
            try:
                provider.on_volume_topology_changed()
            except Exception as exc:
                self._notice(f"Active volume provider failed: {self._format_error(exc)}")

    def _notice(self, message: str) -> None:
        if self._closing.is_set():
            return
        self._post_to_ui(lambda: self._on_notice(message))

    @staticmethod
    def _format_error(exc: Exception) -> str:
        return str(exc).strip() or exc.__class__.__name__

    def _report_status(self, plugin_id: str, status: str) -> None:
        normalized = " ".join(str(status).split()) or "Unknown status"
        with self._record_lock:
            record = self._records_by_id.get(plugin_id)
            if record is not None:
                record.status = normalized
        if normalized.lower().startswith(("unavailable", "authorization failed", "setup required")):
            record_name = record.name if record is not None else plugin_id
            self._notice(f"Plugin {record_name}: {normalized}")

    def _hotkey_error_from_thread(self, exc: Exception) -> None:
        LOGGER.error("Plugin hotkey loop failed (%s).", exc.__class__.__name__)
        with self._record_lock:
            for record in self._records:
                if record.active_hotkey is not None:
                    record.active_hotkey = None
                    record.shortcut_error = "global shortcut service stopped"
        self._notice(f"Plugin shortcuts failed: {self._format_error(exc)}")

    def refresh_hotkey(self, plugin_id: str) -> None:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or record.plugin is None:
            return
        try:
            configured = record.plugin.get_hotkey()
            if configured is not None and not isinstance(configured, HotkeySpec):
                raise ValueError("get_hotkey() must return HotkeySpec or None.")
        except Exception as exc:
            record.shortcut_error = self._format_error(exc)
            return

        record.configured_hotkey = configured
        if self._hotkeys is None:
            record.active_hotkey = None
            record.shortcut_error = "global shortcut service is unavailable"
            return
        try:
            self._hotkeys.set_binding(plugin_id, configured)
        except Exception as exc:
            record.shortcut_error = self._format_error(exc)
            LOGGER.warning(
                "Plugin shortcut registration failed for %s (%s).",
                plugin_id,
                exc.__class__.__name__,
            )
            self._notice(f"Could not register {record.name} shortcut: {self._format_error(exc)}")
            return
        record.active_hotkey = configured
        record.shortcut_error = None

    def _dispatch_trigger(self, plugin_id: str) -> None:
        if self._closing.is_set():
            return
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or record.plugin is None:
            return
        with self._inflight_lock:
            existing = self._inflight.get(plugin_id)
            if existing is not None and existing.is_alive():
                return
            worker = threading.Thread(
                target=self._run_trigger,
                args=(record,),
                name=f"plugin-{plugin_id}",
                daemon=True,
            )
            self._inflight[plugin_id] = worker
            worker.start()

    def _run_trigger(self, record: PluginRecord) -> None:
        try:
            if not self._closing.is_set():
                record.plugin.trigger()
        except Exception as exc:
            LOGGER.error(
                "Plugin trigger failed for %s (%s).",
                record.plugin_id,
                exc.__class__.__name__,
            )
            if record.plugin_id is not None:
                self._report_status(
                    record.plugin_id,
                    f"Unavailable: {self._format_error(exc)}",
                )
        finally:
            if record.plugin_id is not None:
                with self._inflight_lock:
                    self._inflight.pop(record.plugin_id, None)

    def prepare_window(self, window: Any) -> None:
        apply_app_icon(window)
        theme_state = read_windows_theme_state()
        window.after_idle(lambda: apply_window_chrome(window, theme_state.dark_mode))
        self._windows.append(window)

        def forget(_event: Any = None) -> None:
            try:
                self._windows.remove(window)
            except ValueError:
                pass

        window.bind("<Destroy>", forget, add="+")

    def apply_theme(self, dark_mode: bool) -> None:
        for window in tuple(self._windows):
            try:
                if window.winfo_exists():
                    apply_window_chrome(window, dark_mode)
            except tk.TclError:
                pass

    def show_configuration(self, parent: tk.Misc | None = None) -> None:
        parent = parent or self.root
        window = tk.Toplevel(parent)
        window.title("Configure plugins")
        window.transient(parent)
        window.resizable(True, True)
        self.prepare_window(window)

        frame = ttk.Frame(window, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=(
                "Plugins run as trusted in-process Python code. New or removed "
                "plugin files are detected after windows-ddc restarts."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        tree = ttk.Treeview(
            frame,
            columns=("source", "status", "shortcut", "volume"),
            show="tree headings",
            selectmode="browse",
            height=max(5, min(12, len(self._records) + 1)),
        )
        tree.heading("#0", text="Plugin")
        tree.heading("source", text="Source")
        tree.heading("status", text="Status")
        tree.heading("shortcut", text="Active shortcut")
        tree.heading("volume", text="Volume")
        tree.column("#0", width=170, stretch=True)
        tree.column("source", width=210, stretch=True)
        tree.column("status", width=260, stretch=True)
        tree.column("shortcut", width=135, stretch=False)
        tree.column("volume", width=125, stretch=False)
        tree.grid(row=1, column=0, columnspan=3, sticky="nsew")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=3, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        def refresh_tree() -> None:
            selected = tree.selection()
            selected_key = selected[0] if selected else None
            for item in tree.get_children():
                tree.delete(item)
            for record in self.records:
                tree.insert(
                    "",
                    "end",
                    iid=record.key,
                    text=(
                        f"★ {record.name}"
                        if record.active_volume_provider
                        else record.name
                    ),
                    values=(record.source, record.display_status, record.shortcut_label, "Active" if record.active_volume_provider else ("Available" if record.is_volume_provider and record.initialized else "—")),
                )
            if selected_key and tree.exists(selected_key):
                tree.selection_set(selected_key)
            elif tree.get_children():
                tree.selection_set(tree.get_children()[0])
            update_buttons()

        def selected_record() -> PluginRecord | None:
            selection = tree.selection()
            if not selection:
                return None
            return next((record for record in self.records if record.key == selection[0]), None)

        def update_buttons(_event: Any = None) -> None:
            record = selected_record()
            if record is not None and record.plugin is not None and record.initialized:
                configure_button.state(["!disabled"])
            else:
                configure_button.state(["disabled"])
            if record is not None and record.is_volume_provider and record.initialized:
                use_volume_button.state(["!disabled"])
            else:
                use_volume_button.state(["disabled"])

        def configure_selected() -> None:
            record = selected_record()
            if record is None or record.plugin is None or not record.initialized:
                return
            try:
                record.plugin.configure(window)
                if record.plugin_id is not None:
                    self.refresh_hotkey(record.plugin_id)
            except Exception as exc:
                record.status = f"Configuration failed: {self._format_error(exc)}"
                LOGGER.error(
                    "Plugin configuration failed for %s (%s).",
                    record.plugin_id,
                    exc.__class__.__name__,
                )
            refresh_tree()

        def open_user_folder() -> None:
            directory = user_plugins_directory()
            try:
                directory.mkdir(parents=True, exist_ok=True)
                os.startfile(directory)  # type: ignore[attr-defined]
            except OSError as exc:
                self._notice(f"Could not open the plugin folder: {self._format_error(exc)}")

        def use_selected_volume_provider() -> None:
            record = selected_record()
            if record is not None and record.plugin_id is not None:
                self.set_active_volume_provider(record.plugin_id)
            refresh_tree()

        start_with_windows_var: tk.BooleanVar | None = None
        if self._get_start_with_windows is not None and self._set_start_with_windows is not None:
            start_with_windows_var = tk.BooleanVar(value=bool(self._get_start_with_windows()))

            def toggle_start_with_windows() -> None:
                assert start_with_windows_var is not None
                try:
                    self._set_start_with_windows(bool(start_with_windows_var.get()))
                    start_with_windows_var.set(bool(self._get_start_with_windows()))
                except Exception:
                    start_with_windows_var.set(not bool(start_with_windows_var.get()))

            ttk.Checkbutton(
                frame,
                text="Start with Windows",
                variable=start_with_windows_var,
                command=toggle_start_with_windows,
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        configure_button = ttk.Button(
            frame,
            text="Configure",
            underline=0,
            command=configure_selected,
        )
        configure_button.grid(row=3, column=0, sticky="w", pady=(10, 0))
        use_volume_button = ttk.Button(
            frame,
            text="Use for volume",
            command=use_selected_volume_provider,
        )
        use_volume_button.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        open_button = ttk.Button(
            frame,
            text="Open plugins folder",
            underline=0,
            command=open_user_folder,
        )
        open_button.grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(10, 0))
        close_button = ttk.Button(frame, text="Close", command=window.destroy)
        close_button.grid(row=3, column=3, sticky="e", pady=(10, 0))

        tree.bind("<<TreeviewSelect>>", update_buttons)
        tree.bind("<Double-1>", lambda _event: configure_selected())
        tree.bind("<Return>", lambda _event: configure_selected())
        window.bind("<Alt-c>", lambda _event: configure_selected())
        window.bind("<Alt-o>", lambda _event: open_user_folder())
        window.bind("<Escape>", lambda _event: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        refresh_tree()
        window.update_idletasks()
        window.minsize(max(760, window.winfo_reqwidth()), max(330, window.winfo_reqheight()))
        window.grab_set()
        tree.focus_set()
        window.wait_window()

    def stop(self, timeout: float = 2.0) -> bool:
        if timeout < 0:
            raise ValueError("Plugin shutdown timeout cannot be negative.")
        self._closing.set()
        deadline = time.monotonic() + timeout
        stopped = True
        if self._hotkeys is not None:
            try:
                stopped = self._hotkeys.stop(max(0.0, deadline - time.monotonic())) and stopped
            except Exception:
                stopped = False
            self._hotkeys = None

        for record in self._records:
            if not record.initialized or record.plugin is None:
                continue
            result: list[bool] = []

            def shut_down_plugin(plugin: Any = record.plugin) -> None:
                try:
                    result.append(bool(plugin.shutdown(max(0.0, deadline - time.monotonic()))))
                except Exception:
                    result.append(False)

            shutdown_worker = threading.Thread(
                target=shut_down_plugin,
                name=f"plugin-shutdown-{record.plugin_id}",
                daemon=True,
            )
            shutdown_worker.start()
            shutdown_worker.join(max(0.0, deadline - time.monotonic()))
            stopped = (not shutdown_worker.is_alive() and result == [True]) and stopped

        with self._inflight_lock:
            workers = tuple(self._inflight.values())
        for worker in workers:
            if worker.is_alive():
                worker.join(max(0.0, deadline - time.monotonic()))
            stopped = not worker.is_alive() and stopped
        return stopped
