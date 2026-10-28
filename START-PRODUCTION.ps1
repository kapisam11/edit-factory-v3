[CmdletBinding()]
param(
    [switch]$Migrate,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
# Handle native exit codes explicitly below, including the DB-not-found sentinel.
$PSNativeCommandUseErrorActionPreference = $false
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$ComposeFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\docker-compose.yml"
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"

function Get-EnvFileValue([string]$Name) {
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        return $null
    }

    $line = Get-Content -LiteralPath $EnvFile | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -First 1

    if ($null -eq $line) {
        return $null
    }

    $value = ($line -replace "^\s*$([regex]::Escape($Name))\s*=\s*", "").Trim()
    return $value.Trim([char]'"').Trim([char]"'")
}

function Get-ConfiguredValue([string]$Name) {
    $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue.Trim().Trim([char]'"').Trim([char]"'")
    }
    return Get-EnvFileValue $Name
}

function Test-UsableSecret([string]$Value) {
    $candidate = if ($null -eq $Value) { "" } else { $Value.Trim().Trim([char]'"').Trim([char]"'") }
    if ($candidate.Length -lt 32) {
        return $false
    }
    if ($candidate -match "(?i)change[\s_-]*me|placeholder|replace[\s_-]*me|your[-_ ]?(secret|token)|^(secret|password|local-development)$") {
        return $false
    }
    return $true
}

function Invoke-Compose([string[]]$ComposeArguments) {
    & docker compose --env-file $EnvFile -f $ComposeFile @ComposeArguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "docker compose failed with exit code ${exitCode}: $($ComposeArguments -join ' ')"
    }
}

if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "Production Compose file was not found: $ComposeFile"
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Production environment file was not found: $EnvFile. Configure a pinned AIVF_IMAGE and strong secrets first; see 06-CONFIG-AND-DEPLOYMENT/README.md."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install Docker Desktop, then retry."
}

$image = Get-ConfiguredValue "AIVF_IMAGE"
if ([string]::IsNullOrWhiteSpace($image) -or $image -notmatch '^.+@sha256:[0-9a-fA-F]{64}$') {
    throw "AIVF_IMAGE must be a pinned immutable image reference ending in @sha256:<64 hex characters>. Production launch will not build source or use a mutable tag."
}
$flaskSecret = Get-ConfiguredValue "FLASK_SECRET_KEY"
$dashboardToken = Get-ConfiguredValue "AIVF_DASHBOARD_TOKEN"
if (-not (Test-UsableSecret $flaskSecret)) {
    throw "FLASK_SECRET_KEY is missing, too short, or a known placeholder. Configure a generated secret in 06-CONFIG-AND-DEPLOYMENT/.env."
}
if (-not (Test-UsableSecret $dashboardToken)) {
    throw "AIVF_DASHBOARD_TOKEN is missing, too short, or a known placeholder. Configure a generated token in 06-CONFIG-AND-DEPLOYMENT/.env."
}

# These process values take precedence over the development values that may
# exist in the shared .env file. Production Compose also fixes them explicitly.
$env:AIVF_IMAGE = $image
$env:FLASK_SECRET_KEY = $flaskSecret
$env:AIVF_DASHBOARD_TOKEN = $dashboardToken
$env:AIVF_ENV = "production"
$env:AIVF_COOKIE_SECURE = "1"
$env:AIVF_ALLOW_RUNTIME_MIGRATIONS = "0"

$port = Get-ConfiguredValue "AIVF_DASHBOARD_PORT"
if ([string]::IsNullOrWhiteSpace($port)) {
    $port = "5000"
}
if ($port -notmatch '^\d{1,5}$' -or [int]$port -lt 1 -or [int]$port -gt 65535) {
    throw "AIVF_DASHBOARD_PORT must be a TCP port number between 1 and 65535."
}
$env:AIVF_DASHBOARD_PORT = [string][int]$port
$baseUrl = "http://127.0.0.1:$([int]$port)"

Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "     EDIT FACTORY V3 - PRODUCTION START" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "Using the pinned production image. This script does not build from source."
Write-Host "The dashboard will bind to localhost only."

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start Docker Desktop and retry."
}

# Refuse to silently replace a different version. Release upgrades belong to
# the backup/rollback deployment procedure, not this simple start command.
$existingIds = @(& docker compose --env-file $EnvFile -f $ComposeFile ps --all -q web)
$psExitCode = $LASTEXITCODE
if ($psExitCode -ne 0) {
    throw "Could not inspect the existing production Compose project (exit code $psExitCode)."
}
$existingId = $existingIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
if ($existingId) {
    $existingImage = (& docker inspect --format '{{.Config.Image}}' $existingId).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the existing web container; refusing to replace it."
    }
    if ($existingImage -ne $image) {
        if ($existingImage -eq "edit-factory-v3:dev") {
            throw "The development stack is still using this Compose project. Stop it first with: docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml down. No production container was changed."
        }
        throw "A different image is already configured for this Compose project. Refusing an unbacked image upgrade. Use the controlled deployment/rollback procedure in 06-CONFIG-AND-DEPLOYMENT/README.md."
    }
}

Write-Host ""
Write-Host "Validating production Compose configuration..."
Invoke-Compose @("config", "--quiet")

Write-Host ""
function Start-StartupSplash {
    if ($NoBrowser) {
        return
    }

    $splashSource = Join-Path $Root "02-WEB-FILES\static\startup-splash.html"
    $welcomeSource = Join-Path $Root "02-WEB-FILES\templates\welcome.html"
    if (-not (Test-Path -LiteralPath $splashSource -PathType Leaf) -or
        -not (Test-Path -LiteralPath $welcomeSource -PathType Leaf)) {
        Write-Host "Startup splash or welcome page is missing; continuing without the pre-login intro." -ForegroundColor Yellow
        return
    }

    $splashPath = Join-Path $env:TEMP ("EditFactoryStartup-" + [guid]::NewGuid().ToString("N") + ".html")
    $welcomePath = Join-Path $env:TEMP ("EditFactoryWelcome-" + [guid]::NewGuid().ToString("N") + ".html")
    $welcomeHtml = (Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw).Replace('href="/login"', ('href="' + $baseUrl + '/login"'))
    Set-Content -LiteralPath $welcomePath -Value $welcomeHtml -Encoding UTF8
    $welcomeUri = ([System.Uri]::new($welcomePath)).AbsoluteUri

    $splashHtml = (Get-Content -LiteralPath $splashSource -Encoding UTF8 -Raw).Replace("__AIVF_BASE_URL__", $baseUrl).Replace("__AIVF_WELCOME_URI__", $welcomeUri)
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
        Write-Host "Fullscreen kiosk startup screen opened in an isolated browser profile." -ForegroundColor Green
    } else {
        Start-Process $splashUri
        Write-Host "Chrome/Edge not found; default browser opened (fullscreen cannot be guaranteed)." -ForegroundColor Yellow
    }
}

function Start-LocalAccessDetails {
    if ($NoBrowser) {
        return
    }

    $accessDetailsScript = Join-Path $Root "SHOW-LOCAL-CREDENTIALS.ps1"
    if (-not (Test-Path -LiteralPath $accessDetailsScript -PathType Leaf)) {
        Write-Host "Access Details helper is missing; use the local .env file to recover your token."
        return
    }

    $powershellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powershellCommand) {
        Write-Host "Could not launch the Access Details window. Double-click SHOW-LOCAL-CREDENTIALS.bat after startup."
        return
    }

    try {
        Start-Process -FilePath $powershellCommand.Source -ArgumentList @(
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            ('"{0}"' -f $accessDetailsScript)
        ) -WindowStyle Hidden | Out-Null
        Write-Host "Access Details opened separately. Reopen it any time with SHOW-LOCAL-CREDENTIALS.bat."
    } catch {
        Write-Host "Could not open the Access Details window; double-click SHOW-LOCAL-CREDENTIALS.bat after startup."
    }
}

Start-StartupSplash

Write-Host "Pulling the pinned production image..."
Invoke-Compose @("pull")

if ($Migrate) {
    # Detect first boot without treating ordinary Docker/Compose errors as an
    # absent database. Exit 42 is the deliberate 'DB does not exist' signal.
    & docker compose --env-file $EnvFile -f $ComposeFile run --rm --no-deps -T web sh -c "if test -f /app/state/jobs.db; then exit 0; else exit 42; fi"
    $dbCheckCode = $LASTEXITCODE
    if ($dbCheckCode -eq 0) {
        $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $backupPath = "/app/state/backups/pre-migration-$stamp.tar.gz"
        $backupMarker = "${backupPath}.verified"
        $backupCommand = "mkdir -p /tmp/aivf-empty /app/state/backups && aivf-backup backup --db /app/state/jobs.db --backup $backupPath --knowledge /tmp/aivf-empty --output /tmp/aivf-empty --uploads /tmp/aivf-empty --state /tmp/aivf-empty && aivf-backup verify --backup $backupPath --marker $backupMarker"
        Write-Host "Existing database detected; creating and verifying a local pre-migration database backup..."
        Invoke-Compose @("run", "--rm", "--no-deps", "-T", "web", "sh", "-c", $backupCommand)
        Write-Host "Verified pre-migration backup: $backupPath"
    } elseif ($dbCheckCode -ne 42) {
        throw "Could not determine whether the production database exists (exit code $dbCheckCode). Migration was not run."
    }

    Write-Host "Running the explicit production database migration..."
    Invoke-Compose @("run", "--rm", "--no-deps", "-T", "web", "aivf-db-migrate", "/app/state/jobs.db")
} else {
    Write-Host "Verifying the existing database schema without modifying it..."
    $verifyCode = "from ai_video_factory.db_migrations import verify_database_schema; verify_database_schema('/app/state/jobs.db')"
    try {
        Invoke-Compose @("run", "--rm", "--no-deps", "-T", "web", "python", "-c", $verifyCode)
    } catch {
        throw "Production database schema verification failed. For first start or a schema-changing release, rerun explicitly with -Migrate after reviewing the backup and migration procedure. Details: $($_.Exception.Message)"
    }
}



Write-Host ""
Write-Host "Starting the production services..."
Invoke-Compose @("up", "-d", "--no-build", "--remove-orphans")

Write-Host "Waiting for the dashboard health check..."
$healthy = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/api/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # Allow the pinned production container time to complete startup.
    }
    Start-Sleep -Seconds 1
}

if (-not $healthy) {
    throw "Production dashboard did not become healthy. Inspect the web service logs with docker compose logs --tail 100 web."
}

Invoke-Compose @("ps")
Write-Host ""
Write-Host "Production dashboard is healthy at $baseUrl" -ForegroundColor Green
Write-Host "Dashboard token is stored in the configured .env file; it is deliberately not printed."
Write-Host "Autonomous publishing is not started by default. The automation profile is separate and remains opt-in."

if (-not $NoBrowser) {
    Write-Host "Startup screen will transition to the welcome page when the dashboard is healthy."
    Start-LocalAccessDetails
}
