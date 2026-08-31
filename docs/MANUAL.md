# FenSoundSwitch Manual

## Install

Download `FenSoundSwitch.exe` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases), put it in a folder you control, and run it. The app starts in the notification area. Double-click its icon to open the main window.

## Create Your First Route

A route connects an input to one or more outputs. The normal input is **Windows Volume keys**.

1. Open the app window from the notification area.
2. In **Routes**, select **Add route**.
3. Enter a useful route name, such as `Desk monitor` or `Living room`.
4. Select **Windows Volume keys** as the input.
5. Select the output you want to control.
6. Select **Configure output...**.
7. For a monitor, select the monitor from the list. For a receiver, enter its address and port.
8. Select **OK** to save the output settings, then **OK** again to save the route.
9. Press Volume Up or Volume Down at a safe listening level.

Add more routes to control several outputs from the same keys. A key press changes each output in sequence.

### Keyboard Input

Choose **Keyboard volume keys** as a route input to capture separate volume-down and volume-up keys. **Forward keys to other applications** is enabled by default, including existing routes, so the configured keys remain available to the foreground application. Disable it only when that route should consume its configured key down/repeats and matching key up; modifier and unrelated keys are never consumed.

### MQTT Input

Choose **MQTT / Home Assistant** as a route input, then configure its broker host, port, optional credentials, discovery prefix, topic prefix, and slider maximum. The route connects to the broker in the background and publishes retained Home Assistant MQTT discovery for a 0-to-maximum volume slider. It accepts integer slider values from `0` to the configured maximum on `<topic prefix>/<route id>/command`. MQTT credentials are stored in the route settings, so use a restricted broker account.

## Outputs

### Monitor

Enable DDC/CI in the monitor menu before configuring a monitor route. Select the monitor in its configuration window. If the app cannot identify a monitor safely, reconnect it or choose it again.

### Receiver

Choose the matching receiver output and enter its network address in **Configure output...**. The app supports common Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony network receivers. Use the receiver's network settings to find its address.

If a receiver does not respond, confirm that it is powered on, on the same network, and that its network-control option is enabled.

## Manage Routes

- **Edit** changes a route name, input, output, or output settings.
- **Duplicate route** creates a copy you can configure for another device.
- **Remove** stops a route from receiving volume-key changes.
- Route names appear in the overlay and status messages.

## Overlay

Choose an overlay in the **Overlay** section of Routes.

- **Windows 11 overlay** offers **Current route** and **All** display choices.
- **macOS-style overlay** provides an alternate visual style.

Current route shows the route most recently changed. All shows every configured route and its current status.

## Plugins And Shortcuts

The **Action plugins** section contains optional actions such as Discord output switching. Select **Configure** to set up an action plugin. Select **Configure shortcuts** to assign a shortcut and choose whether **Forward keys to other applications** is enabled. Existing shortcuts default to forwarding; disabled forwarding consumes only that shortcut's held key pair. Use **Disable** to stop an action plugin and unregister its shortcuts; disabled action plugins do not initialize at the next start. Enabling a plugin that was stopped in the current session takes effect after restarting the app, because plugins are not required to support reinitialization.

**Audio output keep-alive** renders silence to the selected current Windows default playback output, voice output, or both. It is disabled until configured. Choose continuous operation, or keep the outputs active only while the pointer has moved within the selected number of seconds. The plugin does not change volume or default-device selection.

## Tray

Right-click the notification-area icon for status, refresh, restore, and exit commands. Closing a restored app window exits the app. Minimizing returns it to the notification area.

## Start With Windows

Use the **Start with Windows** option in Routes to launch the app when you sign in.

## Safety Notes

Test new routes at a safe volume. If an output cannot be contacted, the app reports the route failure and Windows volume keys return to normal Windows behavior until a route is ready. The app does not intercept Mute.

For common problems, see [Troubleshooting](TROUBLESHOOTING.md). For storage, privacy, and network details, see [Configuration and Security](CONFIGURATION.md).
