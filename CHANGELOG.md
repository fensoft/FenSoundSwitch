# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

No changes yet.

## [1.3.0] - 2026-11-15

### Added

- Added reusable named AVR profiles under Integrations for Denon/Marantz, Onkyo/Integra, Pioneer/Elite, Sony, and Yamaha receivers. Multiple profiles of the same type can coexist, and routes reference a shared profile.
- Added AVR automation actions for selecting an input and turning a configured receiver on or off.
- Added Windows application-session volume and mute automation with exact executable matching, executable icons, extension-free labels, and background discovery refreshed whenever configuration opens.
- Added separate keyboard key-press and key-release automation events, including modifier-only capture such as Ctrl, with per-event conflict detection and dispatch.
- Added route-scoped passive keyboard input and Windows render-soundcard master-volume plugins.
- Added generic HTTP JSON volume, DDC brightness/contrast, bounded HTTP, Windows power-plan, and Windows default-device automation actions.
- Added raw and Home Assistant-compatible MQTT publishing, reusable broker profiles, MQTT route input, retained discovery, and exact Home Assistant button dispatch.
- Added DDC monitor-input automation with background discovery, stable monitor/input configuration, advertised-input validation, serialized access, and verified readback.
- Added host-owned automations triggered by app start, keyboard, tray, and MQTT/Home Assistant, with ordered action/wait steps, repeated actions, overlap suppression, and failure handling.
- Added configuration export/import and default `.fsc` archives, including recent-backup selection and restart confirmation.
- Added configurable audio output keep-alive and Windows default-device cycling plugins.
- Added categorized and deterministic ordering to the automation action chooser.

### Changed

- Moved AVR connection configuration into Integrations. Routes now select profiles, while AVR input and power changes are performed by automation actions.
- Kept Onkyo/Integra network connections available across rapid volume writes to improve held-volume responsiveness.
- Added persisted plugin enable/disable controls and forwarding policies for plugin shortcuts and keyboard routes.
- Added Windows 11 and macOS-style overlay renderers with persisted settings, route-type icons, DPI-aware placement, High Contrast support, and no-activate behavior.
- Improved automation summaries by removing unnecessary parentheses, accenting configured details, and avoiding empty icon spacing.
- Added persisted route names and informational route types, with safe migration of older settings.
- Improved the WebView2 command center, declarative plugin UI, accessibility, translations, configuration layout, and responsive behavior.

### Fixed

- Fixed modifier-only shortcut capture, key-release dispatch, duplicate hotkey conflicts, and held-key repeat handling.
- Fixed application-session discovery and configuration refresh so stale audio-session lists are not reused.
- Fixed rapid AVR volume updates and route/profile reconfiguration so shared settings reach existing route instances.

## [1.2.0] - 2026-09-07

### Added

- Added process-local UI localization with automatic Windows-language detection and English, German, Spanish, French, and Italian catalogs.
- Expanded plugin capabilities with native mute, richer route metadata, action-plugin state, improved UI validation, and broader hardware-free coverage.
- Added configuration, diagnostics, accessibility, and presentation translations for the WebView2 interface.

## [1.1.0] - 2026-09-07

### Added

- Added the Windows Bluetooth volume plugin for paired Bluetooth devices whose audio endpoints are active.
- Added stable Bluetooth device and endpoint matching with fail-closed resolution and hardware-free tests.

## [1.0.1] - 2026-09-04

### Changed

- Updated the Nuitka build tooling and disabled payload compression for the packaged application.

## [1.0.0] - 2026-09-04

### Added

- Added a versioned runtime plugin framework with deterministic bundled, adjacent, and per-user discovery, isolated failures, plugin-owned configuration, and action-only plugin management.
- Added independent volume routes with stable route IDs, multiple providers, route-scoped keyboard input, and serialized provider writes.
- Added bundled network AVR providers for Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony.
- Added Discord output switching with OAuth, Credential Manager persistence, token reuse/refresh, one-second switching/restoration, and safe shutdown behavior.
- Added Windows soundcard volume, passive plugin shortcuts, audio output management, configurable overlays, diagnostics, autostart, tray recovery, and single-instance protection.
- Added EDID/device-path monitor identity, display/device notifications, fail-closed audio-output matching, elevated FenSound endpoint renaming, and DDC revalidation.
- Added a local WebView2 presentation child using authenticated named-pipe IPC while the primary process retains hardware, tray, hook, worker, and credential boundaries.
- Added a native WiX MSI standalone build, tag-derived versions, release automation, Windows CI, and expanded user/developer documentation.

### Changed

- Redesigned the interface around the Fluent-inspired Windows 11 command center with Routes, Automations, Integrations, Appearance, Settings, and Diagnostics.
- Replaced direct Discord and legacy shortcut configuration with signal-based host-owned automations.
- Added live theme, High Contrast, DPI, keyboard accessibility, overlay focus safety, tray acknowledgement, bounded native lifecycle waits, and resilient queue polling.

## [0.1.0] - 2026-03-22

### Added

- Added DDC/CI monitor discovery, stable selection, volume reads, clamped `0`–`100` writes, and hardware readback.
- Added a Tkinter volume control window with status display, volume keys, tray-first operation, restore/minimize behavior, and a translucent volume overlay.
- Added per-user monitor settings, Windows light/dark theme detection, a shared application/tray icon, native Win32 tray integration, and a low-level keyboard hook.
- Added background DDC reads, serialized/coalesced rapid writes, Python 3.10+ packaging metadata, and a console-free Nuitka build.
- Added the supported `app.py` launcher, the rejecting `main.py` stub, setup/build documentation, screenshots, and the published `FenSoundSwitch.exe` release asset.

[Unreleased]: https://github.com/fensoft/windows-ddc/compare/1.3.0...HEAD
[1.3.0]: https://github.com/fensoft/windows-ddc/compare/1.2.0...1.3.0
[1.2.0]: https://github.com/fensoft/windows-ddc/compare/1.1.0...1.2.0
[1.1.0]: https://github.com/fensoft/windows-ddc/compare/1.0.1...1.1.0
[1.0.1]: https://github.com/fensoft/windows-ddc/compare/1.0.0...1.0.1
[1.0.0]: https://github.com/fensoft/windows-ddc/compare/0.1.0...1.0.0
[0.1.0]: https://github.com/fensoft/windows-ddc/releases/tag/0.1.0
