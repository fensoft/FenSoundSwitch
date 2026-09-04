# FenSoundSwitch Manual

## Install

Download `FenSoundSwitch.exe` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases), put it in a folder you control, and run it. The app starts in the notification area. Double-click its icon to open the main window.

The **About** page shows the exact tag embedded in a release executable. Source runs and ordinary local builds show version `dev`.

## Create Your First Route

A route connects an input to one or more outputs. The normal input is **Windows Volume keys**.

1. Open the app window from the notification area.
2. In **Routes**, select **Add route**.
3. Enter a useful route name, such as `Desk monitor` or `Living room`.
4. Select **Windows Volume keys** as the input.
5. Select the output you want to control.
6. Select **Configure output...**.
7. For a monitor, select the monitor from the list. For a receiver, enter its address and port, then optionally enable startup power and choose an input.
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

**Turn on when route activates** sends the receiver's main-zone power-on command once when that configured route instance first starts. **Input on activation** can then select an input from a protocol-wide list assembled across known models. Input availability and naming still vary by model; selecting an unsupported entry makes that route's startup activation fail or be ignored by the receiver. Leave both controls disabled to preserve the receiver's current power and input state.

Each route activates independently. If several routes address the same receiver with different startup inputs, their startup order determines the final input.

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

## Automations And Integrations

The **Automations** section combines one or more triggers with an ordered list of steps. An automation may run when the primary app starts, from a keyboard shortcut, from an item in the notification-area icon's right-click menu, or from any combination of those triggers. Enable **Run when a key is pressed** to reveal the key and forwarding controls. Enable **Run when a tray menu option is chosen** to reveal its menu-text field. Disabled trigger sections are hidden and cleared when the automation is saved. Add action steps in the order they must run, insert wait steps where a delay is needed, and use the arrow buttons to reorder them. Every action completes before the next step starts. Repeated triggers while the same automation is running are ignored; a failing step stops that run. Waits are interrupted during application shutdown.

For example, create `Movie mode`, assign `F1`, add **Cycle Windows playback**, add a 1000 ms wait, and then add **Cycle Windows voice output**. Setting a tray label also exposes the same automation under **Automations** in the tray icon's right-click menu.

The **Integrations** section contains setup only for integrations used by steps, such as Discord output switching and audio keep-alive. Discord and Windows device switching do not expose separate direct actions or action shortcuts; add their steps to an automation and configure the automation's trigger instead. Existing saved Discord and Windows direct shortcuts are promoted to equivalent one-step automations.

**DDC monitor input** adds a **Select monitor input** automation step. Add the step and select **Configure** beside it. The configuration dialog opens immediately with a waiting message while monitor discovery runs, then shows the stable monitor and input choices. Use **Refresh monitors** in that dialog to repeat discovery. Every step keeps its own target, so one automation can configure several screens independently. The monitor is saved by stable EDID identity when available, with its Windows device path as the fallback; its temporary list number is never saved. Each run finds that exact monitor again, confirms that it still advertises the selected input, changes it once, and verifies the result. A missing or ambiguous monitor stops the automation without changing another display.

**Audio output keep-alive** renders silence to the selected current Windows default playback output, voice output, or both. It is disabled until configured. Choose continuous operation, or keep the outputs active only while the pointer has moved within the selected number of seconds. The plugin does not change volume or default-device selection.

## Tray

Right-click the notification-area icon for status, refresh, restore, and exit commands. The icon remains available while the main window is open. Closing a restored app window exits the app. Minimizing returns it to the notification area.

## Start With Windows

Use the **Start with Windows** option in Routes to launch the app when you sign in.

## Safety Notes

Test new routes at a safe volume. If an output cannot be contacted, the app reports the route failure and Windows volume keys return to normal Windows behavior until a route is ready. The app does not intercept Mute.

For common problems, see [Troubleshooting](TROUBLESHOOTING.md). For storage, privacy, and network details, see [Configuration and Security](CONFIGURATION.md).
