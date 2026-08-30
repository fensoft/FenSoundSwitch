# windows-ddc

## One set of volume keys. The sound you actually want.

`windows-ddc` turns the Windows Volume Up and Volume Down keys into a simple remote for your monitor, AV receiver, or both.

Build named routes for the rooms and devices you use. Choose a monitor or receiver for each route, then adjust them together from your keyboard. A tray-first interface, configurable overlays, and optional Discord output switching keep control close without adding clutter.

![windows-ddc control window](docs/app.png)

## Why windows-ddc

- Use Windows volume keys for a monitor, receiver, or several devices at once.
- Create clearly named routes such as `Desk monitor`, `Living room`, or `Movie night`.
- Control compatible displays and popular network receivers.
- See a clean Windows 11 or macOS-style volume overlay.
- Keep the app quietly available in the notification area.
- Configure optional Discord output switching and shortcuts.
- Start automatically with Windows.

## Get Started

1. Download `windows-ddc.exe` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases).
2. Run it and open the window from the notification area if needed.
3. Add a route, give it a name, select **Windows Volume keys** as the input, and choose an output.
4. Configure the output, then press Volume Up or Volume Down at a safe listening level.

The app is made for Windows 10 and Windows 11. Compatible monitor control requires DDC/CI enabled in the monitor menu. Receiver control requires a supported receiver on your home network.

> [!IMPORTANT]
> Once a route is ready, Volume Up and Volume Down control your configured route instead of Windows system volume. Mute is never intercepted.

## Simple Manual

- [Complete user manual](docs/MANUAL.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [What changed](CHANGELOG.md)

## For Developers

- [Build instructions](BUILD.md)
- [Technical documentation](docs/TECHNICAL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Repository rules](AGENTS.md)
