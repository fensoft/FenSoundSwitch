# Build FenSoundSwitch

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or newer.
- A working Python installation on `PATH`.
- .NET SDK 8 or newer for the WiX command-line tool.
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download-section). It is included with current Windows 10/11 installations and is required for the HTML control window.

## Build The Installer

From the repository root:

```powershell
python -m pip install -e .[build]
.\build_installer.ps1
```

`build_installer.ps1` restores the pinned WiX 6.0.2 command from `.config\dotnet-tools.json` through the official NuGet v3 source; no global WiX installation is required.

For packaging-authoring changes only, `build_installer.ps1 -SkipStandaloneBuild` can reuse an existing `dist\FenSoundSwitch\` tree. Release CI never uses this switch and always performs a clean standalone build.

The build produces `dist\FenSoundSwitch.msi`. It contains the non-self-extracting Nuitka standalone application generated under `dist\FenSoundSwitch\`. A normal local build embeds `dev`, which is shown on the application's **About** page. Release tags must use `major.minor` or `major.minor.patch`, optionally prefixed with `v`; a tag named `2.0` is displayed exactly as version `2.0`. PE metadata is padded to four components and MSI upgrade comparison uses all three accepted numeric components. Same-version rebuilds replace one another rather than creating parallel registered products.

The build can download Nuitka, WiX, or toolchain components, replaces existing output under `dist`, and removes intermediate compiler output. The MSI uses the native Windows Installer service rather than an executable bootstrap or Nuitka extraction phase. Do not run it as a routine test.

Release installers and application binaries are currently unsigned. Defender can still classify unfamiliar software with low-level keyboard hooks and Windows device control as a generic machine-learning threat even when its published digest matches, but standalone compilation removes the prior self-extracting bootstrap signal. Signing releases with a trusted Authenticode certificate remains the durable reputation fix. Until signing is configured, submit false positives to [Microsoft Security Intelligence](https://www.microsoft.com/en-us/wdsi/filesubmission) and compare the downloaded SHA-256 with the digest shown for the GitHub Release asset. Do not advise users to disable Defender or add broad exclusions.

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

Parse both PowerShell build scripts without executing them:

```powershell
foreach ($script in @(".\build_exe.ps1", ".\build_installer.ps1")) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $script),
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -ne 0) { $parseErrors; exit 1 }
}
```

## Continuous Integration And Releases

Continuous integration runs hardware-free checks on Windows. The Release workflow builds and verifies a `dev` MSI on `master`; a pushed Git tag embeds that exact tag, verifies the standalone executable and MSI, and publishes the MSI as a GitHub Release asset. There is no signing automation.
