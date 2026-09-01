# Changelog

- Embedded **Routes** and action-only **Plugins** lists in the main window, removing its Refresh and configuration launch buttons while retaining tray Refresh for status rediscovery.

- Added the generic runtime-plugin API and optional normalized volume-provider capability. The bundled DDC monitor implementation is now a provider selected through **Routes**; provider failures fail closed and do not silently reroute volume keys.

All notable changes to this project are documented in this file. The reconstruction audit covered the complete reachable repository history through `0b4f263`, all local and remote-tracking branches, and the live origin heads and tags before the documentation changes listed under Unreleased.

That audited history contains one parentless commit. The audit clone had no local tag ref, but origin advertised a lightweight `0.1.0` tag at the same commit as the then-current `master` and `origin/master`. Because a lightweight tag has no independent tagger timestamp, the version date below uses the tagged commit's committer date; the GitHub release was published shortly afterward on the same calendar date. There is no earlier release boundary or separate untagged development period to reconstruct.

## [Unreleased]

### Added

- Added main-window configuration export/import controls. Exports bundle main and plugin JSON settings into `.fsc` archives; every export is recorded in import history, bundled `default.py` creates the user default archive, Import's arrow lists the five latest exports, and Default restores `default.fsc` after restart confirmation.
- Added a bundled configurable audio output keep-alive plugin that renders silence to the current Windows default playback and/or voice output continuously or following recent mouse movement.
- Added persisted Enable/Disable controls for action plugins; disabled plugins do not initialize or register shortcuts, and unconfigured shortcut cells are blank.
- Added a bundled MQTT route input with retained Home Assistant MQTT configurable 0-to-100 slider discovery for per-route volume commands.
- Added a persisted **Forward keys to other applications** policy to every action-plugin shortcut. Existing action bindings migrate to forwarding; disabled forwarding consumes only the configured held key pair.
- Added a bundled Windows default-device plugin with independent shortcuts for active playback, voice output, input, and microphone devices. Each shortcut cycles only its corresponding Windows default roles and needs no plugin configuration dialog.
- Added a typed per-keyboard-route **Forward keys to other applications** setting. Existing keyboard routes migrate to forwarding; routes that disable it consume only their configured held key pairs, while action-plugin shortcuts remain passive.
- Added `BUILD.md`, a complete user manual, and a technical documentation index while simplifying the root README into a product overview and brief setup guide.
- Added selectable bundled Windows 11 and macOS-style overlay renderers. Overlay selection and renderer settings are persisted under plugin settings; the Windows 11 renderer safely migrates the former global `overlay_mode` setting and retains typed current/all configuration.
- Added persisted, validated route names to route configuration, status reporting, and all overlay rows; schema-v6 routes receive deterministic endpoint-based names on their next save.
- Moved the bundled volume overlay into the `plugins` package as the renderer-only `volume-overlay` plugin capability. The plugin is initialized by `PluginManager`, constructed on the host Tk thread after manager startup, and remains absent from action and route configuration.
- Added a logical Windows Volume Up/Down input plugin with per-input volume-provider routing, persisted safely in the main settings file while the host retains the only native Volume-key hook and serialized write boundary.
- Added immutable host-published multi-provider volume-status snapshots for plugins and a configurable host-owned overlay that can show the current provider or all routed providers.
- Added host-owned named plugin shortcut actions with isolated manager persistence, generic capture/configuration, conflict handling, and legacy API-v1 shortcut compatibility.
- Split operational documentation into focused user-guide, configuration, troubleshooting, and development pages while simplifying the root project overview.
- Added a release workflow that verifies a build on `master` and publishes `FenSoundSwitch.exe` only for pushed Git tags.
- Added a configurable bundled Onkyo/Integra eISCP main-zone volume provider with bounded TCP transport and hardware-free protocol tests.
- Added configurable bundled main-zone volume providers for Denon/Marantz, Yamaha, Pioneer/Elite, and Sony network receivers with isolated protocol tests.
- Reduced GitHub Actions CI to Python 3.10 and made the autostart command test independent of Windows short-path normalization.
- Added keyboard mnemonics, refresh shortcuts, slider boundary/page navigation, focusable controls, and descriptive volume button labels.
- Added release-history, architecture, and repository-agent documentation.
- Added hardware-free unit coverage for hook liveness, readiness loss, write failures, shutdown, and paired volume-key events.
- Added EDID/device-path monitor identity, display/device change notifications, error overlays, and hardware-free identity/settings/revalidation coverage.
- Added a persistent **Change speed** selector with Slow (`+1`), Medium (`+2`), and Fast (`+3`) choices for the GUI buttons and global volume keys.
- Added hardware-free tray acknowledgement, fallback, timeout, and Explorer-restart recovery coverage.
- Added hardware-free coverage for queued-callback containment, DDC watchdog behavior, bounded native-thread lifecycle waits, and shutdown diagnostics.
- Added a session-scoped Windows single-instance guard with hardware-free mutex, composition-root, and duplicate-restore coverage.
- Added Windows GitHub Actions CI across Python 3.10 and 3.14 for the hardware-free unit suite and all low-risk repository checks.
- Added bounded per-user rotating diagnostics for lifecycle, settings, native-subsystem, DDC, UI-callback, and shutdown failures, with isolated hardware-free coverage.
- Added an opt-in **Start with Windows** checkbox backed by the current-user Run key, with safe source/one-file command quoting, nonfatal error handling, and mocked registry coverage.
- Added a live tray menu with active monitor, confirmed volume, routing state, Refresh, stable-identity monitor switching, Restore, and Exit actions.
- Added fail-closed Windows sound-output matching that exposes the selected monitor as **FenSound**, hides only outputs mapped to other connected screens, and leaves unrelated audio devices untouched.
- Added an administrator-approved, fixed-purpose Core Audio rename helper plus hardware-free matching, topology, mutation-order, and composition-root coverage.
- Added a versioned, trusted in-process Python plugin framework with deterministic bundled/adjacent/per-user discovery, isolated failures, plugin-owned configuration, and a **Plugins** window.
- Added a shared passive keyboard observer for plugin shortcuts, with duplicate-plugin conflict reporting, live rebinding, foreground-key forwarding, held-key repeat suppression, off-native-thread dispatch, overlap suppression, and bounded shutdown.
- Added a bundled Discord output plugin with app-owned OAuth setup, current-user Credential Manager persistence and prototype migration, silent token reuse/refresh, automatic one-second output switching/restoration, and hardware-free coverage.

### Changed

- Replaced the user-facing Tk interface and plugin UI API v3 with a local WebView2 HTML application and declarative plugin UI API v4. The primary process retains hardware, tray, hook, worker, and overlay ownership; the presentation child uses authenticated Windows named-pipe IPC, opens no TCP port, loads no remote UI assets, and keeps Discord secrets write-only and Credential Manager-only.
- Redesigned the complete Tk interface around a refined Windows 11 visual system matching the approved Fluent Command Center concept: the persistent sidebar now exposes Routes, Actions, Appearance, Settings, and Diagnostics; route and plugin tables are replaced by selectable status cards; live route values, summary cards, quick settings, overlay preview, configuration management, dialogs, and the Windows 11 overlay share one surface and accent system while retaining light, dark, High Contrast, DPI, keyboard, tray, and no-activate behavior.
- Moved bundled first-party plugins into the `plugins` package and reserved the adjacent `external-plugins\` directory for dynamically discovered trusted external plugins; `%APPDATA%\fensoundswitch\plugins\` is the per-user external location.
- Replaced the single active volume provider and root volume slider with ordered independent routes. Schema version 5 persists stable route IDs and migrates schema-4 input maps; one input may fan out to several outputs while writes remain serialized.
- Split configuration into **Plugins** for initialized action-only plugins and **Routes** for volume-provider setup, input assignment, overlay configuration, and Start with Windows.
- Moved Discord output switching to the host-configured `switch-output` shortcut action and removed Discord-local shortcut storage and key capture UI.
- Apply Windows light/dark, system-color, and High Contrast changes live, and reflow the control window at its current DPI.
- Expanded user and operator documentation without changing runtime behavior.
- Place the volume/error overlay on the cursor's DPI-scaled Windows work area, fall back to the selected display when needed, and enforce native no-activate presentation.
- Made global Volume Down/Up interception require a live native hook and release subsequent presses after an uncertain DDC write result until Refresh succeeds.
- Kept the consume/pass-through decision stable from the first key-down through the matching key-up.
- Replaced description/ordinal persistence with backward-compatible schema-version-2 stable identity matching; missing or ambiguous targets now fail closed instead of selecting another monitor.
- Reacquire and exact-match fresh monitor wrappers before every actual DDC write, reject stale topology generations, and automatically rediscover after display changes or uncertain writes.
- Wait for confirmed tray-icon addition before withdrawing Tk, restore the main window on tray failures, and re-add visible icons after Explorer recreates the taskbar.
- Bound native listener startup and shutdown waits, keep Tk queue polling alive after individual callback failures, and report native threads that miss the shutdown deadline.
- Disable monitor control after a 10-second DDC watchdog timeout, retain the single-worker serialization slot until the native call returns, ignore its late result, and then perform read-only rediscovery.
- Reject duplicate launches before Tk or native initialization and ask the existing tray instance to restore its control window.
- Keep plugin imports, discovery, and credential access beyond the single-instance/Tk boundary so duplicate launches remain side-effect-free.

## [0.1.0] - 2026-03-22

### Added

- DDC/CI monitor discovery and a refreshable selector, including ordinal disambiguation for monitors that report the same description.
- Audio-volume reads and clamped `0`–`100` writes with immediate hardware readback.
- A fixed-size Tkinter control window with a volume slider, percentage display, status bar, and one-point decrement/increment controls.
- Global Windows Volume Down and Volume Up interception that redirects ready-state key presses to the selected monitor while passing keys through before readiness.
- A topmost, translucent on-screen volume overlay that automatically hides after 1.4 seconds.
- Tray-first operation with startup minimization, double-click/menu restore, minimize-to-tray behavior, and an Exit action.
- Per-user selected-monitor persistence in `%APPDATA%\fensoundswitch\settings.json`, with a home-directory fallback and temporary-file replacement.
- Windows light/dark application-theme detection, dark DWM title-bar support, and a shared application/tray icon.
- Background DDC/CI reads and serialized, coalesced rapid volume writes so blocking hardware access does not run on Tk's UI thread.
- Native Win32 tray and low-level keyboard-hook integration through `ctypes`.
- Python 3.10+ setuptools metadata with pinned `monitorcontrol==4.2.0` runtime and `Nuitka==2.4.8` build dependencies.
- A one-file, console-free Nuitka build for `dist\FenSoundSwitch.exe`, including the Tk plugin and icon as both executable metadata and runtime data.
- The supported `app.py` launcher, an explicit exit-1 rejection stub in `main.py` that directs users to `app.py`, setup/build documentation, and tracked UI screenshots.
- A published `FenSoundSwitch.exe` asset on the GitHub `0.1.0` release.

[Unreleased]: https://github.com/fensoft/windows-ddc/compare/0.1.0...HEAD
[0.1.0]: https://github.com/fensoft/windows-ddc/releases/tag/0.1.0
# Unreleased

- Added route-scoped passive keyboard input and Windows render-soundcard master-volume bundled plugins.
