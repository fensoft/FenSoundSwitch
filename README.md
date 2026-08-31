# FenSoundSwitch

## One set of volume keys. The sound you actually want.

`FenSoundSwitch` turns the Windows Volume Up and Volume Down keys into a simple remote for your monitor, AV receiver, or both.

Build named routes for the rooms and devices you use. Choose a monitor or receiver for each route, then adjust them together from your keyboard. A tray-first interface, configurable overlays, and optional Windows or Discord device switching keep control close without adding clutter.

![FenSoundSwitch control window](docs/app.png)

## Why FenSoundSwitch

- Use Windows volume keys for a monitor, receiver, or several devices at once.
- Create clearly named routes such as `Desk monitor`, `Living room`, or `Movie night`.
- Control compatible displays and popular network receivers.
- Control a route from MQTT, with Home Assistant MQTT discovery buttons.
- See a clean Windows 11 or macOS-style volume overlay.
- Keep the app quietly available in the notification area.
- Configure separate shortcuts for Windows playback, voice output, input, and microphone switching, or optional Discord output switching.
- Keep the Windows default playback and/or voice output active by rendering silence, continuously or after recent mouse movement.
- Start automatically with Windows.
- Export and restore complete non-secret configuration archives.

## Get Started

1. Download `FenSoundSwitch.exe` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases).
2. Run it and open the window from the notification area if needed.
3. Add a route, give it a name, select **Windows Volume keys** as the input, and choose an output.
4. Configure the output, then press Volume Up or Volume Down at a safe listening level.

The app is made for Windows 10 and Windows 11. Compatible monitor control requires DDC/CI enabled in the monitor menu. Receiver control requires a supported receiver on your home network.

## Default Configuration

On first launch, FenSoundSwitch creates `%APPDATA%\fensoundswitch\configurations\default.fsc` from its bundled `default.py` definition. The **Default** button imports this ready-to-use Windows audio configuration:

- Volume Up and Volume Down control the default output route named **Output**.
- `Ctrl + Alt + F9` and `Ctrl + Alt + F10` control the default voice-output route named **Voice**.
- The Discord action is disabled.
- Audio keep-alive is enabled for both the default playback and voice outputs. It remains active while the mouse is moving and for 60 seconds afterward.

Default device shortcuts:

- To cycle playback devices, press `Ctrl + Alt + F11`.
- To cycle recording devices, press `Ctrl + Alt + F7`.

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
