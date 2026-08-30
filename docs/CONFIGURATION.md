# Configuration And Security

## Settings

Main settings are stored at `%APPDATA%\windows-ddc\settings.json`, or `<home>\windows-ddc\settings.json` when `APPDATA` is unavailable. They persist the selected provider, input routes, Change speed, and monitor identity. Settings writes replace a sibling temporary file atomically. The active overlay and its non-secret typed settings are instead plugin-owned files under `%APPDATA%\windows-ddc\plugin-settings`; older global `overlay_mode` values migrate to the Windows 11 overlay.

The app prefers EDID manufacturer/product/serial identity and uses the Windows device path as a fallback. Missing or ambiguous saved identity fails closed rather than selecting another monitor.

Plugin configuration is non-secret JSON at `%APPDATA%\windows-ddc\plugin-settings\<plugin-id>.json`. Network providers store only their host and port there. Discord OAuth client data and tokens are stored only in current-user Windows Credential Manager.

## Providers And Plugins

Bundled providers are DDC monitor volume, Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony network receiver main-zone volume. Use the main-window **Routes** section to configure providers and assign each input to an output. The list uses friendly input and provider names; an input can also remain **Not assigned**.

The host owns the volume overlay. Its **Volume overlay** option in the main-window **Routes** section shows either the current provider or every routed provider with a confirmed volume; unavailable entries remain explicitly unavailable. Plugins receive only immutable host-published volume-status snapshots and cannot use this API to read Tk state or hardware.

Action plugins, including Discord, are configured from the main-window **Action plugins** section, which also configures their shortcuts. Bundled first-party modules live in the installed `plugins` package and are imported directly, never dynamically scanned. External Python plugins load from `external-plugins` next to the source tree or executable, then `%APPDATA%\windows-ddc\plugins`. They are trusted, unsandboxed in-process code and take effect after restart. Review external plugin source before placing it in either external folder.

## Audio Routing

The DDC provider can match the selected monitor to a Windows render endpoint. It makes the selected output visible as **FenSound** and hides only outputs positively matched to other connected screens. Ambiguous matches, headphones, speakers, and unrelated endpoints are untouched. These endpoint changes persist after exit.

## Start With Windows And Logs

**Start with Windows** is available in the main-window **Routes** section and manages only `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\windows-ddc`. It is current-user only and requires no elevation. Source launches prefer `pythonw.exe`; commands over 260 characters are rejected.

The rotating diagnostic log is `%LOCALAPPDATA%\windows-ddc\windows-ddc.log`, falling back to `APPDATA` and then home. It retains two 512 KiB backups. Inspect logs before sharing because unexpected exceptions can include local paths.

## Backup

Exit every application instance, then copy `%APPDATA%\windows-ddc\settings.json` to a user-controlled location. Restore it only while the app is stopped. Do not commit backups, credentials, tokens, or machine-specific plugin settings.

## Security Boundaries

`windows-ddc` has no supported API server, listener, database, account system, or application CLI. The process reads Windows monitor/audio metadata and can alter matched display-audio endpoint visibility. DDC and receiver writes can change external hardware volume. Network providers use configured, unauthenticated plaintext LAN protocols; configure only trusted local devices.
