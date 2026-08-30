# Troubleshooting

| Symptom | Resolution |
| --- | --- |
| No window appears | Check the notification area and overflow menu, then double-click the icon or use **Restore**. Tray-first startup is expected. |
| A second launch does nothing | The duplicate exits after asking the existing instance to restore. Check the existing tray window. |
| Windows volume keys change Windows audio | Open the app and confirm at least one configured route is ready. Unavailable routes deliberately pass keys through to Windows. |
| A route reports unavailable | Edit the route, confirm its input/output selection, and configure its output again. |
| A monitor route fails | Enable DDC/CI in the monitor menu, select the correct monitor in the route output settings, then try again. |
| A receiver route times out | Confirm its address, port, power state, network-control option, and local-network reachability. |
| The overlay does not appear | Select an overlay in Routes and inspect the status message for an overlay-specific error. |
| Settings are not remembered | Ensure the per-user settings folder is writable and only one app instance is running. |
| Start with Windows fails | Check the status/log and Run-key access. |
| Discord setup fails | Confirm Discord is running and follow the setup guidance shown by the plugin. |
| Build fails | Follow [BUILD.md](../BUILD.md). |

For step-by-step setup, see the [Manual](MANUAL.md). For technical details, see [Technical Documentation](TECHNICAL.md).
