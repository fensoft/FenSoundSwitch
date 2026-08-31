# Build FenSoundSwitch

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or newer.
- A working Python installation on `PATH`.

## Build The Executable

From the repository root:

```powershell
python -m pip install -e .[build]
.\build_exe.ps1
```

The build produces `dist\FenSoundSwitch.exe`.

The build can download Nuitka support or toolchain components, overwrites an existing output with the same name, and removes intermediate output. Do not run it as a routine test.

## Validate Before Building

Run the hardware-free checks first:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py audio_outputs.py autostart.py core_audio.py ddc.py diagnostics.py gui.py main.py plugin_api.py plugin_hotkeys.py plugin_manager.py settings.py theme.py windows_platform.py plugins
python -m pip check
git diff --check
git diff --cached --check
git status --short
```

Parse the PowerShell build script without executing it:

```powershell
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\build_exe.ps1),
    [ref]$tokens,
    [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -ne 0) { $parseErrors; exit 1 }
```

## Continuous Integration And Releases

Continuous integration runs hardware-free checks on Windows. The Release workflow builds and verifies the executable on `master`; a pushed Git tag additionally publishes the executable as a GitHub Release asset. There is no installer or signing automation.
