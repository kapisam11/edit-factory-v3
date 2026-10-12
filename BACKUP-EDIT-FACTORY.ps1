[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\docker-compose.dev.yml"
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Start Docker Desktop and retry."
}
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "Compose file not found: $ComposeFile"
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Local .env was not found. Start Edit Factory with START-ALL.bat once before backing it up."
}

$Destination = [System.IO.Path]::GetFullPath($Destination)
$SourceDrive = [System.IO.Path]::GetPathRoot($Root).TrimEnd('\')
$BackupDrive = [System.IO.Path]::GetPathRoot($Destination).TrimEnd('\')
if ([string]::IsNullOrWhiteSpace($BackupDrive)) {
    throw "Destination must be an absolute path on a separate drive, for example E:\EditFactoryBackups."
}
if ([string]::Equals($SourceDrive, $BackupDrive, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Backup destination must use a different drive letter than the repository ($SourceDrive)."
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $Destination /inheritance:r /grant:r "${Identity}:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict backup-folder permissions. Use an NTFS destination drive and retry."
}

$Prefix = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running." }

$RunningId = (& docker @($Prefix + @("ps", "-q", "web")) | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$RunningId)) {
    throw "The Edit Factory web container is not running. Start it with START-ALL.bat first."
}
$RunningId = ([string]$RunningId).Trim()

# Build the backup utility into the current checkout without restarting the running dashboard.
& docker @($Prefix + @("build", "web"))
if ($LASTEXITCODE -ne 0) { throw "Could not build the backup utility image." }

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Name = "edit-factory-v3-$Timestamp.tar.gz"
$ContainerBackup = "/app/state/backup-staging/$Name"
$ContainerVerify = "/app/state/backup-staging/verify-$Name"
$ArchivePath = Join-Path $Destination $Name

try {
    & docker @($Prefix + @(
        "run", "--rm", "--no-deps", "--entrypoint", "aivf-backup", "web",
        "backup",
        "--db", "/app/state/jobs.db",
        "--backup", $ContainerBackup,
        "--knowledge", "/app/knowledge_base_v3",
        "--output", "/app/output",
        "--uploads", "/app/uploads",
        "--state", "/app/state"
    ))
    if ($LASTEXITCODE -ne 0) { throw "Backup creation failed inside the container." }

    & docker cp "${RunningId}:${ContainerBackup}" $ArchivePath
    if ($LASTEXITCODE -ne 0) { throw "Could not copy the backup archive to $ArchivePath." }

    & icacls $ArchivePath /inheritance:r /grant:r "${Identity}:F" "SYSTEM:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict archive permissions on the destination drive." }

    # Verify the exact copy that landed on the destination drive, not only the source archive.
    & docker cp $ArchivePath "${RunningId}:${ContainerVerify}"
    if ($LASTEXITCODE -ne 0) { throw "Could not stage the copied archive for verification." }

    & docker @($Prefix + @(
        "run", "--rm", "--no-deps", "--entrypoint", "aivf-backup", "web",
        "verify", "--backup", $ContainerVerify, "--marker", "/tmp/aivf-backup-verified.json"
    ))
    if ($LASTEXITCODE -ne 0) { throw "Backup verification failed. Do not rely on this archive." }

    $Hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
    Write-Host "Verified Edit Factory backup: $ArchivePath" -ForegroundColor Green
    Write-Host "SHA-256: $Hash"
    Write-Host "State token/secret files are intentionally excluded; authorize provider accounts again after restore."
    Write-Host "The archive is not encrypted. Keep it on a trusted, access-restricted drive."
}
finally {
    & docker exec $RunningId sh -lc "rm -f '$ContainerBackup' '$ContainerVerify'" *> $null
}
