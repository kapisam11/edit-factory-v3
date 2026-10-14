[CmdletBinding()]
param(
    [switch]$Migrate,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
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
        throw "A different image is already configured for this Compose project. Refusing an unbacked image upgrade. Use the controlled deployment/rollback procedure in 06-CONFIG-AND-DEPLOYMENT/README.md."
    }
}

Write-Host ""
Write-Host "Validating production Compose configuration..."
Invoke-Compose @("config", "--quiet")

Write-Host ""
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
    Start-Process "$baseUrl/login"
}
