[CmdletBinding()]
param(
    [switch]$RunChecks,
    [switch]$SkipChecks,
    [switch]$NoBrowser,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$ComposeFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\docker-compose.dev.yml"
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"

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

function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = @()
    if (Test-Path $EnvFile) {
        $lines = @(Get-Content -Path $EnvFile)
    }

    $updated = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=") {
            if (-not $found) {
                $updated.Add("$Name=$Value")
                $found = $true
            }
        } else {
            $updated.Add([string]$line)
        }
    }
    if (-not $found) {
        $updated.Add("$Name=$Value")
    }

    $temporaryEnvFile = "$EnvFile.tmp"
    Set-Content -Path $temporaryEnvFile -Value $updated.ToArray() -Encoding UTF8
    Move-Item -Path $temporaryEnvFile -Destination $EnvFile -Force
}

function Test-SecretValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    $candidate = $Value.Trim()
    if ($candidate.Length -lt 32) {
        return $false
    }
    $lower = $candidate.ToLowerInvariant()
    if ($lower -in @(
        "change-me", "changeme", "dev-secret-change-me", "dev-token-change-me",
        "replace-me", "your-secret", "your-token", "secret", "password"
    )) {
        return $false
    }
    if ($lower.StartsWith("change-me-") -or $lower.StartsWith("changeme-") -or
        $lower.StartsWith("replace-me-") -or $lower.StartsWith("your-") -or
        $lower.StartsWith("example-")) {
        return $false
    }
    return $true
}

$DashboardPort = $env:AIVF_DASHBOARD_PORT
if (-not $DashboardPort) {
    $DashboardPort = Get-EnvValue "AIVF_DASHBOARD_PORT"
}
if (-not $DashboardPort) {
    $DashboardPort = "5000"
}
if ($DashboardPort -notmatch '^\d{1,5}$') {
    throw "AIVF_DASHBOARD_PORT must be a TCP port number between 1 and 65535."
}
$DashboardPortNumber = [int]$DashboardPort
if ($DashboardPortNumber -lt 1 -or $DashboardPortNumber -gt 65535) {
    throw "AIVF_DASHBOARD_PORT must be a TCP port number between 1 and 65535."
}
$BaseUrl = "http://127.0.0.1:$DashboardPortNumber"
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

if ($RunChecks -and -not $SkipChecks -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required to run startup checks. Install uv before starting, or use -SkipChecks to explicitly skip code checks."
}

function Test-UsableSecret([string]$Value) {
    $candidate = if ($null -eq $Value) { "" } else { $Value.Trim() }
    if ($candidate.Length -lt 32) {
        return $false
    }
    if ($candidate -match "(?i)change[\s_-]*me|changeme|placeholder|replace[\s_-]*me|your[-_ ]?(secret|token)|^(secret|password|local-development)$") {
        return $false
    }
    return $true
}

Write-Step "Preparing local dashboard configuration"

if (-not (Test-Path $EnvFile)) {
    @"
# Auto-created by START-ALL.ps1 for local Windows development.
# This file is intentionally local and should not be committed.
AIVF_COOKIE_SECURE=0
AIVF_ENV=development
"@ | Set-Content -Path $EnvFile -Encoding UTF8
}

if (-not (Test-UsableSecret (Get-EnvValue "FLASK_SECRET_KEY"))) {
    $flaskSecret = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
    Set-EnvValue "FLASK_SECRET_KEY" $flaskSecret
    Write-Ok "Generated a valid FLASK_SECRET_KEY in the local .env."
}
if (-not (Test-UsableSecret (Get-EnvValue "AIVF_DASHBOARD_TOKEN"))) {
    $dashboardToken = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
    Set-EnvValue "AIVF_DASHBOARD_TOKEN" $dashboardToken
    Write-Ok "Generated a valid AIVF_DASHBOARD_TOKEN in the local .env."
}
if ([string]::IsNullOrWhiteSpace((Get-EnvValue "AIVF_ADMIN_PANEL_CODE"))) {
    # Generate a private per-install code. The application has no source-code fallback.
    $adminPanelCode = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
    Set-EnvValue "AIVF_ADMIN_PANEL_CODE" $adminPanelCode
    Write-Ok "Generated AIVF_ADMIN_PANEL_CODE in the ignored local .env."
    Write-Host "Admin unlock code saved in the private .env file; it is deliberately not printed." -ForegroundColor DarkGray
}
Write-Ok "Local dashboard configuration is ready."

# Preserve valid secrets across restarts, but repair absent or known placeholder
# values before Docker Compose sees the environment.
foreach ($secretName in @("FLASK_SECRET_KEY", "AIVF_DASHBOARD_TOKEN")) {
    $secretValue = Get-EnvValue $secretName
    if (-not (Test-SecretValue $secretValue)) {
        $generatedSecret = (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
        Set-EnvValue $secretName $generatedSecret
        Write-Warn "Generated a new local secret for $secretName because the existing value was missing or a placeholder."
    }
}

$env:AIVF_COOKIE_SECURE = "0"
Write-Ok "Using non-secure cookies for the local HTTP dashboard."

Write-Step "Validating Docker Compose configuration"
Invoke-Native "docker" @(
    "compose",
    "--env-file", $EnvFile,
    "-f", $ComposeFile,
    "config",
    "--quiet"
)
Write-Ok "Docker Compose configuration is valid; secret values were not printed."

function Start-StartupSplash {
    if ($NoBrowser) {
        return
    }

    $splashSource = Join-Path $Root "02-WEB-FILES\static\startup-splash.html"
    $welcomeSource = Join-Path $Root "02-WEB-FILES\templates\welcome.html"
    if (-not (Test-Path -LiteralPath $splashSource -PathType Leaf) -or
        -not (Test-Path -LiteralPath $welcomeSource -PathType Leaf)) {
        Write-Warn "Startup splash or welcome page is missing; continuing without the pre-login intro."
        return
    }

    $splashPath = Join-Path $env:TEMP ("EditFactoryStartup-" + [guid]::NewGuid().ToString("N") + ".html")
    $welcomePath = Join-Path $env:TEMP ("EditFactoryWelcome-" + [guid]::NewGuid().ToString("N") + ".html")
    $welcomeHtml = (Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw).Replace('href="/login"', ('href="' + $BaseUrl + '/login"'))
    Set-Content -LiteralPath $welcomePath -Value $welcomeHtml -Encoding UTF8
    $welcomeUri = ([System.Uri]::new($welcomePath)).AbsoluteUri

    $splashHtml = (Get-Content -LiteralPath $splashSource -Encoding UTF8 -Raw).Replace("__AIVF_BASE_URL__", $BaseUrl).Replace("__AIVF_WELCOME_URI__", $welcomeUri)
    Set-Content -LiteralPath $splashPath -Value $splashHtml -Encoding UTF8
    $splashUri = ([System.Uri]::new($splashPath)).AbsoluteUri

    $browser = $null
    foreach ($candidate in @("msedge.exe", "chrome.exe")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            $browser = $command.Source
            break
        }
    }
    if (-not $browser) {
        $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
        $candidates = @(
            (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
            (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
            (Join-Path $programFilesX86 "Microsoft\Edge\Application\msedge.exe"),
            (Join-Path $programFilesX86 "Google\Chrome\Application\chrome.exe")
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
        $browser = $candidates | Select-Object -First 1
    }

    if ($browser) {
        $profileDir = Join-Path $env:TEMP ("EditFactoryV3-Kiosk-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        $browserArguments = @(
            "--kiosk",
            "--start-fullscreen",
            "--app=$splashUri",
            ('--user-data-dir="{0}"' -f $profileDir),
            "--no-first-run",
            "--disable-background-mode",
            "--disable-session-crashed-bubble"
        )
        Start-Process -FilePath $browser -ArgumentList $browserArguments
        Write-Ok "Fullscreen kiosk startup screen opened in an isolated browser profile."
    } else {
        Start-Process $splashUri
        Write-Warn "Chrome/Edge was not found; opened the startup screen in the default browser (fullscreen cannot be guaranteed)."
    }
}

Start-StartupSplash

Write-Step "Starting the local development dashboard stack"

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
Write-Ok "Local Edit Factory dashboard container started."

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

$usersConfig = Get-EnvValue "AIVF_DASHBOARD_USERS"
if (-not [string]::IsNullOrWhiteSpace($usersConfig)) {
    Write-Host ""
    Write-Host "Named dashboard accounts are configured; shared dashboard token login is disabled." -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "Dashboard token is stored in 06-CONFIG-AND-DEPLOYMENT/.env; it is deliberately not printed." -ForegroundColor DarkGray
}

if (-not $NoBrowser) {
    Write-Ok "Startup screen will transition to the welcome page now that the dashboard is healthy."
}

$stopCommand = "docker compose --env-file `"$EnvFile`" -f `"$ComposeFile`" down"
if (-not $RunChecks -or $SkipChecks) {
    Write-Ok "Dashboard startup completed; the developer test suite was not run."
    Write-Host ""
    Write-Host "Edit Factory is running." -ForegroundColor Cyan
    Write-Host "Stop it with: $stopCommand" -ForegroundColor White
    Write-Host "Run START-ALL.bat -RunChecks when you deliberately want to run lint, type checks, and tests." -ForegroundColor DarkGray
    exit 0
}

Write-Step "Running local code checks"

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
