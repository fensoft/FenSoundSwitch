# Build FenSoundSwitch

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or newer.
- A working Python installation on `PATH`.
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download-section). It is included with current Windows 10/11 installations and is required for the HTML control window.

## Build The Executable

From the repository root:

```powershell
python -m pip install -e .[build]
.\build_exe.ps1
```

The build produces `dist\FenSoundSwitch.exe`. A normal local build embeds `dev`, which is shown on the application's **About** page. The release workflow passes the pushed tag name to the build, so a tag named `2.0` is displayed exactly as version `2.0`. Numeric tags may have a leading `v` and are padded to four components for Windows executable metadata. Other tag names are still displayed exactly while their numeric Windows file version remains `0.0.0.0`.

The build can download Nuitka support or toolchain components, overwrites an existing output with the same name, and removes intermediate output. Do not run it as a routine test.

## Validate Before Building

Run the hardware-free checks first:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py app_version.py audio_outputs.py autostart.py core_audio.py ddc.py diagnostics.py gui.py main.py plugin_api.py plugin_hotkeys.py plugin_manager.py settings.py theme.py web_presentation.py web_ui_host.py windows_platform.py plugins
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

Continuous integration runs hardware-free checks on Windows. The Release workflow builds and verifies a `dev` executable on `master`; a pushed Git tag embeds that exact tag, verifies the executable, and publishes it as a GitHub Release asset. There is no installer or signing automation.
