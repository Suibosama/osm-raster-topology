Param(
    [ValidateSet("gui", "run", "check", "design")]
    [string]$Mode = "gui",
    [string]$InputPath = "",
    [string]$OutputDir = "",
    [double]$PixelSize = 1.0,
    [string]$TargetCrs = "EPSG:3857",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$PipExe = Join-Path $VenvPath "Scripts\pip.exe"
$BootstrapMarker = Join-Path $VenvPath ".bootstrap_complete"

Write-Host "Project root: $ProjectRoot"

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating virtual environment..."
    python -m venv $VenvPath
}

if (-not $SkipInstall) {
    if (-not (Test-Path $BootstrapMarker)) {
        Write-Host "Installing project dependencies (first run)..."
        & $PythonExe -m pip install --upgrade pip
        & $PipExe install -e "$ProjectRoot"
        & $PipExe install -e "$ProjectRoot[gui]"
        & $PipExe install pyproj
        & $PipExe install rasterio
        & $PipExe install matplotlib
        New-Item -ItemType File -Path $BootstrapMarker -Force | Out-Null
    }
    else {
        Write-Host "Dependencies already installed. Use -SkipInstall to skip this check entirely."
        & $PipExe install pyproj | Out-Null
        & $PipExe install rasterio | Out-Null
        & $PipExe install matplotlib | Out-Null
    }
}
else {
    Write-Host "Skipping dependency installation by request."
}

Push-Location $ProjectRoot
try {
    if ($Mode -eq "gui") {
        Write-Host "Launching GUI..."
        & $PythonExe -m osm_raster_topology gui
        exit $LASTEXITCODE
    }

    if ([string]::IsNullOrWhiteSpace($InputPath)) {
        throw "InputPath is required for mode '$Mode'. Use -InputPath path\to\file.osm or .xodr"
    }

    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        $OutputDir = Join-Path $ProjectRoot "build\run_bundle"
    }

    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    Write-Host "Running mode: $Mode"
    Write-Host "Input: $InputPath"
    Write-Host "OutDir: $OutputDir"

    & $PythonExe -m osm_raster_topology $Mode `
        --input $InputPath `
        --outdir $OutputDir `
        --pixel-size $PixelSize `
        --target-crs $TargetCrs

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
