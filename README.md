# windows-ddc: Windows Monitor And AVR Volume Control

`windows-ddc` is a Windows desktop application for controlling the volume of a DDC/CI monitor or compatible network AV receiver with the slider, tray icon, and global Volume Up/Down keys.

It supports DDC/CI monitors plus configurable main-zone Ethernet control for Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony receivers. It does not change the Windows audio mixer, application volume, brightness, or mute state.

![windows-ddc control window](docs/app.png)

## Features

- Control one selected DDC/CI monitor or network AV receiver volume provider.
- Route the host-owned global Windows Volume Up/Down input to a configured ready volume provider.
- Configure one or more ordered route instances. Each route has a user-defined name and independent input and output settings, so the same provider can control multiple isolated receivers or monitors. The route editor shows configured endpoint summaries and opens endpoint-specific Configure input/output dialogs: Windows Volume has no settings, DDC routes select a monitor, and network receivers use Host/IP-address and TCP-port fields. Its Overlay section selects either the Windows 11 overlay or macOS-style overlay and opens typed renderer settings. Saving an endpoint dialog changes only the route draft until the route itself is saved. A Windows Volume Up/Down event broadcasts its delta sequentially to every output routed from that input, and the selected overlay reports routed results by route name.
- Use tray controls for status, Refresh, Restore, and Exit.
- Configure trusted action plugins and volume-provider routes directly in the main window.
- Select one normalized `0`–`100` volume provider, including monitor DDC/CI and supported network receiver protocols.
- Use passive configurable plugin shortcuts that listen globally without blocking foreground application input.
- Optionally route display audio safely for the selected DDC monitor.
- Use Start with Windows, live light/dark/High Contrast themes, keyboard navigation, and DPI-aware layout.

> [!IMPORTANT]
> When a provider is ready, the application consumes Volume Up and Volume Down globally. They no longer change Windows system volume until the app exits or becomes unavailable. Mute is never intercepted.

## Quick Start

### Windows executable

Download `windows-ddc.exe` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases), place it in a user-controlled folder, and run it. The executable is standalone and has no installer.

### Run from source

```powershell
git clone https://github.com/fensoft/windows-ddc.git
Set-Location windows-ddc
python -m pip install -e .
python app.py
```

Requirements: Windows 10 or 11, Python 3.10+ with Tkinter for source runs, and either a DDC/CI-capable monitor or a compatible configured network receiver. Enable DDC/CI in the monitor OSD before using monitor volume.

## Documentation

- [User guide](docs/USER_GUIDE.md): installation, first run, tray, keyboard, providers, and everyday use.
- [Configuration and security](docs/CONFIGURATION.md): settings, plugins, audio routing, autostart, logs, backups, and safety boundaries.
- [Troubleshooting](docs/TROUBLESHOOTING.md): common monitor, tray, volume-key, receiver, Discord, and build issues.
- [Development](docs/DEVELOPMENT.md): build, tests, CI, and manual validation.
- [Architecture](docs/ARCHITECTURE.md): process, thread, plugin, and platform design.
- [Changelog](CHANGELOG.md): release history.

## External Plugins

Bundled first-party plugins are Python modules in the installed `plugins` package and are imported directly by the application. To add trusted external plugins beside a source checkout or packaged executable, place `*.py` files in `external-plugins\`; per-user external plugins remain in `%APPDATA%\windows-ddc\plugins\`. Both locations execute trusted code in-process when the primary application starts.

## Supported Network Receiver Protocols

| Brand family | Main-zone protocol | Default port |
| --- | --- | --- |
| Onkyo / Integra | eISCP | `60128` |
| Denon / Marantz | AVR `MV` | `23` |
| Yamaha | YNCA | `50000` |
| Pioneer / Elite | IP control | `8102` |
| Sony | JSON-RPC | `10000` |

Network providers use user-configured outbound LAN connections only. They do not discover devices, listen for connections, or poll. Confirm compatibility with the specific receiver model before relying on it.

## Safety

`windows-ddc` is one interactive user-session application, not a Windows service or server. It uses fail-closed provider readiness, serialized volume operations, and one instance per Windows session. DDC and receiver commands can change physical device volume; test at a safe level.
