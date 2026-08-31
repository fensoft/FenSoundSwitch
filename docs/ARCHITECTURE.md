# Architecture

This document describes the tagged `0.1.0` release and the current repository sources. `FenSoundSwitch` is a small Windows desktop process: Tk owns the UI, native Win32 message loops provide display-change protection, the tray icon, and the global volume-key hook, and short-lived workers perform DDC/CI operations against physical monitors.

There is no server-side tier. The project has no HTTP API, listening port, database, container, service, application account system, embedded broker, cron process, or telemetry client. The bundled Discord plugin is an OAuth client that can use Discord's local named pipe and HTTPS token endpoint; the optional MQTT input is a client of a user-configured broker.

## High-level flow

1. The supported entrypoint, `app.py`, first recognizes the strictly validated internal elevated audio-rename request. Ordinary launches acquire a session-local named mutex; a duplicate broadcasts a restore request and exits before creating Tk or application subsystems.
2. The primary instance configures a rotating per-user diagnostic log, creates one `tk.Tk`, constructs `gui.MonitorVolumeApp`, and enters `root.mainloop()` while retaining the mutex handle.
3. `MonitorVolumeApp.__init__()` samples the Windows app-theme and High Contrast state, reads the optional current-user autostart value, and loads saved monitor selection and route settings from `settings.py`.
4. It builds the keyboard-navigable, DPI-scaled control window and status bar on the Tk thread. A persistent Fluent-style sidebar maps only one of four gridded pages at a time: Routes, Actions, Appearance, or Settings. Routes and plugins use composed focusable cards rather than tables; the hidden monitor combobox remains a nonvisual stable-identity model for tray monitor commands.
5. It dynamically imports `plugin_manager` only after the mutex/Tk boundary, directly imports first-party modules from the bundled `plugins` package, then loads adjacent `external-plugins\*.py` and per-user Python files in isolation, and starts the shared passive shortcut observer. After plugin initialization, the selected renderer-only bundled overlay creates the hidden, native-no-activate overlay on the Tk thread. The bundled package is never dynamically scanned. The bundled Discord plugin opens its app-owned setup modal only when Credential Manager has no valid configuration, then validates or creates its grant on a worker.
6. It starts a dedicated `DisplayChangeListener` hidden window for topology and live theme/system-color broadcasts. Failure leaves all monitor-volume writes disabled; the app can still enumerate and display status.
7. It independently starts the tray controller and low-level global Volume Up/Down hook. Tk publishes immutable menu snapshots to the tray, while tray Refresh/selection/restore/exit actions enqueue Tk callbacks. Their failures remain nonfatal to the other subsystems.
8. It schedules the recurring Tk queue poll and a one-shot initial monitor refresh after 50 ms.
9. The refresh worker enumerates DDC wrappers and Windows monitor identities, exact-matches the saved target, and reads its volume. With no saved selection, automatic selection occurs only when exactly one verifiable monitor exists.
10. The worker enqueues a tokened completion callback. The Tk queue poll applies the monitor list, selection, displayed volume, readiness, and status text.
11. After a successful exact selected-monitor read, a separate coalesced worker matches Windows render endpoints to the current monitor paths, makes the selected endpoint visible, hides positively matched other-screen endpoints, and requests the fixed FenSound rename when needed.
12. A volume request updates the UI optimistically, resolves the cursor's Windows display work area for the overlay with selected-display fallback, then a serialized worker reacquires all wrappers and exact-matches the identity before set/readback.
13. The readback becomes authoritative. A coalesced follow-up starts another worker and therefore performs another fresh discovery/match.
14. Display/device notifications invalidate a thread-safe topology generation immediately, release key consumption, clear pending writes and audio reconciliation, and schedule debounced discovery with bounded retries.
15. A 10-second watchdog fails a stalled DDC operation closed without releasing its serialization slot; when the worker eventually returns, its result is ignored and read-only rediscovery follows.
16. Shutdown stops plugin shortcut observation first, rejects new plugin triggers, signals Discord's one-second wait so restoration can finish within the shared two-second plugin budget, then stops display/hook/tray loops, reports missed deadlines, closes the overlay, destroys Tk, closes diagnostics, and releases the mutex.

## Runtime process and thread ownership

Source execution uses one interactive process per Windows session, enforced by `SingleInstanceGuard` and the named `Local\fensoundswitch-single-instance` mutex. The one-file Nuitka executable adds a compiler-controlled bootstrap/extraction phase, but the repository does not customize that phase.

| Thread | Lifetime | Owns or performs | Communication boundary |
| --- | --- | --- | --- |
| Tk main thread | Process lifetime | Widgets, state mutation, selection persistence, status, overlay, queue draining, diagnostic-handler lifecycle | Receives queued callables and key deltas every 50 ms |
| `display-change-listener` | Long-lived daemon | Hidden Win32 window, monitor device registration, display/device and theme/system-color message pump | Invalidates the topology generation or enqueues a live-theme refresh in Tk |
| `tray-icon` | Long-lived daemon | Hidden Win32 window, notification icon, live snapshot menu, message pump | Reads lock-protected immutable state; controller actions enqueue Tk work through `_post_to_ui()` |
| `volume-key-hook` | Long-lived daemon | `WH_KEYBOARD_LL` hook and message pump | Reads readiness through `should_consume()` and enqueues integer deltas |
| `plugin-hotkey-loop` | Long-lived daemon | `WH_KEYBOARD_LL` observer | Queues manager binding IDs and consumes only explicitly configured held action/route key pairs; never calls plugin or Tk code directly |
| `plugin-hotkey-dispatch` | Long-lived daemon | Dispatches queued shortcut binding IDs | Starts one manager-owned trigger worker only when that action is not already active |
| `plugin-<id>` | Short-lived daemon per accepted trigger | Runs trusted plugin trigger code | Reports status through the host context; never calls Tk directly |
| `discord-output-auth` | Short-lived daemon | Discord named-pipe authentication, consent-code exchange, and token reuse/refresh | Reports privacy-safe status through `_post_to_ui()`; Credential Manager holds secrets |
| `ddc-gui-worker` | One short-lived daemon per accepted refresh/selection read | Enumeration and selected-monitor volume reads | Enqueues success or failure callable |
| `ddc-volume-write` | One short-lived daemon at a time | Selected-monitor set followed by readback | Enqueues write success or failure callable |
| `audio-output-sync` | One short-lived daemon at a time | Monitor/endpoint metadata reads, fail-closed matching, endpoint visibility updates, and optional administrator-consent request | Enqueues a result/status callback; reads topology validity but never calls Tk |

`MonitorVolumeApp._result_queue` carries zero-argument callbacks into Tk. `_hotkey_delta_queue` carries signed step changes. `_poll_queues()` drains all result callbacks first, combines key deltas accumulated during the poll interval, then schedules itself again after 50 ms. Each callback and the combined adjustment are caught independently; a failure is reported through Tk's callback reporter, fails monitor control closed, and the next poll is scheduled from `finally`.

The `_busy` flag prevents a new refresh/selection worker while another general operation is active. Volume controls remain usable during a valid active volume write, but they update `_pending_target_volume` rather than starting another DDC worker. A token identifies the one application-issued DDC operation, and a Tk watchdog tracks it. A timeout disables controls and interception but deliberately retains the token and busy/write flags until the uncancellable worker returns. This is the application's serialization boundary; there is no separate DDC lock.

Display, tray, Volume-key hook, and plugin-hotkey startup readiness waits are bounded. Plugin shutdown has one shared two-second budget; registered shortcuts stop before plugin resources, and Discord receives an early stop signal so an active temporary route restores before pipe closure is used as fallback. Other native controllers retain their two-second joins. DDC and audio-output workers are daemon threads and are not cancelled or joined; `_closing` drops their callbacks, and topology invalidation blocks subsequent audio mutations at the next safety check. A native call already entered cannot be cancelled.

All Tk calls must remain on the Tk thread. A native or DDC thread must enqueue work rather than touching widgets. Queued callbacks must remain small and exception-safe even though the polling boundary now contains and reports their failures.

## Application entrypoints and composition

### Supported entrypoint: `app.py`

`app.main()` keeps the ordinary composition path small:

```text
SingleInstanceGuard -> configure logging -> tk.Tk -> MonitorVolumeApp -> Tk mainloop -> close logging -> release guard
```

The guard is acquired before logging or Tk and always closed in `finally`. This keeps a duplicate from opening the rotating log concurrently with the primary. `ERROR_ALREADY_EXISTS` makes the duplicate register and broadcast the restore message, then return `0` without reading settings, importing or discovering plugin code, reading credentials, or creating hooks, tray state, or DDC workers. `gui.py` deliberately imports `plugin_manager` inside post-Tk initialization so the module boundary preserves that rule. Both source execution and `build_exe.ps1` use `app.py` and therefore share the same mutex.

The only path before the guard is the app-generated `--internal-rename-audio-endpoint <validated-id>` execution mode. Shell32 launches it through `runas`; it writes the fixed `FenSound` alias through Core Audio and exits without logging, Tk, settings, hooks, tray state, or DDC work. It is deliberately not an installed or supported user CLI.

### Unsupported entrypoint: `main.py`

`main.py` prints `This launcher is no longer supported. Run: python app.py` to standard error and returns `1`. It is not listed in `[tool.setuptools].py-modules`. It is a compatibility signal, not an alternate composition root.

### No installed command or HTTP entrypoint

`pyproject.toml` has no `[project.scripts]` or `[project.gui-scripts]` entry and there is no supported command surface. The strict internal rename execution mode described above is not a general argument parser. There are no HTTP handlers, routes, sockets, or method/route semantics. The only user entry is process launch. Principal runtime inputs include GUI/native events, DDC results, monitor/audio endpoint metadata, Windows theme and system metrics, the current-user Run value, settings-path/environment resolution, settings contents, and tracked icon-file availability.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `app.py` | Enforces the single-instance boundary, then creates and runs the application. |
| `audio_outputs.py` | Reads monitor/render endpoint identity, matches fail-closed, changes matched endpoint visibility, and implements the fixed-purpose FenSound rename helper. |
| `autostart.py` | Quotes source/packaged launch commands and reads/writes the named current-user Run value. |
| `diagnostics.py` | Configures and closes the bounded per-user rotating log and provides component loggers. |
| `gui.py` | Coordinates Tk, monitor selection, readiness, DDC workers, rapid-write coalescing, live theme/DPI reflow, keyboard navigation, overlay targeting, persistence, tray lifecycle, and shutdown. |
| `ddc.py` | Defines `MonitorRef`, selection identity, monitor discovery, clamping, and DDC read/change/write wrappers. |
| `plugins/ddc_volume_plugin.py` | Bundled generic volume provider that owns DDC target configuration, fresh reads/writes, and active-only monitor audio policy. |
| `plugins/denon_marantz_volume_plugin.py` | Bundled Denon/Marantz AVR main-zone provider using bounded TCP `MV` commands. |
| `plugins/discord_output_plugin.py` | Implements Discord local RPC, OAuth/token refresh, Credential Manager storage/migration, and exact temporary output restoration. |
| `plugins/audio_keepalive_plugin.py` | Configurable action plugin that renders silence to the active Windows default playback and/or communications endpoint. |
| `plugins/windows_default_device_plugin.py` | Provides four host-configured shortcut actions for active Windows playback, voice output, input, and microphone devices; each applies the next device only to its corresponding default roles. |
| `plugins/onkyo_volume_plugin.py` | Bundled main-zone Onkyo/Integra eISCP volume provider with bounded outbound TCP transport and plugin-owned receiver configuration. |
| `plugins/pioneer_elite_volume_plugin.py` | Bundled Pioneer/Elite main-zone provider using bounded TCP IP control. |
| `plugins/sony_volume_plugin.py` | Bundled Sony network AVR main-zone provider using bounded JSON-RPC HTTP requests. |
| `plugins/yamaha_volume_plugin.py` | Bundled Yamaha main-zone provider using bounded TCP YNCA commands. |
| `settings.py` | Atomically loads selected-monitor, legacy Change speed, and ordered route JSON settings. |
| `plugins/windows11_overlay_plugin.py` | Windows 11 renderer with typed current/all presentation settings and safe migration of the old global setting. |
| `plugins/macos_overlay_plugin.py` | Distinct macOS-inspired renderer that shares the focus-safe Windows presentation path. |
| `plugin_api.py` | Defines API version 3, `HotkeySpec`, `ShortcutAction`, optional input, volume-provider, and selectable overlay-renderer capabilities, plus a Tk-safe plugin text-overlay host callback. |
| `plugin_hotkeys.py` | Owns the native keyboard observer, off-thread dispatch queue, and explicit route-key consumption policy. |
| `plugins/keyboard_input_plugin.py` | Defines two route-scoped keys with a persisted forward/consume policy; duplicate combinations are rejected rather than broadcast. |
| `plugins/mqtt_input_plugin.py` | Creates one bounded MQTT client per configured route, routes absolute commands through the Tk queue boundary, and publishes retained Home Assistant MQTT slider discovery. |
| `plugins/windows_soundcard_volume_plugin.py` | Selects a stable Windows render endpoint and reads/writes normalized master volume through `core_audio.py` only in workers. |
| `plugins/windows_microphone_gain_plugin.py` | Selects a stable Windows capture endpoint and reads/writes normalized gain through `core_audio.py` only in workers. |
| `core_audio.py` | Minimal ctypes Core Audio adapter for render/capture endpoint discovery, endpoint master volume, explicit default-role changes, and silent shared-WASAPI playback; it never changes endpoint visibility or names. |
| `plugin_manager.py` | Discovers trusted bundled/external plugins, isolates failures, owns configuration UI, suppresses overlapping triggers, and coordinates shutdown. |
| `theme.py` | Reads Windows theme/High Contrast/DPI state, defines reversible ttk styling, applies DWM chrome, and resolves the icon. |
| `windows_platform.py` | Declares the Win32 ctypes ABI and implements the single-instance mutex/restore signal, monitor identity/EDID inventory, display work-area/window-DPI lookup, High Contrast query, no-activate overlay operations, display/theme notifications, snapshot-driven tray menu, keyboard hook, and DWM helpers. |
| `main.py` | Rejects the old launch path. |

`ddc.change_monitor_volume()` is an internal helper but is not used by the GUI. The GUI uses its own cached-target and serialized-write flow so rapid key events can be coalesced.

## Runtime plugins and Discord OAuth

`plugin_manager.discover_plugins()` directly imports API-v3 first-party modules from the bundled `plugins` package in a fixed precedence order: Windows 11 overlay, macOS-style overlay, Discord, audio keep-alive, Windows default-device, DDC, Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, Sony, and Windows Volume input. It then scans sorted `external-plugins\*.py` beside the source/executable, sorted `%APPDATA%\fensoundswitch\plugins\*.py`, and the legacy trusted `%APPDATA%\windows-ddc\plugins\*.py` as a final read-only fallback. The bundled package is never dynamically scanned. Repeated physical external directories are collapsed. A candidate module must expose `PLUGIN_API_VERSION = 3` and `create_plugin()`; malformed objects, later duplicate IDs, import exceptions, and initialization exceptions become visible failure records. The host persists disabled action-plugin IDs in `%APPDATA%\fensoundswitch\plugin-settings\action-plugin-state.json`; a disabled action plugin stays visible but does not initialize or register shortcuts until enabled. There is no sandbox or runtime reload: every discovered external file is deliberately trusted and executes in-process at primary startup.

The host gives each loaded plugin a status/logging boundary, `_post_to_ui()` bridge, prepared Tk-parent/window callbacks, plugin-owned non-secret settings callbacks, and a volume-refresh callback. Plugin API v3 rejects older modules at discovery. The main window embeds shared manager-built Routes and action-only Plugins panels; endpoint editors, shortcut capture, Discord configuration, and typed overlay settings remain prepared modal dialogs. Overlay definitions are excluded from action plugins; the Routes Overlay section persists exactly one active renderer in `%APPDATA%\fensoundswitch\plugin-settings\active-overlay.json` and safely hands off the Tk renderer. The Windows 11 renderer owns current/all presentation and migrates the removed global `overlay_mode` into its own settings. Action plugins declare named `ShortcutAction` values; the manager persists bindings in `%APPDATA%\fensoundswitch\plugin-settings\shortcuts.json`, captures/configures them, detects conflicts, and dispatches `trigger_shortcut(action_id)` off Tk. Route plugins expose input/output factories: every schema-v6 route has a stable route ID plus independent `{plugin_id, parameters}` input and output endpoints. Parameters remain internal persistence data; the Routes panel uses definition-specific controls rather than raw JSON. The manager constructs a distinct output instance for every route, with no provider-global route configuration file; v4/v5 records are migrated on the next save and old files are retained. Blocking normalized `0`–`100` reads/writes remain worker-routed through the one serialized host lane. Bundled network providers make only bounded outbound requests and have no discovery, listener, or polling loop. `PluginHotkeyController` accepts any nonzero Windows keyboard virtual-key code with optional Ctrl/Alt/Shift/Win modifiers, detects duplicate in-process combinations, suppresses held-key repeats, and always calls the next hook so the foreground application receives the original input. Modifier keys act as capture prefixes rather than standalone trigger keys. It is independent of `GlobalVolumeKeyListener`.

The current composition extends that original Routes/Plugins ownership: the manager now builds the Routes, Actions, and Appearance pages, while `gui.py` owns Settings. Routes and Actions use bounded scrollable card lists. Routes and Appearance register synchronized views of the one persisted overlay renderer, and every visible Start with Windows control is refreshed from the same GUI-owned state after a mutation.

Keyboard route bindings retain an explicit `forward_keys` policy in their input parameters. Action-plugin shortcuts persist the same policy in `shortcuts.json`; legacy bindings migrate to `true`. An action or route with `forward_keys: false` consumes only its configured non-modifier key down/repeats and the matching held key up; modifier and unrelated input always call the next hook. Rebinding, route removal, hook errors, and shutdown release held keys immediately.

The Discord plugin reads and writes a generic current-user Credential Manager target named `fensoundswitch/plugins/discord-output/oauth-rpc`. Valid older `windows-ddc/plugins/discord-output/oauth-rpc` and `windows-ddc/test-discord/oauth-rpc` values are copied without deletion only when no current value exists. The blob contains Application ID, client secret, redirect URI, access/refresh tokens, and expiry. With no saved configuration, Tk opens the Developer Portal and one modal that asks for the reset secret before the public Application ID and explains the exact `https://127.0.0.1` redirect and restricted scopes. The worker exchanges the local RPC `AUTHORIZE` code at Discord's HTTPS OAuth endpoint; the first Discord consent cannot be automated away. Valid access tokens authenticate locally, expired tokens refresh silently, and revoked grants fail only this plugin until explicit reauthorization.

On a registered press, the manager rejects overlap and starts `plugin-discord-output`. It authenticates a fresh named-pipe client, captures `GET_VOICE_SETTINGS.output.device_id`, chooses the first different available ID other than Discord's dynamic `default`, verifies the temporary `SET_VOICE_SETTINGS`, waits one second, and verifies restoration in `finally`. Closing the pipe is the last restoration fallback. No Discord or RPC worker calls Tk.

## Monitor model and DDC/CI boundary

### Dependency boundary

`ddc.py` imports `monitorcontrol.get_monitors` and `monitorcontrol.vcp.VCPError`. An unavailable dependency is captured at import time so the GUI can start far enough to report a `DDCError` during discovery rather than failing the module import immediately.

The pinned `monitorcontrol==4.2.0` library maps `get_volume()` / `set_volume()` to MCCS VCP sound-volume code `0x62`. Inside that dependency—not in application code—the Windows backend enumerates with `EnumDisplayMonitors`, `GetNumberOfPhysicalMonitorsFromHMONITOR`, and `GetPhysicalMonitorsFromHMONITOR`; reads with `GetVCPFeatureAndVCPFeatureReply`; writes with `SetVCPFeature`; and ultimately releases handles with `DestroyPhysicalMonitor`. The application treats the library's monitor wrapper as the external hardware boundary and always enters it as a context manager:

```python
with monitor_ref.monitor:
    # get_volume() and/or set_volume(...)
```

Application code never touches a raw physical-monitor handle; the dependency owns the wrapper and handle lifetime. Keep application calls inside the existing context-manager boundary, but do not assume that leaving the context itself destroys the pinned Windows backend's retained handle.

### `MonitorRef` and selection identity

Each immutable `MonitorRef` retains the transient index, monitorcontrol wrapper, description/ordinal, short GDI display name, and optional `MonitorIdentity`. The combobox adds `S/N <serial>` when usable, otherwise the short `DISPLAYn` name; the full device-interface path remains internal.

`enumerate_monitors()` takes a Windows identity snapshot before and after `get_monitors()`. Identity enumeration follows the same `EnumDisplayMonitors` traversal, maps a one-physical-monitor/one-active-interface logical display, reads EDID through SetupAPI, and returns no identity for ambiguous mappings. A changed snapshot or wrapper/identity count mismatch rejects the discovery.

Stable matching follows these rules:

1. A unique normalized EDID manufacturer/product/serial tuple matches across device-path changes.
2. Duplicate serial tuples require an exact case-insensitive saved device path.
3. A monitor without a usable serial requires its exact saved device path.
4. Missing, ambiguous, and unverifiable targets never fall back to another monitor.
5. With no saved target, only one verifiable monitor can be selected automatically.

Legacy description/ordinal settings are loaded, but the ordinal is not trusted after topology changes. A legacy description promotes only when exactly one verifiable current monitor has that description. Selection is persisted only after a successful volume read.

### Read and write semantics

All volume targets and readbacks exposed by `ddc.py` are clamped to `0`–`100`.

- `read_monitor_volume()` opens the monitor context, calls `get_volume()`, and clamps the result.
- `set_monitor_volume()` opens one context, writes the clamped target, immediately reads it back, and returns the clamped readback.
- `change_monitor_volume()` reads, adds the requested delta, clamps the resulting target, writes only when the value changes, then reads back.

The set/readback sequence is not a transaction and has no rollback. A write can reach the monitor before readback or topology validation fails. The GUI then marks volume unknown, disables control, reports uncertainty in the overlay/status, and performs read-only rediscovery without retrying the write.

Application clamping defines the UI range; it does not prove that every device accepts every target. For example, the pinned dependency can raise `ValueError` when a monitor reports a maximum below the requested value. Such non-`VCPError` exceptions cross the generic GUI worker boundary and their message is shown in the status bar.

Discovery catches `NotImplementedError` and `VCPError` as a detection error. Individual DDC operations translate `VCPError` to `DDCError`. The GUI worker boundary catches other exceptions and exposes their message in the status bar.

## Volume request and concurrency flow

The GUI maintains both confirmed and requested state:

- `current_volume`: last successful read or write readback;
- `target_volume`: latest UI target;
- `_volume_write_inflight`: whether a DDC write worker owns the operation slot;
- `_pending_target_volume`: the newest target requested during that write.

A request follows this sequence:

1. `_request_volume_target()` clamps and stores the target.
2. The scale/text are updated optimistically and the overlay is shown immediately.
3. If a write is active, only `_pending_target_volume` is replaced.
4. Otherwise `_start_volume_write()` marks the app busy and launches `ddc-volume-write`.
5. The worker calls `enumerate_monitors()`, exact-matches the saved identity, and checks its captured topology generation immediately before the write.
6. `set_monitor_volume()` performs set/readback only through the newly acquired wrapper and inside one context.
7. Success is accepted only if the generation remains current. A distinct pending target starts another worker and another discovery/match.
8. When no follow-up remains, confirmed readback is displayed and readiness is restored.
9. Missing/ambiguous identity, DDC failure, or a stale generation clears pending state, marks volume unknown, releases interception, shows an unavailable overlay, and schedules read-only rediscovery.
10. If the 10-second watchdog fires first, the operation token remains active and no new worker can start. The late completion is non-authoritative; only after it arrives is the slot released and a read-only refresh scheduled.

This last-target-wins design limits DDC traffic while ensuring every actual hardware write—not every key-repeat message—uses fresh handles. An in-flight native DDC call cannot be cancelled; generation checks reduce but cannot remove the final check-to-write race.

The watchdog bounds how long readiness remains trusted; it does not cancel or terminate the native DDC call.

## Global keyboard event flow

`windows_platform.GlobalVolumeKeyListener` installs a `WH_KEYBOARD_LL` hook with thread ID `0`. Its ctypes callback is kept alive in `_hook_callback` for the controller's lifetime.

The callback passes unrelated keys directly to `CallNextHookEx`. It recognizes only:

- `VK_VOLUME_DOWN` (`0xAE`)
- `VK_VOLUME_UP` (`0xAF`)

When `MonitorVolumeApp._should_consume_volume_keys()` is false, those keys pass onward. If a previously configured target is unavailable, the first key-down in that invalid period also queues an error overlay without consuming the event. The notice is latched until readiness is restored or a new invalidation begins. On a consumed press, repeated key-down and matching key-up retain the initial consume decision; readiness loss stops new deltas but does not split a press between the app and Windows. The hook's numeric step is updated through a lock-protected setter when Tk handles the persistent Slow (`+1`), Medium (`+2`), or Fast (`+3`) Change speed event, so the native thread never reads Tk state. The mute key is not recognized.

The callback does not inspect the `KBDLLHOOKSTRUCT.flags` injection bits. Synthesized Volume Down/Up events are therefore handled like physical-key events.

`_update_hotkey_state()` computes the key-consumption state as:

```text
`_hotkeys_ready` is true
AND app not closing
AND the display-change listener is live
AND the topology generation is valid
AND the listener exists and its native hook is active
AND selected key exists
AND confirmed current volume exists
```

`_hotkeys_ready` is application/DDC state set after successful selected-volume reads or write readbacks. Native hook liveness is tracked independently by `GlobalVolumeKeyListener.is_active`; `_should_consume_volume_keys()` rechecks both the computed state and live listener state at callback time.

There is no user preference to disable the hook while leaving the app running. Display invalidation or write failure clears readiness so subsequent physical presses pass through until exact-match rediscovery and a successful read. A hook failure disables only global interception; a display-listener failure disables all monitor-volume writes.

## Display and theme event flow

`DisplayChangeListener` owns a separate hidden window registered for `GUID_DEVINTERFACE_MONITOR`. `WM_DISPLAYCHANGE` and relevant `WM_DEVICECHANGE` messages synchronously clear a thread-safe topology-valid event and increment a generation before posting Tk work. Tk then clears cached/pending volume state and schedules a 500 ms debounced refresh; transient automatic failures retry after 1, 2, and 4 seconds.

The same listener routes `WM_SETTINGCHANGE`, `WM_SYSCOLORCHANGE`, and `WM_THEMECHANGED` through `_post_to_ui()`. Tk debounces those broadcasts for 100 ms, rereads app-theme and High Contrast state, reapplies reversible widget/overlay palettes and DWM chrome, and reflows the control window. Native callbacks do no blocking discovery or Tk work on the listener thread.

## Windows sound-output reconciliation

The selected DDC monitor and its Windows render endpoint come from separate inventories. `audio_outputs.py` joins them without persisting endpoint IDs:

1. The successful DDC refresh supplies every current monitor interface path and the exact selected path.
2. Read-only `HKLM\SYSTEM\CurrentControlSet\Enum` lookups resolve each monitor's container ID.
3. Read-only `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render` and endpoint-PnP lookups enumerate only currently active or audio-policy-hidden render endpoints, their descriptions, adapter names, container IDs, and visibility.
4. Unique container IDs produce direct monitor/endpoint matches. Duplicate containers are not accepted.
5. After direct matches, exactly one unmatched monitor may be paired with exactly one unmatched same-adapter endpoint only when that endpoint has no usable container ID. More than one candidate, or a conflicting non-placeholder container ID, is never guessed.
6. If the selected path is still unmatched, reconciliation raises `AudioOutputMatchError` before any output mutation. Non-display endpoints and unmatched endpoints are never included in the hide plan.

`gui.py` coalesces requests into at most one `audio-output-sync` worker plus the latest pending topology. The worker checks the generation and thread-safe topology-valid event before its first mutation and between endpoint changes. It first re-enables the selected endpoint through private `IPolicyConfig::SetEndpointVisibility`, then hides only visible endpoints mapped to other current monitors. These Windows policy changes outlive the application. The interface is not a documented public Windows contract, so failures are nonfatal and surfaced in the status bar while DDC control remains ready.

If the selected endpoint's device description is not `FenSound`, the worker asks Shell32 to start the strict internal helper with administrator consent. The helper validates the render endpoint ID again, accepts no caller-supplied alias, opens that endpoint with `IMMDevice::OpenPropertyStore(STGM_READWRITE)`, writes `PKEY_Device_DeviceDesc`, commits, and exits. A successful launch is asynchronous; the primary does not assume the property write completed. Each endpoint is attempted at most once per primary-process session, including cancellation/failure, to prevent repeated consent prompts. Once the persisted alias is visible on a later refresh or restart, no helper is requested.

The public Core Audio endpoint-property contract supports reading but makes the non-administrator property store read-only; that is why the normal process never attempts the rename directly. The app does not call a default-endpoint setter or change mixer/application-session volumes, although Windows may reroute sound when hiding makes a former output unavailable. Relevant Microsoft contracts are [audio endpoint properties](https://learn.microsoft.com/en-us/windows/win32/coreaudio/device-properties), [`IMMDevice::OpenPropertyStore`](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immdevice-openpropertystore), and [audio endpoint container IDs](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-endpoint-container-id).

## Tray and window event flow

`TrayIconController` creates a per-process hidden Win32 window named with the process ID and controller identity. Its message loop handles private show/hide/exit messages, the registered `TaskbarCreated` broadcast, `Shell_NotifyIconW` callbacks, and a native popup menu.

- Icon ID: `1`
- Tooltip: `FenSoundSwitch`
- Double-click: Restore
- Context menu status: active monitor, last confirmed volume, and routing enabled/disabled
- Context menu actions: Restore (`1001`), Exit (`1002`), Refresh (`1003`), and up to 100 selectable monitor commands starting at `2000`

Tk builds `TrayMenuState` from `selected_key`, confirmed `current_volume`, `_hotkeys_enabled`, and every currently verifiable monitor, then replaces the controller's snapshot under a lock whenever those values change. The tray thread copies the snapshot when the popup opens. Checked monitor entries retain that captured `SavedMonitorSelection`; even if Tk publishes a newer list while the native menu is open, its returned command maps to the old menu's exact identity rather than the same numeric index in the new list. Labels normalize whitespace, escape Win32 mnemonic ampersands, and are bounded to 96 characters.

Refresh and monitor-switch callbacks never invoke Tk directly. The controller calls closures that enqueue `refresh_monitors()` or `_select_monitor_from_tray()` through `_post_to_ui()`. A switch starts normal exact-identity discovery/read and is rejected while another DDC operation is active. Menu volume remains the authoritative read/readback value during optimistic or coalesced writes; routing reflects the same `_hotkeys_enabled` state used by the native hook.

The controller tries to load `FenSoundSwitch.ico` at the Windows small-icon dimensions and falls back to `IDI_APPLICATION`. The main Tk window also tries the same tracked icon; icon failure is nonfatal.

The tray's native window is created synchronously from the caller's perspective because `start()` waits up to two seconds for `_ready`. `show()` creates a per-request completion event, posts `WM_TRAY_SHOW`, and waits up to two seconds for the tray thread's actual `Shell_NotifyIconW` result. `MonitorVolumeApp.minimize_to_tray()` withdraws Tk only after that acknowledgement. A native failure, stopped controller, post failure, or timeout keeps/restores and normalizes the main window, hides any late icon best-effort, and reports the error in the visible status bar.

The controller registers the shell's `TaskbarCreated` message and the app's stable duplicate-launch restore message during construction. A restore broadcast invokes the normal tray-to-Tk restore callback. When Explorer recreates the taskbar, a previously visible icon is re-added with `NIM_ADD` and its notification version is restored. If re-registration fails, the error crosses the queue boundary and Tk restores the main window instead of leaving the process unreachable.

Minimizing a visible Tk window schedules an idle check and withdraws it only if the state is `iconic`. Restoring hides the tray icon, normalizes/lifts/focuses the Tk window, and reapplies dark title-bar chrome. Closing the visible window follows the full shutdown path rather than minimizing.

## Frontend and overlay

The control window is vertically resizable with a DPI-scaled 900-logical-pixel minimum width. Its persistent sidebar switches among four real pages. Routes combines live summary cards, selectable route cards, route actions, quick startup/overlay settings, and an action-plugin summary. Actions exposes selectable plugin cards and configuration/shortcut/state controls. Appearance owns the overlay preview, renderer selection, and renderer settings. Settings owns startup and configuration archives. Diagnostics and the status bar remain directly available. Unmapped pages do not participate in keyboard traversal. Tray Refresh remains the explicit status rediscovery action; the main window has no Refresh, Plugins, or Routes launch buttons.

Widget state derives from `_busy`, monitor availability, confirmed volume, display-listener liveness, and topology validity. During a valid active write the volume controls remain enabled so a new last target can be queued.

All interactive widgets participate explicitly in `Tab`/`Shift+Tab` traversal. `Escape` minimizes to the tray; removed root actions no longer reserve `Alt+R`, `Ctrl+R`, `F5`, `Alt+P`, or `Alt+U`. The embedded action list opens a selected plugin's configuration on double-click, while the Routes panel exposes its Add, Edit, Duplicate, and Remove actions directly. Disabled shortcut targets are ignored.

`VolumeOverlay` is a borderless tool `Toplevel` with a live palette. Normal mode shows percentage/progress and hides after 1.4 seconds. Error mode shows an `Unavailable` heading plus wrapped reason text, hides the progress bar, and remains for 2.8 seconds. Alpha is `0.96` in dark mode, `0.97` in light mode, and `1.0` in High Contrast; Tk's tool-window attribute remains best-effort.

Every presentation takes a fresh `GetCursorPos` reading and current `MONITORINFOEXW` snapshots. The display containing the cursor wins. If the cursor cannot be resolved to an enumerated display, the selected `MonitorRef.display_device_name` is used, with the primary or first enumerated display as the final fallback. Geometry is bottom-centered inside `rcWork`, supports negative virtual-screen coordinates, scales the side/top/bottom margins with `GetScaleFactorForMonitor`, and clamps oversized content inside the available work area.

Focus safety is fail-closed. The native top-level HWND must accept the preserved extended styles plus `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` before Tk deiconifies it. `SetWindowPos(HWND_TOPMOST, ..., SWP_NOACTIVATE | SWP_SHOWWINDOW)` then positions and reveals it without a Tk `lift()` or focus call. A style or native-show failure withdraws the overlay instead of allowing an activating fallback.

`theme.is_windows_dark_mode_enabled()` reads the current user's:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme
```

Only a DWORD value of `0` selects dark mode. `SystemParametersInfoW(SPI_GETHIGHCONTRAST)` supplies the live High Contrast state; High Contrast suppresses the custom dark palette and uses Windows system color names throughout the Tk window and opaque overlay. Dark mode creates a custom ttk theme, while light/High Contrast prefers `vista`, `xpnative`, then `winnative` when available. Named Fluent styles provide the shared sidebar, card, navigation, dialog, field, button, and tree hierarchy without changing the Tk plugin UI contract. DWM attributes `20` then `19` are applied in both directions so a live transition also clears dark title chrome. These lookups are read-only.

`GetDpiForWindow` supplies the restored control window's current DPI with a 96-DPI fallback. Shell padding, sidebar width, status metrics, and minimum width scale from logical values, and a debounced top-level `<Configure>` callback reapplies them only when the window DPI changes. Overlay geometry separately retains its per-destination-screen scale lookup.

The manually maintained screenshots are tracked at `docs/app.png` (452×203) and `docs/overlay.png` (210×122). They predate the route-page navigation, Fluent visual system, live theme/scaling behavior, and current error presentation; there is no automated screenshot workflow, so updates require real scrubbed captures.

## Persistent data, registry, and filesystem ownership

The intended app-owned durable state is the main settings file, plugin-settings JSON, bounded diagnostic logs, an optional named current-user Run value, and Discord OAuth data in current-user Credential Manager. Windows separately persists the render-endpoint visibility and FenSound description changed through Core Audio; the app does not copy endpoint IDs into its JSON. The normal settings path is:

```text
%APPDATA%\fensoundswitch\settings.json
```

If `APPDATA` is unset or empty, `settings.SETTINGS_PATH` uses:

```text
<home>\fensoundswitch\settings.json
```

It inherits the current user's filesystem permissions. The schema is:

```json
{
  "schema_version": 2,
  "change_speed": "medium",
  "selected_monitor": {
    "description": "physical monitor description",
    "identity": {
      "device_path": "Windows monitor interface path",
      "manufacturer_id": "DEL",
      "product_code": 4660,
      "serial_number": "EXAMPLE-SERIAL"
    }
  }
}
```

`save_selected_monitor_key()` requires a stable identity and preserves a valid Change speed. `save_change_speed()` normalizes its value and preserves current schema-v2 or legacy monitor-selection data. Both create the parent directories, write indented UTF-8 JSON to sibling `settings.tmp`, and replace `settings.json`. This reduces exposure to a partial destination but provides no file lock. The session mutex prevents ordinary project instances in one Windows session from racing, but separate sessions and external tools are not coordinated. Persistence failure is not visible in the status bar.

The monitor-selection writer emits schema version 2. Its loader accepts the old unversioned description/ordinal shape for safe one-time promotion, rejects boolean ordinals, and treats missing files, I/O failures, invalid JSON, non-object roots, unknown versions, and invalid nested data as no selection. Change speed is an independent top-level preference: missing or invalid values default to `slow`, and valid values are `slow`, `medium`, and `fast`.

Route persistence uses schema version 8. Each ordered `volume_routes` record has a stable `route_id`, a validated user-defined `name`, and JSON input/output endpoints. Schema-v6 records are read safely with a deterministic `<input ID> to <output ID>` name and are upgraded only when routes are next saved, preserving unrelated settings. Route names identify independent output instances in the Routes table, status messages, and all overlay rows.

The monitor's actual volume is external mutable hardware state, not app storage. The app reads it after discovery or selection and never backs it up or restores it on exit.

Plugin-owned non-secret settings live under `%APPDATA%\fensoundswitch\plugin-settings`; the Discord file contains only `schema_version` and a nullable modifier/virtual-key pair. First-party executable code lives in the bundled `plugins` package and is imported directly. External executable code is discovered separately under `external-plugins` beside the source/executable and `%APPDATA%\windows-ddc\plugins`. OAuth client configuration and tokens never enter either JSON location.

`diagnostics.LOG_PATH` normally resolves to `%LOCALAPPDATA%\fensoundswitch\fensoundswitch.log`, falling back to `APPDATA` and then the home directory. `RotatingFileHandler` caps the current log at 512 KiB and retains two backups. Handler creation failure installs a managed `NullHandler`, so an unwritable diagnostic location never blocks application startup. Routine GUI messages log operation classes rather than monitor descriptions, identities, or device paths; unexpected top-level tracebacks can still contain local source paths.

`autostart.py` treats `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FenSoundSwitch` as the Start with Windows source of truth. A legacy `windows-ddc` value is read for compatibility; only an explicit checkbox change writes the new value and removes the legacy value. A packaged launch quotes the absolute original one-file path from `sys.argv[0]`; a source launch quotes a sibling `pythonw.exe` when available plus the repository `app.py`. The helper rejects commands longer than the Windows Run-key limit of 260 characters. Registry errors restore the prior checkbox value, update status, and emit a privacy-safe diagnostic. Moving the registered target can leave a stale value until the user disables or replaces it.

### Backup and restore format

The main-window Export action opens a save dialog in `%APPDATA%\fensoundswitch\configurations` with a timestamped `.fsc` filename by default. The ZIP-compressed archive contains `settings.json` plus flat `plugin-settings/*.json` files, all validated as JSON objects before import. Every export is copied into that default directory for the import history. On first primary launch, `default.py` creates `default.fsc` in the same directory without overwriting an existing archive. Archives deliberately exclude Credential Manager OAuth secrets, executable plugins, logs, audio-endpoint state, and hardware state. The Import down-arrow shows the five newest non-default archives and waits for the user to select one. Every import requires confirmation that the app will restart, replaces saved main and plugin settings only after archive validation, shuts down through the normal controller lifecycle, and re-executes the app. The Default action reads this generated archive. There is no database dump, encryption, or integrity signature.

Tests must replace settings/plugin paths and diagnostic destinations with unique temporary paths and mock `autostart.winreg`. Audio-output tests mock registry enumeration, COM visibility, and elevation. Discord tests mock Credential Manager, browser, named-pipe, and HTTPS boundaries. Automated tests must not read, redirect, mutate, or delete live app-data, credentials, Run values, audio endpoints, Discord settings, or grants.

## Authentication and security boundaries

There is no FenSoundSwitch account model because there is no remote or multi-user application interface. The primary process runs unelevated in the launching user's interactive session. There is no elevation manifest; a narrowly scoped helper process is launched through `runas` only when the selected endpoint needs the fixed FenSound description. Discord independently controls its OAuth consent and restricted RPC scopes.

The security-relevant boundaries are local:

1. **Global input hook.** The low-level hook receives desktop keyboard callbacks, acts only on Volume Down/Up, does not persist events, and can suppress those keys globally while ready.
2. **Physical hardware writes.** DDC/CI writes change monitor state and may produce an audible volume change. They are neither transactional nor rolled back.
3. **Native ABI.** Incorrect ctypes structures, argument types, callback signatures, or callback lifetimes can corrupt or crash the process.
4. **Cross-thread UI.** Tk is not thread-safe; queues are the required ownership boundary.
5. **Per-user files.** Settings and diagnostics are user-writable and unencrypted. Settings contain monitor identity metadata; routine diagnostics avoid it, although unexpected tracebacks can contain local paths. A device-interface path is machine-specific but is not a credential.
6. **Current-user autostart.** The opt-in Run value stores an absolute local command and executes it at sign-in. It requires no administrator access, but moving the target can leave stale machine-specific path data.
7. **Windows audio policy.** The primary reads machine endpoint metadata and uses a private Windows COM interface to persist visibility for positively matched display-audio endpoints. A topology race can occur after a validity check; the next successful refresh repairs the selected endpoint first. Unmatched outputs are never hidden.
8. **Elevated rename helper.** The helper accepts only a syntactically valid render endpoint ID and a code-fixed alias, performs one endpoint property write, and exits before ordinary app initialization. Windows administrator consent remains the authority boundary, and cancellation is nonfatal.
9. **Trusted external plugins.** Every discovered Python file executes unsandboxed in the primary process with current-user access. Folder placement is the opt-in boundary; there is no permissions mediation.
10. **Discord credentials and network.** Client configuration and tokens stay in current-user Credential Manager and are excluded from deliberate logs. OAuth token/refresh exchanges use Discord HTTPS; voice-setting commands use Discord's local named pipe. The app does not host a callback server.

External plugins can use arbitrary network paths. The bundled Discord plugin contacts Discord's OAuth token endpoint and local RPC pipe. Dependency installation and Nuitka's `--assume-yes-for-downloads` can also contact package/toolchain servers during development or build.

## Build and deployment architecture

`pyproject.toml` uses `setuptools.build_meta` as its PEP 517 backend, with unpinned `setuptools` and `wheel` build-system requirements. It declares project version `0.1.0`, Python `>=3.10`, and explicit flat modules:

```text
app, audio_outputs, autostart, diagnostics, plugins.discord_output_plugin, plugins.windows11_overlay_plugin, plugins.macos_overlay_plugin, gui, ddc, settings, theme, plugin_api, plugin_hotkeys, plugin_manager, windows_platform
```

The direct runtime dependency is pinned to `monitorcontrol==4.2.0`. The `build` extra pins `Nuitka==2.4.8`. There is no lockfile; build-system requirements and transitive build dependencies are not fully pinned.

`gui.py`'s function-local import is still a direct Python import, and `plugin_manager.py` directly imports every bundled provider from `plugins`; `build_exe.ps1` also passes `--include-package=plugins`, so Nuitka embeds the bundled framework/plugins. Files discovered from adjacent `external-plugins`, per-user `%APPDATA%\fensoundswitch\plugins`, or the legacy trusted `%APPDATA%\windows-ddc\plugins` fallback are deliberately not build inputs or embedded data; the executable imports them from disk after restart.

`build_exe.ps1` resolves `python`, changes to its own repository directory, checks `app.py` and `FenSoundSwitch.ico`, then invokes Nuitka with:

- `--onefile`
- `--windows-console-mode=disable`
- `--enable-plugins=tk-inter`
- `--include-package=plugins`
- `--windows-icon-from-ico=FenSoundSwitch.ico`
- `--include-data-files=FenSoundSwitch.ico=FenSoundSwitch.ico`
- `--output-dir=dist`
- `--output-filename=FenSoundSwitch.exe`
- `--remove-output`
- `--assume-yes-for-downloads`
- entrypoint `app.py`

The runtime data copy of the icon is essential because `theme.APP_ICON_PATH` resolves a file beside `theme.py`; an embedded PE icon alone does not satisfy that lookup.

The output is ignored `dist\FenSoundSwitch.exe`. Nuitka support/toolchain downloads are accepted automatically, an existing named artifact may be overwritten, and `--remove-output` removes the intermediate build directory after output is produced. The application can create its current-user autostart value interactively. The Release workflow builds and verifies the executable for `master` pushes without uploading it; a pushed Git tag additionally publishes the executable to the matching GitHub release. The repository defines no installer, Windows service, machine-wide startup registration, or signing step.

The setuptools configuration defines no package-data rule for `FenSoundSwitch.ico`; generated `SOURCES.txt` also omits it. An ordinary wheel/non-editable source install therefore falls back to default icons at runtime. Editable source execution sees the repository file, while the Nuitka build explicitly includes it.

Ignored `fensoundswitch.egg-info\` is setuptools-generated residue, not source of truth, and can lag behind `README.md` or `pyproject.toml`. Ignored `__pycache__\` and `dist\` are also generated. None should be hand-maintained or committed.

## Background activity and absent subsystems

Display, tray, Volume-key-hook, and plugin-hotkey message pumps are event loops, while DDC, audio reconciliation, and plugin actions use background workers. There is no periodic monitor, volume, or audio-endpoint poll. The Discord configuration window polls in-memory status every 200 ms only while that modal exists. Display notifications schedule a 500 ms debounced refresh and at most three retry timers; tray Refresh and every actual write also perform discovery. Successful selected-monitor reads schedule coalesced audio reconciliation. There are no durable events, job queues, or cron-style tasks.

There is likewise no web frontend/backend split, database schema, migration command, health endpoint, readiness probe, or liveness probe. Discord OAuth and local RPC are a client integration rather than an operated backend.

## Health and failure behavior

The status bar is the immediate health surface:

- startup begins at `Searching for monitors...`;
- successful initial read reports Ready, monitor count, and selected description;
- empty discovery reports `No DDC/CI monitors found.`;
- monitor, hook, tray, and autostart-update exceptions are formatted into status text.
- inconclusive or failed sound-output matching is reported without disabling otherwise-ready monitor-volume control.

The packaged executable disables its console. `diagnostics.py` retains lifecycle, thread/component, native-subsystem, settings-save, autostart-update, DDC watchdog, refresh/write, queued-callback, shutdown, and top-level failure records in a bounded per-user log. Tray failures still restore the main window so an immediate status remains visible.

Volume-control readiness requires a live display listener, valid topology generation, unique selected identity, and confirmed volume. Key-consumption readiness additionally requires an active keyboard hook. Refresh clears readiness while it runs and restores it only after an exact match and successful read. Topology and write failures trigger bounded automatic rediscovery; external OSD/tool volume changes are not reconciled automatically.

Known failure-state caveats include:

- an in-flight native DDC call cannot be cancelled, so a topology event can race with the final pre-write generation check;
- a DDC call that never returns keeps monitor control disabled until the application is restarted; the worker stays daemonized and no concurrent replacement call is started;
- if Windows emits no notification before a first post-change press, that press can be consumed while asynchronous revalidation rejects the write; subsequent presses pass through;
- a set may succeed before its readback fails;
- duplicate restoration is a best-effort broadcast and can be missed during the primary instance's very early startup or shutdown;
- diagnostic-handler creation is deliberately nonfatal, so an unwritable log location leaves only the status bar (and standard error in source runs).
- the private endpoint-visibility interface is Windows-version dependent, and an elevated rename request can be cancelled; both failures leave DDC volume control available;
- a topology event can race with an already-entered endpoint-policy call, so a newly selected endpoint may remain hidden until the next successful revalidation makes it visible first.

## Development and testing

The standard-library test suite covers fail-safe Volume hotkeys, passive forwarding plugin shortcuts, deterministic discovery/failure isolation, Discord credential migration/OAuth helpers/output restoration, EDID parsing, stable identity/settings, Change speed, autostart and diagnostics, topology generations, display/theme routing, fresh-wrapper writes, fail-closed audio endpoint matching, strict helper dispatch, single-instance composition, tray snapshots, overlay placement/no-activate behavior, live accessibility/scaling, queue containment, DDC watchdogs, native lifecycle waits, CI safety, and shutdown diagnostics. It has no third-party test framework, linter, formatter, or type checker.

`.github/workflows/ci.yml` runs on `windows-latest` for pushes, pull requests, and manual dispatches with Python 3.10. It installs the runtime project, runs the unit suite, compiles runtime modules, checks installed dependencies, parses `build_exe.ps1` without executing it, checks tracked/staged whitespace, and requires a clean repository state. It neither starts `app.py` nor invokes hardware tools, live audio enumeration/mutation, monitor enumeration, the Nuitka build, publishing, or deployment. `.github/workflows/release.yml` builds and verifies the executable on `master`, publishing it only for pushed tags. `tests/test_ci_workflow.py` locks down both workflow contracts.

Do not use application launch as a routine smoke test. It reads and writes user settings, starts a global hook, creates native tray state, and contacts physical monitor hardware. `build_exe.ps1` also writes ignored artifacts and may download tooling.

An authorized manual Windows/hardware pass is required for changes to plugin discovery/shortcuts, Discord RPC/OAuth, monitor discovery/selection/DDC, audio endpoint policy/rename, key interception, autostart, tray lifecycle, theme/accessibility/scaling, overlay, or shutdown. Settings use temporary paths; autostart/audio/Discord tests mock registry, native, credential, and network boundaries. Pure DDC wrapper tests use fake context-manager monitor objects.

## Things to preserve

- Keep `app.py` as the single supported composition root and `main.py` as an explicit unsupported stub unless compatibility policy changes.
- Acquire and retain `SingleInstanceGuard` before creating Tk; duplicate launches must remain side-effect-free apart from the best-effort restore broadcast.
- Keep plugin imports/discovery and Credential Manager reads after the primary-instance/Tk boundary. Bundled plugins are direct package imports; trusted external plugins are auto-loaded from adjacent `external-plugins\` and per-user `plugins\`; isolate each failure.
- Keep plugin shortcut observation independent from the low-level Volume hook. Action shortcuts and routes may consume only configured held non-modifier key pairs; modifiers and unrelated input must forward. Dispatch away from the native thread, suppress held-key repeats and same-plugin overlap, and stop the observer before plugin shutdown.
- Keep Discord secrets and tokens out of JSON and diagnostics. Preserve first-consent honesty, silent reuse/refresh, concrete alternative selection, restoration in `finally`, and pipe close as fallback.
- Keep all Tk access on the main thread; use `_result_queue`, `_hotkey_delta_queue`, and `_post_to_ui()` at cross-thread boundaries.
- Route live theme broadcasts through `_post_to_ui()` and preserve debouncing, reversible light/dark chrome, Windows system colors in High Contrast, and current-window DPI reflow.
- Keep DDC work off Tk and serialize/coalesce writes. Do not start one hardware worker per key event.
- Keep audio-output work off Tk and coalesced. Preserve exact container matching, the single placeholder-container inference, selected-visible-first order, topology checks, non-display/unmatched exclusion, and one rename attempt per endpoint per process session.
- Keep the internal elevated helper before the mutex/logging boundary, strictly validate its endpoint ID, keep the alias code-fixed, and do not broaden it into a general privileged command surface.
- Keep monitorcontrol operations inside the monitor context manager and clamp all public volume results/targets to `0`–`100`.
- Preserve system key pass-through until a selected monitor has a successful volume read, and clear/pass through safely on loss of readiness.
- Keep native ctypes callbacks strongly referenced and stop display/hook/tray loops before destroying Tk.
- Keep tray state immutable and lock-protected, and keep the menu-open snapshot paired with its command IDs. Tray actions must enter Tk through `_post_to_ui()` and monitor switching must revalidate the captured stable identity.
- Do not use the displayed index or description ordinal as current persistent identity. Preserve version-2 matching and backward-compatible legacy loading.
- Never touch live per-user settings or diagnostics in automated work; use temporary paths.
- Never touch the live Run value in automated work. Preserve current-user-only opt-in writes, Windows quoting, the command-length check, source `pythonw.exe` preference, and original one-file executable path.
- Never enumerate or mutate live audio endpoint state during automated work; mock registry/COM/ShellExecute boundaries. Manual audio validation must identify the exact endpoints and preserve unrelated outputs.
- Treat physical monitor volume and global keyboard handling as safety-sensitive side effects.
- Keep `[tool.setuptools].py-modules` synchronized when distributable runtime modules are added, renamed, or removed.
- Keep `FenSoundSwitch.ico`, `theme.APP_ICON_PATH`, and both Nuitka icon flags synchronized.
- Do not treat generated egg-info, `dist\`, or `__pycache__\` as authoritative source.
- Do not document APIs, services, authentication, databases, health probes, hotplug polling, machine-wide startup, signing, or deployment automation unless they are actually implemented.
