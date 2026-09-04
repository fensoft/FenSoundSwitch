# AGENTS.md

These instructions apply to the entire repository. Keep this file operational and repository-specific; user setup belongs in `README.md`, and implementation detail belongs in `docs/ARCHITECTURE.md`.

## Project Snapshot

- FenSoundSwitch is a Windows-only Python 3.10+ Tkinter application for one selected monitor's DDC/CI audio volume.
- The supported composition root is `app.py`. `main.py` is deliberately an exit-1 rejection stub that directs users to `app.py` without launching it.
- Runtime dependencies: `monitorcontrol==4.2.0`, `paho-mqtt==2.1.0`, `pywebview==6.1`, and `pythonnet==3.0.5`. Optional executable builder: `Nuitka==4.2`.
- The app is an interactive current-user process, not a service. It has no HTTP/API server, listening port, database, application account system, external broker/job queue, cron, or telemetry. The bundled Discord plugin is an OAuth client for Discord's HTTPS token endpoint and local named pipe.
- The global Volume Down/Up hook, passive plugin shortcut observer, Discord voice-output changes, physical DDC volume/input writes, audio endpoint visibility changes, and elevated FenSound rename are safety-sensitive. Do not launch the app or call live monitor/audio/Discord operations as routine automated validation.
- There is a standard-library unit-test suite for fail-safe and registered hotkeys, plugin discovery/isolation, mocked Discord credentials/OAuth/restoration, stable identity/settings, Change speed persistence, autostart, diagnostics, display invalidation, revalidation, fail-closed audio-output matching, single-instance behavior, overlay focus safety, live theme/accessibility/scaling, resilience, CI safety, and tray recovery. GitHub Actions runs hardware-free checks on Windows for Python 3.10. There is no lint, format, type-check, or third-party test-framework configuration. State those limitations accurately.

## Runtime Shape

1. `app.py` handles only its strictly validated internal elevated audio-rename request before the normal composition boundary. An ordinary launch acquires the session-local `SingleInstanceGuard` before creating Tk; a duplicate broadcasts restore and exits before application initialization.
2. The primary configures the rotating diagnostic log, creates one withdrawn `tk.Tk`, constructs `gui.MonitorVolumeApp`, reads the current-user autostart state, and enters Tk's coordination loop while retaining the mutex handle. A strict internal pywebview child renders packaged local HTML in WebView2 and communicates through authenticated Windows named-pipe messages; it never owns hardware or authoritative state and opens no HTTP/TCP listener.
3. After Tk exists, `gui.py` dynamically imports the plugin manager, directly imports bundled modules from the `plugins` package, then discovers trusted adjacent `external-plugins\*.py` and per-user `plugins\*.py` modules. The bundled package is never dynamically scanned. It initializes plugins and starts the shared `plugin-hotkey-loop`/`plugin-hotkey-dispatch`. Duplicate processes exit before that import, discovery, or credential access.
4. `display-change-listener`, `tray-icon`, and `volume-key-hook` are long-lived daemon threads with native Win32 message loops. The display listener also relays live theme/system-color broadcasts into Tk. The plugin-hotkey observer is independent of the low-level Volume hook: action shortcuts and keyboard routes may explicitly consume only their configured held key pairs.
5. `ddc-gui-worker`, `ddc-volume-write`, and `ddc-input-discovery` are daemon workers for blocking DDC/CI work. `audio-output-sync`, `plugin-<id>`, `plugin-signal-<id>`, and `discord-output-auth` are separate short-lived daemons for audio reconciliation, direct plugin triggers, ordered action signals, and Discord OAuth/RPC respectively.
6. Worker and native-thread callbacks cross into Tk through `queue.Queue`; `_poll_queues()` drains them every 50 ms. Plugin status uses the same `_post_to_ui()` boundary.
7. Only one application-issued DDC operation should be active. Rapid writes are serialized and reduced to the latest `_pending_target_volume`. A timed-out operation keeps the slot until its worker returns; its late result is ignored and followed by read-only rediscovery.
8. A successful exact-identity volume read enables monitor control only while the display-change listener remains live and the topology generation is valid. If the native keyboard hook is also live, Volume Down/Up events are consumed instead of reaching Windows system audio.
9. Confirmed tray-icon addition makes startup tray-first unless `--foreground` immediately invokes the existing restore path. Tk publishes immutable active-monitor, confirmed-volume, routing, selectable-monitor, and action-signal snapshots; tray actions queue back into Tk. Addition or recovery failure keeps/restores the main window; Explorer recreation re-adds a previously visible icon. Closing the restored window exits, while minimizing returns it to the tray after another confirmed add.
10. A successful exact selected-monitor read schedules audio reconciliation. Exact unique container IDs are preferred; only one remaining same-adapter endpoint with a missing/placeholder container may be inferred. The selected endpoint is made visible before positively matched other-screen endpoints are hidden. Ambiguous selected matching changes nothing. A missing FenSound name launches the fixed-purpose elevated helper at most once per endpoint per primary session.
11. The Discord plugin declares a `switch-output` signal slot, not a direct action or shortcut. A signal run captures the current concrete output, switches to the first different concrete device for one second, and restores in `finally`; overlap is ignored. Shutdown stops signal input first and signals restoration within one shared two-second plugin budget.

Always preserve Tk's thread affinity. Never call Tk methods from tray, hook, or DDC worker threads; enqueue a callback with `_post_to_ui()`.

## Important Files

| File | Responsibility |
| --- | --- |
| `app.py` | Supported process entrypoint, single-instance boundary, and Tk composition root. |
| `app_version.py` | Loads the build-injected version resource and defaults source/local execution to `dev`. |
| `audio_outputs.py` | Fail-closed monitor/render-endpoint matching, endpoint visibility policy, and fixed FenSound elevated rename helper. |
| `core_audio.py` | Focused Core Audio render/capture endpoint enumeration and master-volume adapter for soundcard/capture-gain route workers, explicit default-role changes, and silent WASAPI keep-alive streams. |
| `autostart.py` | Current-user Run-key state and quoted source/packaged launch commands. |
| `diagnostics.py` | Nonfatal per-user rotating-log configuration and component logger access. |
| `main.py` | Unsupported launcher stub; prints migration guidance and returns `1`. |
| `gui.py` | UI state machine, selection, readiness, queues, worker serialization, live theme/DPI reflow, keyboard navigation, overlay targeting, tray snapshots/actions, and window lifecycle. |
| `ddc.py` | Monitor identity, enumeration, clamping, and DDC read/write wrappers used by the bundled DDC volume provider. |
| `plugins/ddc_volume_plugin.py` | Bundled DDC volume provider, including plugin-owned monitor selection and active-only audio-output policy. |
| `plugins/ddc_input_source_plugin.py` | Bundled stable-identity DDC monitor-input automation action with background capability discovery and verified writes. |
| `plugins/denon_marantz_volume_plugin.py` | Bundled Denon/Marantz AVR main-zone provider using bounded outbound LAN TCP. |
| `plugins/discord_output_plugin.py` | Discord local RPC, OAuth/token refresh, Credential Manager storage/migration, plugin UI, and temporary output restoration. |
| `plugins/audio_keepalive_plugin.py` | Configurable silent WASAPI playback to the current Windows default playback and/or voice endpoint. |
| `plugins/onkyo_volume_plugin.py` | Bundled configurable Onkyo/Integra eISCP main-zone volume provider using bounded outbound LAN TCP. |
| `plugins/pioneer_elite_volume_plugin.py` | Bundled Pioneer/Elite main-zone provider using bounded outbound LAN TCP. |
| `plugins/sony_volume_plugin.py` | Bundled Sony network AVR main-zone provider using bounded outbound HTTP. |
| `plugins/windows_microphone_gain_plugin.py` | Bundled Windows capture endpoint gain provider. |
| `plugins/mqtt_input_plugin.py` | Bundled reusable MQTT/HA profiles, route volume input, and Home Assistant automation-button trigger clients. |
| `plugins/yamaha_volume_plugin.py` | Bundled Yamaha main-zone provider using bounded outbound LAN TCP. |
| `settings.py` | Per-user selected-monitor and Change speed JSON load/save. |
| `plugin_api.py` | Runtime plugin API version, `HotkeySpec`, optional input/provider capabilities, protocol, and host context. |
| `plugin_hotkeys.py` | Shared native keyboard observer, off-thread dispatch, and explicit per-route key consumption. |
| `plugin_manager.py` | Trusted plugin discovery, isolation/status, configuration UI, trigger suppression, and shutdown. |
| `plugins/windows11_overlay_plugin.py` | Bundled Windows 11 work-area/DPI-aware, live-themed, no-activate, topmost, auto-hiding overlay renderer. |
| `plugins/macos_overlay_plugin.py` | Bundled macOS-style no-activate overlay renderer sharing the safe Windows presentation path. |
| `theme.py` | Windows theme/High Contrast/window-DPI reads, reversible ttk styles, DWM chrome, and runtime icon path. |
| `web_presentation.py` | Primary-owned authenticated named-pipe protocol, WebView child lifecycle, state revisions, restore/minimize handshakes, and crash recovery. |
| `web_ui_host.py`, `web/` | Strict internal pywebview child and packaged local HTML/CSS/JS presentation. |
| `windows_platform.py` | Win32 ctypes ABI, single-instance mutex/restore signaling, monitor identity/EDID inventory, display work areas/scaling, window DPI/High Contrast reads, no-activate overlay helpers, display/theme notifications, snapshot-driven tray controller, global keyboard hook, and DWM helpers. |
| `tests/` | Hardware-free plugin/hotkey/Discord, identity, settings, autostart, audio-output, single-instance, topology, fresh-write, overlay, accessibility/scaling, resilience, and tray regressions. |
| `.github/workflows/ci.yml` | Windows Python 3.10 hardware-free unit and low-risk validation workflow. |
| `pyproject.toml` | Python requirement, dependency pins, and installed flat modules. |
| `build_exe.ps1` | Standalone Nuitka build normalized to `dist\FenSoundSwitch\`. |
| `build_installer.ps1`, `.config/dotnet-tools.json`, `installer/FenSoundSwitch.wxs` | Pinned WiX 6 native MSI packaging, per-machine install, Start Menu shortcut, and major-upgrade metadata. |
| `FenSoundSwitch.ico` | Tracked executable, window, and tray icon source. |
| `docs/app.png`, `docs/overlay.png` | Manually maintained screenshots; there is no generation pipeline. |

Adding, renaming, or removing a distributable runtime module must keep `[tool.setuptools].py-modules` in `pyproject.toml` synchronized. Do not add `main.py` there or turn it back into a launcher without an explicit compatibility decision.

Changes to the icon name or location must update `theme.APP_ICON_PATH`, `--windows-icon-from-ico`, and `--include-data-files` in `build_exe.ps1`, plus the MSI `Icon` source. The runtime icon must remain included as data even though it is also embedded as the executable and installer icon.

## Persistent or Sensitive Data

- Live settings normally reside at `%APPDATA%\fensoundswitch\settings.json`; if `APPDATA` is empty or unset, the fallback is `<home>\fensoundswitch\settings.json`. When that file is absent, runtime validates and copies legacy `%APPDATA%\windows-ddc\settings.json` without modifying it.
- New settings use schema version 10 and persist ordered independent route records (`route_id`, user-defined `name`, input endpoint, output endpoint) plus ordered action signals. Each signal has app-start, keyboard, tray, and/or plugin trigger records and one to 32 synchronous plugin-action or bounded wait slots. MQTT trigger records reference a plugin-owned named profile and keep their own Home Assistant name/ID. Schema-v6 routes receive deterministic endpoint-ID names when next saved. Overlay selection and renderer settings live under `plugin-settings`; legacy global `overlay_mode` migrates to the Windows 11 renderer. Legacy Change speed/active-provider fields remain where present, along with description, a Windows device path, and optional EDID manufacturer/product/serial identity. Never persist the transient display index.
- Main-window Export archives `settings.json` and flat `plugin-settings\*.json` files to `%APPDATA%\fensoundswitch\configurations\*.fsc`. MQTT profile credentials are included, so exported archives must be protected. On first primary launch, bundled `default.py` creates `default.fsc` in that directory without overwriting an existing file. Import replaces those saved files after validating all archive JSON objects and restarts the app to apply them. The Import down-arrow lists the five newest non-default archives and requires a selected item plus restart confirmation; Default reads the generated `default.fsc`. Archives exclude Credential Manager OAuth data, executable plugins, logs, audio-endpoint state, and hardware state.
- Unique EDID serial identity is preferred. Duplicate serials require the saved device path; monitors without a usable serial use the device path. Missing or ambiguous matches fail closed.
- Saving either monitor selection or Change speed writes `settings.tmp` and then replaces `settings.json` while preserving the other valid setting. There is backward-compatible loading/promotion for unambiguous legacy description/ordinal files and no file lock; the session mutex prevents ordinary same-session project instances from racing but does not coordinate external tools or separate sessions.
- Missing, unreadable, invalid-JSON, non-object, unknown-version, and invalid nested monitor settings are treated as absent. JSON booleans are rejected as legacy ordinals. Missing or invalid Change speed defaults to `slow`; valid persisted values are `slow`, `medium`, and `fast`.
- Do not read, overwrite, delete, or reset a user's live `settings.json` or leftover `settings.tmp` during automated work. Patch `settings.SETTINGS_PATH` to a unique temporary path before calling load/save functions.
- Live diagnostics normally reside at `%LOCALAPPDATA%\fensoundswitch\fensoundswitch.log`, fall back to `APPDATA` and then home, and retain two 512 KiB backups. Do not read, overwrite, delete, or reset them during automated work; pass a unique temporary path to `configure_logging()` in tests.
- Start with Windows is represented by `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FenSoundSwitch`; a legacy `windows-ddc` value is read for compatibility and removed only on an explicit GUI checkbox mutation. Never read, create, change, or delete live values during automated work; mock `autostart.winreg`.
- Windows persists audio render-endpoint visibility and the selected endpoint's FenSound description outside the app settings. The normal process reads HKLM endpoint/monitor metadata and changes visibility through private Windows audio policy; the administrator-approved helper writes the fixed alias through Core Audio. Never enumerate, rename, hide, show, or otherwise mutate live endpoints during automated work. Mock `audio_outputs.winreg`, `_set_endpoint_visibility`, and `request_elevated_endpoint_rename`.
- The physical monitor volume is external mutable state. A set can succeed even if the following readback fails, and shutdown does not restore the old value.
- The Windows soundcard route adapter may enumerate or change only its explicitly selected render endpoint from route/configuration workers. Never invoke it in automated tests without mocks; it must not alter endpoint visibility, names, or defaults.
- Plugin JSON lives under `%APPDATA%\fensoundswitch\plugin-settings`; the MQTT plugin's named profiles include optional broker credentials, while Discord OAuth secrets remain Credential Manager-only. Valid legacy JSON is copied without changing `%APPDATA%\windows-ddc`. External executable code is discovered from adjacent `external-plugins\`, `%APPDATA%\fensoundswitch\plugins`, then the legacy trusted `%APPDATA%\windows-ddc\plugins` fallback. Patch these paths to temporary directories in tests and never import untrusted fixtures from a live user directory.
- Discord client configuration, client secret, access token, and refresh token live only in current-user Windows Credential Manager target `fensoundswitch/plugins/discord-output/oauth-rpc`; valid legacy `windows-ddc` and prototype targets are copied but never deleted. Never read, write, delete, or reset live credentials in automated work. Mock credential, browser, pipe, and HTTPS functions. Never include secrets/tokens in source, screenshots, fixtures, logs, or documentation.

There is no database and no migration command. If the settings schema changes, implement backward-compatible loading or an explicit migration, update README and architecture examples, and test old, missing, malformed, and new formats.

## CLI and Operational Commands

| Command | Use and side effects |
| --- | --- |
| `python -m pip install -e .` | Installs the pinned runtime dependency, can contact package indexes, modifies the active Python environment, and creates ignored egg-info. |
| `python app.py` | The primary reads settings/Run/credentials, auto-imports trusted plugins, can open the Discord Developer Portal/consent, starts native threads and global hooks, contacts Discord/monitor hardware, and can reconcile Windows audio endpoints. A duplicate only broadcasts restore and exits. Run primary startup only with explicit authorization for interactive/manual testing. |
| `python app.py --foreground` | Performs the same primary startup but immediately opens the command center through the acknowledged tray restore path. It has the same live side effects and manual-testing restriction as `python app.py`. |
| `python main.py` | Intentionally prints the unsupported-launcher message and exits `1`; do not treat the nonzero result as a regression. |
| `python -m pip install -e .[build]` | Also installs pinned Nuitka tooling and may contact package indexes. |
| `.\build_exe.ps1` | Defaults to version `dev` and creates the standalone application directory. May download Nuitka support/toolchain components and replaces ignored output under `dist\`. Run only when a standalone build is requested. |
| `.\build_installer.ps1` | Runs the standalone build, then creates `dist\FenSoundSwitch.msi` with WiX. Tag CI passes `-Version <tag>`. It may download build dependencies and replaces ignored output. Run only when an installer build is requested. |

Do not call `enumerate_monitors()`, DDC reads/writes, live audio enumeration/reconciliation/visibility, Discord credential/RPC/OAuth functions, the packaged executable, `monitorcontrol`, `python -m monitorcontrol`, or `start()` on native controllers as generic smoke tests. They cross hardware, credential, network, or user-session boundaries; mock them.

There is no deploy, signing, or local publish command. The Release workflow publishes the MSI only for pushed tags. Do not push, tag, upload a release, or publish a package without explicit user authorization.

CI is validation-only. Keep it free of `python app.py`, live controller `start()` calls, Discord credential/browser/pipe/network access, monitor/audio enumeration or mutation, build-script execution, artifacts, signing, publishing, and deployment.

## API Shape

`FenSoundSwitch` defines no external API or installed project CLI command. Its dependency's `monitorcontrol` command is an upstream hardware-management interface, not a supported project entrypoint. The important internal boundaries are:

- `ddc.enumerate_monitors() -> list[MonitorRef]`
- `ddc.match_selected_monitor(monitors, selected_key) -> SelectionMatch`
- `ddc.read_monitor_volume(monitor_ref) -> int`
- `ddc.set_monitor_volume(monitor_ref, target_volume) -> int`
- `ddc.change_monitor_volume(monitor_ref, delta) -> int` (currently unused by the GUI)
- `ddc.enumerate_monitor_inputs(monitor_ref) -> tuple[MonitorInput, ...]`
- `ddc.set_monitor_input(monitor_ref, target) -> int`
- `audio_outputs.match_monitor_audio_endpoints(...)` and `build_audio_output_plan(...)`
- `audio_outputs.reconcile_monitor_audio_outputs(...) -> AudioOutputResult`
- `audio_outputs.parse_internal_rename_request(...)` and `run_internal_rename_helper(...)`
- `plugin_manager.discover_plugins(...)`, `PluginManager.start()`, declarative plugin UI API-v4 dispatch, `refresh_hotkey()`, and `stop()`
- `plugin_hotkeys.PluginHotkeyController` and `plugin_api.HotkeySpec`
- `plugins.discord_output_plugin.DiscordOutputPlugin` plus its mocked RPC/OAuth/credential helpers
- `settings.load_selected_monitor_key()` and `settings.save_selected_monitor_key()`
- `settings.load_change_speed()` and `settings.save_change_speed()`
- `diagnostics.configure_logging()`, `get_logger()`, and `close_logging()`
- `autostart.is_start_with_windows_enabled()` and `set_start_with_windows()`
- `windows_platform.SingleInstanceGuard` and `request_existing_instance_restore()`
- `windows_platform.DisplayChangeListener`, `TrayIconController`, `TrayMenuState`, and `GlobalVolumeKeyListener`

Keep each per-monitor DDC get/set sequence inside `with monitor_ref.monitor:`. Enumeration remains through `get_monitors()`, and description lookup remains on the wrapper. Preserve `0`–`100` application clamping and treat the post-write readback as authoritative when it succeeds.

## Tests Before Commit

Run these low-risk validation checks from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py app_version.py audio_outputs.py autostart.py core_audio.py ddc.py diagnostics.py gui.py main.py plugin_api.py plugin_hotkeys.py plugin_manager.py settings.py theme.py web_presentation.py web_ui_host.py windows_platform.py plugins
python -m pip check
git diff --check
git diff --cached --check
git status --short
```

Parse both build scripts without executing them:

```powershell
foreach ($script in @(".\build_exe.ps1", ".\build_installer.ps1")) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $script),
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -ne 0) { $parseErrors; exit 1 }
}
```

For settings changes, add isolated tests around a temporary `SETTINGS_PATH`. For autostart changes, mock `autostart.winreg`; never use the live Run value. For audio-output changes, mock registry, COM visibility, and ShellExecute/elevation boundaries. For pure helper changes, prefer tests that use fake monitor/endpoint objects and do not import or exercise real hardware unnecessarily.

For plugin tests, pass temporary discovery/config directories and never import files from live adjacent or per-user plugin folders. Mock `PluginHotkeyController.start()` unless the test exercises its pure registration state with mocked User32. Discord tests must mock Credential Manager, `webbrowser`, named-pipe clients, sleeps/waits, and `urllib`; never read or reset the live OAuth grant.

GUI, tray, hook, theme, or DDC changes require an authorized Windows/manual pass with a compatible monitor. Verify:

- startup and tray-first behavior;
- duplicate launch exits without a second app and restores the existing tray window;
- discovery, duplicate descriptions, fallback, and saved selection;
- successful and failed volume reads;
- slider/button/key writes and readback;
- Slow/Medium/Fast Change speed behavior for buttons and keys, including restart persistence;
- Start with Windows enable/disable, checkbox persistence, source command, packaged command after an authorized build, and moved-target behavior;
- first Discord setup asks for the reset secret before Application ID; exact redirect and restricted-scope guidance; first consent; silent restart reuse/refresh; reset/revocation/missing Discord behavior;
- Plugins action-only contents and access key; Routes provider/input assignment, overlay, and autostart controls; shortcut capture/clear/conflict/live rebind/no-repeat; one-second switch/exact restore; repeat suppression; both external plugin folders and restart-only loading;
- key pass-through before readiness and after exit;
- rapid-write coalescing and `0`/`100` boundaries;
- overlay visibility, auto-hide, cursor-screen/selected-display-fallback placement, mixed-DPI work areas, and focus preservation;
- live light/dark/High Contrast transitions, `Tab`/`Shift+Tab` traversal, access keys, descriptive button names, slider boundary/page keys, and control-window mixed-DPI reflow;
- minimize, restore, Refresh, and clean shutdown;
- confirmed icon addition, failed-add fallback, and Explorer `TaskbarCreated` recovery;
- rich tray active-monitor/confirmed-volume/routing state, Refresh, stable-identity switching, Restore, and Exit;
- exact, one-placeholder inference, and ambiguous audio matching; selected-output re-enable before other-screen hiding; unrelated headphones/speakers untouched; FenSound elevation approval/cancellation and restart retry; selection switching and persistence after exit;
- disconnect/stale-handle behavior without leaving keys unexpectedly consumed.
- shutdown during a Discord switch restores before pipe closure when possible and reports a bounded plugin stop failure otherwise.

Manual DDC tests can be audible and mutate monitor state. Record what was actually exercised; do not imply hardware or OS coverage that was not run.

## Gotchas and Things To Preserve

- Do not perform blocking DDC work on the Tk thread.
- Do not mutate Tk state directly from another thread. Keep callbacks small and exception-safe so `_poll_queues()` continues rescheduling itself.
- Keep all plugin imports/discovery and Credential Manager reads after the primary-instance/Tk boundary; duplicate launches must never execute external plugin code or touch Discord credentials.
- Treat external plugins as trusted unsandboxed current-user code, auto-load them only from adjacent `external-plugins\` and per-user `plugins\` after direct bundled-package imports, reject later duplicate IDs, and isolate each load/initialize/trigger failure from monitor control.
- Keep plugin shortcut observation separate from `GlobalVolumeKeyListener`: action shortcuts, action signals, and keyboard routes may consume only their configured non-modifier key down/repeat and matching held key up when their persisted `forward_keys` setting is false; modifier and unrelated keys always forward. Existing action shortcuts migrate to forwarding. Queue dispatch away from the native thread, execute signal slots synchronously only on their manager worker, make waits shutdown-interruptible, and suppress held-key repeats and overlapping triggers.
- Never call Tk from Discord/auth/trigger threads. Preserve Credential Manager-only secrets, explicit first consent, silent reuse/refresh, first non-current concrete output selection, restoration in `finally`, and pipe close only as restoration fallback.
- Keep `audio-output-sync` off Tk and coalesced independently of the serialized DDC slot. Recheck topology before mutations, make the selected output visible first, hide only endpoints positively mapped to other current monitors, and never infer a conflicting/non-placeholder container.
- Keep the internal rename mode strictly before mutex/logging/Tk initialization, validate the endpoint ID on both sides of elevation, accept only the fixed FenSound alias, and latch every requested/failed/cancelled endpoint for the primary session so UAC cannot loop.
- Route theme/system-color messages from the native listener through `_post_to_ui()`. Preserve the debounce, reversible light/dark palettes and chrome, Windows system colors in High Contrast, and per-window DPI reflow.
- Preserve `_volume_write_inflight` / `_pending_target_volume` serialization. Do not introduce concurrent operations against the selected monitor wrapper.
- Do not broaden key consumption beyond `_hotkeys_enabled`. Readiness also requires a live display listener and valid topology event. Display notification, Refresh start, selection clearing, listener error, write failure, and shutdown clear it. Preserve the per-press consume/pass-through decision through matching key-up and the one-notice unavailable latch. Test hook/display-listener failures, reads, writes, Refresh, disconnect, shutdown, and held keys across transitions.
- Keep ctypes callback objects (`_wndproc` and `_hook_callback`) strongly referenced for their controllers' lifetimes. Incorrect ctypes signatures or callback lifetimes can crash the process.
- Native display, hook, tray, and plugin-hotkey startup waits are bounded. Plugin hotkey removal and resource shutdown share a two-second budget; stop plugin shortcuts before all other native controllers and retain shutdown diagnostics. DDC and audio-output workers are daemon threads and are not joined.
- Do not assume a DDC error means no hardware change occurred. Set plus readback is not transactional.
- Do not treat a topology generation check as cancellation. A native DDC call already in progress can still mutate hardware; stale completions must remain non-authoritative and trigger read-only rediscovery.
- Preserve the DDC watchdog token until a timed-out worker actually returns. Do not start a concurrent replacement call, accept its late result, or automatically retry an uncertain write.
- Do not assume a listed monitor supports volume. Enumeration precedes the selected monitor's volume read.
- Display/device notifications trigger debounced discovery with bounded retries; every actual write also reacquires monitor wrappers. Do not claim that external OSD/program volume changes are polled.
- Preserve the acknowledged tray-show handshake: never withdraw Tk until the tray thread confirms `Shell_NotifyIconW` success. Tray errors must keep or restore the main window, and a `TaskbarCreated` broadcast must re-add an icon that was intended to be visible.
- Preserve immutable lock-protected tray snapshots and dispatch monitor command IDs against the snapshot that built the open menu. Never call Tk from the tray thread; Refresh and stable-identity monitor switching must enqueue through `_post_to_ui()`.
- Preserve cursor-screen-first overlay placement with selected-display fallback, current `rcWork`/scale lookup, negative-coordinate handling, and clamping. Never show the overlay unless `WS_EX_NOACTIVATE` is applied, and keep native presentation on `SWP_NOACTIVATE` without Tk focus/lift calls.
- Preserve the session-local named mutex before Tk initialization and retain its handle through main-loop exit. Duplicate launch must not read settings/credentials, import/discover plugins, or initialize hooks, tray state, or workers; its restore broadcast is best-effort.
- Configure logging only after acquiring the session mutex so duplicate processes never contend for rotation. Logging setup must remain nonfatal, bounded, and free of deliberate monitor identity or secret fields.
- Preserve autostart as an explicit current-user checkbox. Keep registry failures nonfatal, quote source/executable paths, reject commands beyond 260 characters, prefer `pythonw.exe` for source, and use `sys.argv[0]` for installed standalone builds.
- Do not bypass the guard to run multiple instances during testing. Separate sessions and external tools can still conflict over hardware or `settings.tmp` because there is no file or hardware lock.
- Keep CI on Windows and hardware-free. Changes to its Python matrix, commands, permissions, or action versions must update `tests/test_ci_workflow.py`, README, and architecture documentation together.
- Do not hand-edit or commit `dist/`, `fensoundswitch.egg-info/`, or `__pycache__/`. The present egg-info is ignored generated residue and can be stale; `pyproject.toml` and tracked sources are authoritative.
- Review `docs/app.png` and `docs/overlay.png` when visible UI changes. Update them only with real application captures and scrub machine-specific or sensitive content.
- Keep `README.md`, `CHANGELOG.md`, this file, and `docs/ARCHITECTURE.md` consistent when commands, dependencies, entrypoints, paths, settings, release behavior, or architectural boundaries change.
- The local clone can lack the remote lightweight `0.1.0` tag. For release-history work, inspect local refs and the live origin before concluding that no tags exist.
