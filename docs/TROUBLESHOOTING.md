# Troubleshooting

| Symptom | Resolution |
| --- | --- |
| No window appears | Check the notification area and overflow menu, then double-click the icon or use **Restore**. Tray-first startup is expected. |
| A second launch does nothing | The duplicate exits after asking the existing instance to restore. Check the existing tray window. |
| No DDC/CI monitors found | Enable DDC/CI in the monitor OSD, confirm the active connection supports it, then Refresh. |
| Volume is `--` | Read the status, try another monitor, and confirm the target supports DDC/CI audio volume. |
| Monitor operation timed out | Wait for Refresh; restart if the native call never returns. The app intentionally avoids concurrent hardware calls. |
| Volume keys change Windows audio | Restore the UI and wait for a successful provider volume read. A hook failure leaves buttons and slider available. |
| Volume keys stop changing Windows audio | Expected while a provider is ready. Exit the app to restore normal system-volume behavior. |
| Selected monitor is missing | Reconnect it or select it again. The app never substitutes another monitor automatically. |
| Settings are not remembered | Ensure the per-user settings folder is writable and only one instance is running. Review the diagnostic log. |
| Start with Windows fails | Check the status/log, Run-key access, and the 260-character command limit. |
| Discord setup fails | Confirm Discord is running, the Application ID and newly reset secret are correct, the redirect is exactly `https://127.0.0.1`, and the required RPC scopes are approved. |
| A network receiver provider fails | Confirm the configured host, port, protocol support, and LAN reachability. The app does not discover receivers or retry indefinitely. |
| Build fails before compilation | Install `.[build]`, ensure `python` is on `PATH`, and keep `app.py` and `windows-ddc.ico` in the repository root. |

For detailed configuration, see [Configuration](CONFIGURATION.md). For maintainer validation, see [Development](DEVELOPMENT.md).
