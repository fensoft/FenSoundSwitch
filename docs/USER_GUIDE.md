# User Guide

## Requirements

- Windows 10 or Windows 11.
- A monitor with DDC/CI enabled and DDC/CI audio-volume support, or a configured network receiver provider.
- Python 3.10+ with Tkinter when running from source.

Run the app in the interactive Windows session whose volume keys and devices it should control. The app normally runs unelevated. Windows requests approval only for the one-time selected-output **FenSound** rename; denying it does not disable volume control.

## Install

Download a release executable from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases), place it in a user-controlled folder, and run it. The executable is standalone and has no installer or signing automation.

To run the current sources:

```powershell
git clone https://github.com/fensoft/windows-ddc.git
Set-Location windows-ddc
python -m pip install -e .
python app.py
```

`app.py` is the supported source entrypoint. `main.py` intentionally exits with migration guidance.

## First Run

1. Enable DDC/CI in the monitor OSD if using monitor volume.
2. Start the executable or `python app.py`.
3. Find the notification-area icon, including its overflow menu. Double-click it to restore the control window.
4. Use the main-window **Routes** section to configure the provider to use when not using the default DDC monitor provider.
5. Choose **Use for main volume**, assign each input to its output as needed, and wait for a successful volume read.
6. Test the slider or buttons at a safe listening level before relying on global Volume Up/Down keys.

The Discord plugin is optional. Its first setup requires a Discord Application ID, a newly reset client secret, the exact `https://127.0.0.1` redirect, required Discord RPC scopes, and interactive consent. Cancel its setup when it is not wanted.

## Everyday Use

| Action | Behavior |
| --- | --- |
| Change volume | Use the slider, buttons, or global Volume Up/Down keys. Slow, Medium, and Fast change by `1`, `2`, and `3`. |
| Volume overlay | In the main-window **Routes** section, choose the current provider only or all routed provider rows. Routed key input identifies its target provider. |
| Keyboard | Use `Tab` and `Shift+Tab` to navigate the embedded Routes and Action plugins sections. `Escape` minimizes to the tray. |
| Tray | Right-click for status, Refresh, monitor switching, Restore, and Exit. |
| Minimize | Minimizing returns the app to the notification area after confirmed tray-icon addition. |
| Close | Closing a restored window exits. Use **Exit** from the tray while minimized. |

Once a provider is ready, Windows Volume Up/Down are consumed globally and no longer change the Windows system mixer. They pass through while the app is unavailable or after exit. Mute is never intercepted.

Network receiver providers require a manually configured host/IP and port. They make bounded outbound LAN requests only: Onkyo/Integra eISCP (`60128`), Denon/Marantz `MV` (`23`), Yamaha YNCA (`50000`), Pioneer/Elite IP control (`8102`), and Sony JSON-RPC (`10000`). They do not discover devices or poll. Confirm compatibility with the exact receiver model before relying on it.

Plugins can provide action configuration and non-secret settings in the main-window **Action plugins** section, while volume providers and input routing are configured in **Routes**. Plugin shortcuts listen for their configured key combination while still forwarding the original key to the foreground application.

## Behavior Notes

The app is tray-first, maintains one instance per Windows session, and fails closed when monitor identity, display topology, or provider communication cannot be safely verified. DDC monitor reads and writes are not transactional; a failed post-write readback can still mean the monitor changed. The volume overlay never takes focus and follows the cursor display, respecting Windows work areas and scaling.

For persistence, plugins, autostart, audio routing, and security details, see [Configuration](CONFIGURATION.md). For common problems, see [Troubleshooting](TROUBLESHOOTING.md).
