[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$NoBrowser,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$ComposeFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\docker-compose.yml"
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"
$BaseUrl = "http://127.0.0.1:5000"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-Uv([string[]]$Arguments) {
    Invoke-Native "uv" $Arguments
}

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) {
        return $null
    }

    $line = Get-Content -Path $EnvFile | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -First 1

    if ($null -eq $line) {
        return $null
    }

    return ($line -replace "^\s*$([regex]::Escape($Name))\s*=\s*", "")
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "       EDIT FACTORY V3 - START ALL" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "Root: $Root"

Write-Step "Checking required tools"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install/start Docker Desktop, then run START-ALL.bat again."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start Docker Desktop, wait until it is ready, then retry."
}
Write-Ok "Docker daemon is running."

if (-not (Test-Path $ComposeFile)) {
    throw "Docker Compose file not found: $ComposeFile"
}

Write-Step "Preparing local dashboard configuration"

if (-not (Test-Path $EnvFile)) {
    $dashboardToken = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
    $flaskSecret = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))

    @"
# Auto-created by START-ALL.ps1 for local Windows development.
# This file is intentionally local and should not be committed.
FLASK_SECRET_KEY=$flaskSecret
AIVF_DASHBOARD_TOKEN=$dashboardToken
AIVF_COOKIE_SECURE=0
AIVF_ENV=development
"@ | Set-Content -Path $EnvFile -Encoding UTF8

    Write-Ok "Created local .env with generated development secrets."
} else {
    Write-Ok "Using existing 06-CONFIG-AND-DEPLOYMENT\.env."
}

$env:AIVF_COOKIE_SECURE = "0"
Write-Ok "Using non-secure cookies for the local HTTP dashboard."

Write-Step "Validating Docker Compose configuration"
Invoke-Native "docker" @(
    "compose",
    "--env-file", $EnvFile,
    "-f", $ComposeFile,
    "config"
)
Write-Ok "Docker Compose configuration is valid."

Write-Step "Starting the full Edit Factory stack"

$composeUpArgs = @(
    "compose",
    "--env-file", $EnvFile,
    "-f", $ComposeFile,
    "up",
    "-d",
    "--remove-orphans"
)

if (-not $NoBuild) {
    $composeUpArgs += "--build"
}

Invoke-Native "docker" $composeUpArgs
Write-Ok "Edit Factory dashboard container started."

Write-Step "Waiting for the dashboard health check"

$healthy = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "$BaseUrl/api/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # The server may still be starting.
    }

    Start-Sleep -Seconds 1
}

if (-not $healthy) {
    Write-Host ""
    Write-Host "Dashboard did not become healthy. Recent container output:" -ForegroundColor Red
    & docker compose --env-file $EnvFile -f $ComposeFile logs --tail 100 web
    throw "Dashboard health check failed."
}

Write-Ok "Dashboard is healthy at $BaseUrl."

Write-Step "Checking container state"
& docker compose --env-file $EnvFile -f $ComposeFile ps
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose status check failed."
}
Write-Ok "Container status checked."

$token = Get-EnvValue "AIVF_DASHBOARD_TOKEN"
if ($token) {
    Write-Host ""
    Write-Host "Dashboard login token:" -ForegroundColor Yellow
    Write-Host $token -ForegroundColor White
    Write-Host "Keep this token private. It is only shown here because this is a local launcher." -ForegroundColor DarkGray
}

if (-not $NoBrowser) {
    Write-Step "Opening the dashboard"
    Start-Process "$BaseUrl/login"
    Write-Ok "Browser opened: $BaseUrl/login"
}

$stopCommand = "docker compose --env-file `"$EnvFile`" -f `"$ComposeFile`" down"
if ($SkipChecks) {
    Write-Warn "Code checks were skipped with -SkipChecks."
    Write-Host ""
    Write-Host "Edit Factory is running." -ForegroundColor Cyan
    Write-Host "Stop it with: $stopCommand" -ForegroundColor White
    exit 0
}

Write-Step "Running local code checks"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn "uv is not installed, so the full Python check suite was skipped."
    Write-Warn "Install uv and run START-ALL.bat again to enable compile, Ruff, mypy, and pytest checks."
} else {
    $env:PYTHONPATH = "01-MAIN-CODE;02-WEB-FILES;03-SIDE-CODE"

    Invoke-Uv @(
        "sync",
        "--frozen",
        "--python", "3.12",
        "--extra", "web",
        "--extra", "dev"
    )
    Write-Ok "Locked development environment is ready."

    Invoke-Uv @("pip", "check")
    Write-Ok "Dependency check passed."

    Invoke-Uv @(
        "run", "python", "-m", "compileall", "-q",
        "01-MAIN-CODE", "02-WEB-FILES", "03-SIDE-CODE"
    )
    Write-Ok "Python compilation check passed."

    Invoke-Uv @(
        "run", "ruff", "check",
        "01-MAIN-CODE", "02-WEB-FILES", "03-SIDE-CODE",
        "--select", "E9,F",
        "--ignore", "F401,F811,F841"
    )
    Write-Ok "Ruff correctness check passed."

    Invoke-Uv @("run", "mypy")
    Write-Ok "mypy check passed."

    Invoke-Uv @("run", "pytest", "-q", "04-TESTS/tests")
    Write-Ok "Full pytest suite passed."
}

Write-Step "Final runtime verification"

try {
    $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 5
    Write-Ok ("Runtime health response: " + ($health | ConvertTo-Json -Compress))
} catch {
    throw "Final dashboard health verification failed: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "             START ALL COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Dashboard: $BaseUrl" -ForegroundColor Cyan
Write-Host "The Docker stack is still running." -ForegroundColor White
Write-Host ""
Write-Host "To stop everything: $stopCommand" -ForegroundColor White
exit 0