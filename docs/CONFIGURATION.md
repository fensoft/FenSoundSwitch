# Configuration And Security

## Settings

Main settings are stored at `%APPDATA%\fensoundswitch\settings.json`, or `<home>\fensoundswitch\settings.json` when `APPDATA` is unavailable. They persist the selected provider, input routes, Change speed, and monitor identity. Settings writes replace a sibling temporary file atomically. The active overlay and its non-secret typed settings are instead plugin-owned files under `%APPDATA%\fensoundswitch\plugin-settings`; older global `overlay_mode` values migrate to the Windows 11 overlay.

The app prefers EDID manufacturer/product/serial identity and uses the Windows device path as a fallback. Missing or ambiguous saved identity fails closed rather than selecting another monitor.

Plugin configuration is JSON at `%APPDATA%\fensoundswitch\plugin-settings\<plugin-id>.json`. MQTT profiles in that directory can contain broker usernames and passwords. Each DDC monitor-input automation step stores its stable monitor identity, input value, and display labels in that step's parameters in `settings.json`; it never stores the transient monitor index. Route-specific network receiver parameters are also stored with each route in `settings.json`; these include host, port, optional startup power, and optional startup input. Discord OAuth client data and tokens are stored only in current-user Windows Credential Manager.

## Providers And Plugins

Bundled providers are DDC monitor volume, Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony network receiver main-zone volume. Use the main-window **Routes** section to configure providers and assign each input to an output. The list uses friendly input and provider names; an input can also remain **Not assigned**.

Receiver startup actions are disabled by default. When enabled, a route instance sends bounded outbound power/input commands once during its first worker-thread activation, before its initial volume probe. The input menus are protocol-wide supersets drawn from known models; an individual receiver may implement only part of its protocol's menu.

The host owns the volume overlay. Its **Volume overlay** controls in the HTML **Routes** and **Appearance** pages show either the current provider or every routed provider with a confirmed volume; unavailable entries remain explicitly unavailable. Plugins receive only immutable host-published volume-status snapshots and cannot use the web presentation API to read Tk state or hardware.

DDC monitor input is configured directly on each automation step; opening that editor starts background discovery and exposes its own refresh control. The main-window **Integrations** tab owns reusable named MQTT/Home Assistant broker configurations and setup for integrations such as Discord. Each MQTT route or automation trigger selects a shared profile and supplies its own Home Assistant name and ID. DDC monitor input, Discord, and Windows device switching are exposed as automation steps rather than direct actions; app-start, keyboard, tray, and MQTT/HA triggers belong to the containing automation. The DDC input action freshly resolves its saved monitor identity and revalidates the selected input against the monitor's advertised capabilities before every write. Bundled first-party modules live in the installed `plugins` package and are imported directly, never dynamically scanned. External Python plugins load from `external-plugins` next to the source tree or executable, then `%APPDATA%\fensoundswitch\plugins`. They are trusted, unsandboxed in-process code and take effect after restart. `%APPDATA%\windows-ddc\plugins` remains a final read-only, trusted compatibility location; move files to the new folder before changing them. Review external plugin source before placing it in either external folder.

## Audio Routing

The DDC provider can match the selected monitor to a Windows render endpoint. It makes the selected output visible as **FenSound** and hides only outputs positively matched to other connected screens. Ambiguous matches, headphones, speakers, and unrelated endpoints are untouched. These endpoint changes persist after exit.

## Start With Windows And Logs

**Start with Windows** is available in the main-window **Routes** section and manages `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FenSoundSwitch`. It is current-user only and requires no elevation. A legacy `windows-ddc` value is read for compatibility; an explicit checkbox change writes the new value and removes the legacy one. Source launches prefer `pythonw.exe`; commands over 260 characters are rejected.

The rotating diagnostic log is `%LOCALAPPDATA%\fensoundswitch\fensoundswitch.log`, falling back to `APPDATA` and then home. It retains two 512 KiB backups. Inspect logs before sharing because unexpected exceptions can include local paths.

## Backup

Use the main-window **Export** button to choose where to save a `.fsc` archive; it opens in `%APPDATA%\fensoundswitch\configurations` with a timestamped name by default. Every export is also recorded in that directory for the latest-import history. An archive contains `settings.json` and every JSON file from `plugin-settings`, including MQTT profile credentials, so protect exported files. It excludes Discord Credential Manager OAuth configuration and tokens, executable plugins, diagnostic logs, Windows audio endpoint state, and hardware volume.

Use **Import** to select an archive. Its down-arrow button displays the five newest exported archives; select one to import it. On first primary launch, bundled `default.py` creates `%APPDATA%\fensoundswitch\configurations\default.fsc` without overwriting an existing file. **Default** imports that generated archive. Every import displays a restart warning and requires confirmation, then replaces saved main and plugin settings and restarts the application. Do not import untrusted archives or commit archives containing machine-specific settings.

FenSoundSwitch performs a one-way settings migration when the new settings file is absent: it validates and copies `%APPDATA%\windows-ddc\settings.json` to the new namespace without modifying the legacy file. It similarly copies valid legacy plugin JSON and Discord credentials only when no current value exists.

## Security Boundaries

`FenSoundSwitch` has no supported API server, listener, database, account system, or application CLI. The process reads Windows monitor/audio metadata and can alter matched display-audio endpoint visibility. DDC and receiver writes can change external hardware power, input, and volume. Network providers use configured, unauthenticated plaintext LAN protocols; configure only trusted local devices.
