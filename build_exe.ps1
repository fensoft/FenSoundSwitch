Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $repoRoot

try {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source

    if (-not (Test-Path "app.py")) {
        throw "app.py was not found in $repoRoot"
    }

    if (-not (Test-Path "windows-ddc.ico")) {
        throw "windows-ddc.ico was not found in $repoRoot"
    }

    Write-Host "Building dist\\windows-ddc.exe with Nuitka..."

    & $pythonExe -m nuitka `
        --onefile `
        --windows-console-mode=disable `
        --enable-plugins=tk-inter `
        --include-package=plugins `
        --include-module=plugins.windows11_overlay_plugin `
        --include-module=plugins.macos_overlay_plugin `
        --include-module=plugins.keyboard_input_plugin `
        --include-module=plugins.windows_soundcard_volume_plugin `
        --windows-icon-from-ico=windows-ddc.ico `
        --include-data-files=windows-ddc.ico=windows-ddc.ico `
        --output-dir=dist `
        --output-filename=windows-ddc.exe `
        --remove-output `
        --assume-yes-for-downloads `
        app.py

    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE"
    }

    Write-Host "Build complete: dist\\windows-ddc.exe"
}
finally {
    Pop-Location
}
