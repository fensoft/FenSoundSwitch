param(
    [ValidateNotNullOrEmpty()]
    [string]$Version = "dev"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$versionFile = Join-Path $repoRoot "fensoundswitch-version.txt"
$versionFileCreated = $false
$nuitkaOutputDir = Join-Path ([System.IO.Path]::GetTempPath()) "fensoundswitch-nuitka-$PID"
$generatedStandaloneDir = Join-Path $nuitkaOutputDir "app.dist"
$standaloneDir = Join-Path $repoRoot "dist\FenSoundSwitch"
Push-Location $repoRoot

try {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source
    $pythonBits = & $pythonExe -c "import struct; print(struct.calcsize('P') * 8)"
    if ($LASTEXITCODE -ne 0 -or $pythonBits -ne "64") {
        throw "The Windows x64 package requires a 64-bit Python interpreter."
    }

    if (-not (Test-Path "app.py")) {
        throw "app.py was not found in $repoRoot"
    }

    if (-not (Test-Path "FenSoundSwitch.ico")) {
        throw "FenSoundSwitch.ico was not found in $repoRoot"
    }

    if (-not (Test-Path "web\index.html")) {
        throw "web\index.html was not found in $repoRoot"
    }

    $displayVersion = $Version.Trim()
    if ($displayVersion.Length -gt 255 -or $displayVersion -match '[\x00-\x1F]') {
        throw "Build version must be at most 255 printable characters."
    }
    if ($displayVersion -eq "dev") {
        $windowsVersion = "0.0.0.0"
    }
    else {
        $numericVersion = $displayVersion -replace '^v', ''
        if ($numericVersion -match '^\d+(?:\.\d+){1,3}$') {
            $components = @($numericVersion.Split('.'))
            foreach ($component in $components) {
                $parsedComponent = [uint16]0
                if (-not [uint16]::TryParse($component, [ref]$parsedComponent)) {
                    throw "Release version '$displayVersion' contains a component outside the Windows 0-65535 range."
                }
            }
            while ($components.Count -lt 4) {
                $components += "0"
            }
            $windowsVersion = $components -join "."
        }
        else {
            $windowsVersion = "0.0.0.0"
        }
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($versionFile, $displayVersion, $utf8NoBom)
    $versionFileCreated = $true

    if (Test-Path -LiteralPath $nuitkaOutputDir) {
        Remove-Item -LiteralPath $nuitkaOutputDir -Recurse -Force
    }

    Write-Host "Building standalone dist\\FenSoundSwitch version $displayVersion with Nuitka..."

    & $pythonExe -m nuitka `
        --standalone `
        --windows-console-mode=disable `
        --enable-plugins=tk-inter `
        --include-package-data=webview `
        --include-package=plugins `
        --include-module=plugins.windows11_overlay_plugin `
        --include-module=plugins.macos_overlay_plugin `
        --include-module=plugins.keyboard_input_plugin `
        --include-module=plugins.windows_bluetooth_volume_plugin `
        --include-module=plugins.windows_soundcard_volume_plugin `
        --include-data-dir=plugins/macos_overlay/assets=plugins/macos_overlay/assets `
        --windows-icon-from-ico=FenSoundSwitch.ico `
        --product-name=FenSoundSwitch `
        --file-description="FenSoundSwitch $displayVersion" `
        --file-version=$windowsVersion `
        --product-version=$windowsVersion `
        --include-data-files=FenSoundSwitch.ico=FenSoundSwitch.ico `
        --include-data-files=fensoundswitch-version.txt=fensoundswitch-version.txt `
        --include-data-dir=web=web `
        --output-dir=$nuitkaOutputDir `
        --output-filename=FenSoundSwitch.exe `
        --remove-output `
        --assume-yes-for-downloads `
        app.py

    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $generatedStandaloneDir "FenSoundSwitch.exe"))) {
        throw "Nuitka standalone output was not found at $generatedStandaloneDir"
    }
    New-Item -ItemType Directory -Path $standaloneDir -Force | Out-Null
    & robocopy $generatedStandaloneDir $standaloneDir /MIR /R:3 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) {
        throw "Failed to synchronize standalone output with robocopy exit code $LASTEXITCODE"
    }

    Write-Host "Build complete: dist\\FenSoundSwitch"
}
finally {
    if ($versionFileCreated -and (Test-Path -LiteralPath $versionFile)) {
        Remove-Item -LiteralPath $versionFile -Force
    }
    if (Test-Path -LiteralPath $nuitkaOutputDir) {
        Remove-Item -LiteralPath $nuitkaOutputDir -Recurse -Force
    }
    Pop-Location
}
