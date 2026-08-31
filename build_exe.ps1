Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $repoRoot

try {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source

    if (-not (Test-Path "app.py")) {
        throw "app.py was not found in $repoRoot"
    }

    if (-not (Test-Path "FenSoundSwitch.ico")) {
        throw "FenSoundSwitch.ico was not found in $repoRoot"
    }

    Write-Host "Building dist\\FenSoundSwitch.exe with Nuitka..."

    & $pythonExe -m nuitka `
        --onefile `
        --windows-console-mode=disable `
        --enable-plugins=tk-inter `
        --include-package=plugins `
        --include-module=plugins.windows11_overlay_plugin `
        --include-module=plugins.macos_overlay_plugin `
        --include-module=plugins.keyboard_input_plugin `
        --include-module=plugins.windows_soundcard_volume_plugin `
        --windows-icon-from-ico=FenSoundSwitch.ico `
        --include-data-files=FenSoundSwitch.ico=FenSoundSwitch.ico `
        --output-dir=dist `
        --output-filename=FenSoundSwitch.exe `
        --remove-output `
        --assume-yes-for-downloads `
        app.py

    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE"
    }

    Write-Host "Build complete: dist\\FenSoundSwitch.exe"
}
finally {
    Pop-Location
}
