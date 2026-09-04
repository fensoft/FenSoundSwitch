param(
    [ValidateNotNullOrEmpty()]
    [string]$Version = "dev",

    [switch]$SkipStandaloneBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$standaloneDir = Join-Path $repoRoot "dist\FenSoundSwitch"
$installerSource = Join-Path $repoRoot "installer\FenSoundSwitch.wxs"
$installerOutput = Join-Path $repoRoot "dist\FenSoundSwitch.msi"
Push-Location $repoRoot

try {
    $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $dotnet) {
        throw ".NET SDK 8 or newer was not found."
    }
    if (-not (Test-Path -LiteralPath $installerSource)) {
        throw "$installerSource was not found."
    }

    & $dotnet.Source tool restore --add-source "https://api.nuget.org/v3/index.json"
    if ($LASTEXITCODE -ne 0) {
        throw ".NET tool restore failed with exit code $LASTEXITCODE"
    }

    if (-not $SkipStandaloneBuild) {
        & (Join-Path $repoRoot "build_exe.ps1") -Version $Version
    }

    $displayVersion = $Version.Trim()
    if ($displayVersion -eq "dev") {
        $msiVersion = "0.0.0"
    }
    elseif ($displayVersion -match '^v?\d+\.\d+(?:\.\d+)?$') {
        $numericVersion = $displayVersion -replace '^v', ''
        $components = @($numericVersion.Split('.'))
        while ($components.Count -lt 3) {
            $components += "0"
        }
        $major = [uint32]$components[0]
        $minor = [uint32]$components[1]
        $build = [uint32]$components[2]
        if ($major -gt 255 -or $minor -gt 255 -or $build -gt 65535) {
            throw "Release version '$displayVersion' is outside the Windows Installer version range."
        }
        $msiVersion = "$major.$minor.$build"
    }
    else {
        throw "Installer release version '$displayVersion' must use major.minor or major.minor.patch, optionally prefixed with v."
    }

    if (-not (Test-Path -LiteralPath (Join-Path $standaloneDir "FenSoundSwitch.exe"))) {
        throw "Standalone application output was not found at $standaloneDir"
    }
    if ($SkipStandaloneBuild) {
        $standaloneVersionFile = Join-Path $standaloneDir "fensoundswitch-version.txt"
        if (-not (Test-Path -LiteralPath $standaloneVersionFile) -or
            [System.IO.File]::ReadAllText($standaloneVersionFile).Trim() -ne $displayVersion) {
            throw "Existing standalone output does not match requested version '$displayVersion'."
        }
    }
    if (Test-Path -LiteralPath $installerOutput) {
        Remove-Item -LiteralPath $installerOutput -Force
    }

    Write-Host "Building dist\\FenSoundSwitch.msi version $displayVersion with WiX..."
    & $dotnet.Source tool run wix -- build `
        -arch x64 `
        -d "ProductVersion=$msiVersion" `
        -b "StandaloneDir=$standaloneDir" `
        -b "ProjectRoot=$repoRoot" `
        -o $installerOutput `
        $installerSource

    if ($LASTEXITCODE -ne 0) {
        throw "WiX build failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $installerOutput)) {
        throw "WiX did not produce $installerOutput"
    }

    & $dotnet.Source tool run wix -- msi validate $installerOutput
    if ($LASTEXITCODE -ne 0) {
        throw "MSI validation failed with exit code $LASTEXITCODE"
    }

    Write-Host "Built installer at dist\\FenSoundSwitch.msi"
}
finally {
    Pop-Location
}
