# Development

## Build

Install the optional build tools, then run the one-file Nuitka build:

```powershell
python -m pip install -e .[build]
.\build_exe.ps1
```

The output is `dist\windows-ddc.exe`. The build may download Nuitka tooling and overwrite the ignored output. GitHub Actions builds and verifies this executable on `master`, but publishes it only for pushed Git tags.

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

CI runs the same hardware-free checks on Windows with Python 3.10. It never starts the application, invokes the build, discovers live monitors/receivers, changes audio endpoints, or accesses Discord credentials.

## Manual Validation

GUI, DDC, tray, global-hook, audio-routing, Discord, and network-receiver changes require an authorized manual Windows test. Such tests can change monitor or receiver volume, user audio routing, credentials, and keyboard behavior. Do not treat application startup as a generic smoke test.

See [Architecture](ARCHITECTURE.md) for component and thread boundaries, and [AGENTS.md](../AGENTS.md) for repository-specific maintenance rules.
