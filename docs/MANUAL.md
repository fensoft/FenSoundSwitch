# FenSoundSwitch Manual

## Install

Download `FenSoundSwitch.msi` from [GitHub Releases](https://github.com/fensoft/windows-ddc/releases) and install it. Windows Installer places the standalone application under Program Files and creates an all-users Start Menu shortcut. Launch **FenSoundSwitch** from there. The app starts in the notification area; double-click its icon to open the main window. Disable **Start with Windows** before uninstalling: the per-machine installer cannot safely remove that current-user value from every Windows profile. Uninstalling removes application files and shortcuts but preserves per-user configuration and credentials.

The **About** page shows the exact tag embedded in a release executable. Source runs and ordinary local builds show version `dev`.

## Create Your First Route

A route connects an input to one or more outputs. The normal input is **Windows media keys**.

1. Open the app window from the notification area.
2. In **Routes**, select **Add route**.
3. Enter a useful route name, such as `Desk monitor` or `Living room`.
4. Select an informational type such as Voice, Headset, Headphones, Earbuds, Speakers, Soundbar, TV, AVR, Amplifier, Microphone, Line-in, Line-out, Mixer, Monitor, or Other.
5. Select **Windows media keys** as the input.
6. Select the output you want to control.
7. Complete any input and output configuration shown by the wizard, then create the route.
8. Press Volume Up or Volume Down at a safe listening level.

Add more routes to control several outputs from the same keys. A key press changes each output in sequence.

### Keyboard Input

Choose **Custom keyboard keys** as a route input to capture separate decrease and increase keys and an optional mute key. Held decrease/increase keys use the real repeated Windows key-down events, matching native key cadence without a slower synthetic timer. **Forward keys to other applications** is enabled by default, including existing routes, so the configured keys remain available to the foreground application. Disable it only when that route should consume its configured key down/repeats and matching key up; modifier and unrelated keys are never consumed.

### MQTT Input

First open **Integrations**, select **Configure** on **MQTT / Home Assistant**, and manage one or more named configurations inside that window. Each stores a broker host, port, optional credentials, discovery prefix, and topic prefix. Choose **MQTT / Home Assistant** as a route input, select which shared configuration to reuse, then provide that route's Home Assistant name, stable ID, and slider maximum. The route publishes retained Home Assistant discovery and accepts integer values on `<topic prefix>/<Home Assistant ID>/command`. Existing routes with inline broker settings continue to work until edited. Use a restricted broker account because exported configurations include MQTT credentials.

## Outputs

### Monitor

Enable DDC/CI in the monitor menu before configuring a monitor route. Select the monitor in its configuration window. If the app cannot identify a monitor safely, reconnect it or choose it again.

### Receiver

Choose the matching receiver output and enter its network address in **Configure output...**. The app supports common Onkyo/Integra, Denon/Marantz, Yamaha, Pioneer/Elite, and Sony network receivers. Use the receiver's network settings to find its address.

**Turn on when route activates** sends the receiver's main-zone power-on command once when that configured route instance first starts. **Input on activation** can then select an input from a protocol-wide list assembled across known models. Input availability and naming still vary by model; selecting an unsupported entry makes that route's startup activation fail or be ignored by the receiver. Leave both controls disabled to preserve the receiver's current power and input state.

Each route activates independently. If several routes address the same receiver with different startup inputs, their startup order determines the final input.

If a receiver does not respond, confirm that it is powered on, on the same network, and that its network-control option is enabled.

## Manage Routes

- **Edit** changes a route name, informational type, input, output, or output settings.
- **Duplicate route** creates a copy you can configure for another device.
- **Remove** stops a route from receiving volume-key changes.
- Route names appear in the overlay and status messages.
- Route type is display-only and never changes routing or device behavior.

## Overlay

Choose an overlay in the **Overlay** section of Routes.

- **Windows 11 overlay** offers **Current route** and **All** display choices.
- **macOS-style overlay** provides an alternate visual style.

Current route shows the route most recently changed. All shows every configured route and its current status.

## Automations

The **Automations** section combines one or more triggers with an ordered list of steps. **Add trigger** opens a chooser that documents app-start, keyboard, notification-area menu, and MQTT/Home Assistant triggers before inserting one; each trigger type can be added once to an automation. A new tray trigger reuses the automation name as its editable menu label. **Add action** opens the same style of documented chooser for plugin actions and **Wait**. An inserted action keeps its selected type; remove it and add another action to replace it. Wait duration, action configuration, and step order remain editable. MQTT-triggered automations select a shared MQTT/HA configuration and define their own Home Assistant name and stable ID. New triggers generate that ID from the Home Assistant name until the ID field is edited manually. Home Assistant receives retained button discovery and a press publishes `PRESS` to `<topic prefix>/automation/<Home Assistant ID>/command`. Removing a trigger row removes that trigger when the automation is saved. Every action completes before the next step starts; a failing step stops that run.

For example, create `Movie mode`, assign `F1`, add **Cycle Windows playback**, add a 1000 ms wait, and then add **Cycle Windows voice output**. Setting a tray label also exposes the same automation under **Automations** in the tray icon's right-click menu.

## Integrations

The separate **Integrations** tab is a simple integration list. Select **Configure** on MQTT/Home Assistant to add, edit, or remove all reusable broker configurations within one management window. Other integrations expose their own Configure button when setup is available. MQTT routes and automations ask which named MQTT/HA configuration to reuse. A configuration cannot be removed while a route or automation references it.

Before Discord is configured, its integration card shows **Configure** and **Open Developer Portal**. Configure presents numbered OAuth setup instructions. After client configuration is saved, those setup controls are hidden and **Reset authorization** is shown instead. Reset removes the Credential Manager OAuth data and restores the setup controls.

**DDC monitor input** adds a **Select monitor input** automation step. Add the step and select **Configure** beside it. The configuration dialog opens immediately with a waiting message while monitor discovery runs, then shows the stable monitor and input choices. DDC handles and the live `Display n` number shown in Windows Settings are acquired together from the same Windows monitor record; neither list position nor description matching is used. Monitor choices are ordered by ascending Windows display number. Use **Refresh monitors** in that dialog to repeat discovery. Every step keeps its own target, so one automation can configure several screens independently. The monitor is saved by stable EDID identity when available, with its Windows device path as the fallback; its temporary display number is never saved. Each run finds that exact monitor again, confirms that it still advertises the selected input, changes it once, and verifies the result. A missing or ambiguous monitor stops the automation without changing another display.

**Audio output keep-alive** renders silence to the selected current Windows default playback output, voice output, or both. It is disabled until configured. Choose continuous operation, or keep the outputs active only while the pointer has moved within the selected number of seconds. The plugin does not change volume or default-device selection.

## Tray

Right-click the notification-area icon for status, refresh, restore, and exit commands. The icon remains available while the main window is open. Closing a restored app window exits the app. Minimizing returns it to the notification area.

## Start With Windows

Use the **Start with Windows** option in Routes to launch the app when you sign in.

## Safety Notes

Test new routes at a safe volume. If an output cannot be contacted, the app reports the route failure and Windows media keys return to normal Windows behavior until a route is ready. Mute toggles only outputs with confirmed native support: Windows playback/capture, Denon/Marantz, Onkyo/Integra, Yamaha, Pioneer/Elite, and Sony routes. DDC monitor and external providers without the explicit capability remain unchanged. A mixed route fan-out toggles only supported outputs.

For common problems, see [Troubleshooting](TROUBLESHOOTING.md). For storage, privacy, and network details, see [Configuration and Security](CONFIGURATION.md).
