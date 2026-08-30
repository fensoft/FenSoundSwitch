# Development

## Validation

Run hardware-free checks from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py audio_outputs.py autostart.py ddc.py diagnostics.py gui.py main.py plugin_api.py plugin_hotkeys.py plugin_manager.py settings.py theme.py windows_platform.py plugins
python -m pip check
git diff --check
git diff --cached --check
git status --short
```

CI runs these hardware-free checks on Windows with Python 3.10. It does not start the application, invoke the build, discover live monitors or receivers, change audio endpoints, or access Discord credentials.

## Manual Validation

GUI, DDC, tray, global-hook, audio-routing, Discord, and network-receiver changes require an authorized manual Windows test. These tests can change monitor or receiver volume, user audio routing, credentials, and keyboard behavior. Do not treat application startup as a generic smoke test.

See [BUILD.md](../BUILD.md) for executable build instructions, [Architecture](ARCHITECTURE.md) for component boundaries, and [AGENTS.md](../AGENTS.md) for repository-specific maintenance rules.
