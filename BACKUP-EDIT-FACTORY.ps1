[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
    [ValidateRange(1, 365)]
    [int]$RetentionDays = 14
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $Root '06-CONFIG-AND-DEPLOYMENT\docker-compose.dev.yml'
$EnvFile = Join-Path $Root '06-CONFIG-AND-DEPLOYMENT\.env'

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code ${LASTEXITCODE}: docker $($Arguments -join ' ')"
    }
}

$script:BackupLogPath = $null
trap {
    if ($script:BackupLogPath) {
        Add-Content -LiteralPath $script:BackupLogPath -Value ("[{0}] ERROR: {1}" -f (Get-Date -Format "o"), $_) -Encoding UTF8
    }
    throw
}

function Write-BackupLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "o"), $Message
    if ($script:BackupLogPath) {
        Add-Content -LiteralPath $script:BackupLogPath -Value $line -Encoding UTF8
    }
    Write-Host $Message
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Install Docker Desktop first.'
}
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "Development Compose file was not found: $ComposeFile"
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Local environment file was not found: $EnvFile. Run START-ALL.bat once to create the local dashboard configuration, then retry."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Desktop is not running. Start it and wait until the daemon is ready.'
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$ResolvedBackupRoot = (Resolve-Path -LiteralPath $Destination).Path
$RepoDrive = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Root))
$BackupDrive = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($ResolvedBackupRoot))
if ([string]::IsNullOrWhiteSpace($BackupDrive) -or $RepoDrive.Equals($BackupDrive, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Choose a backup destination on another drive. Repository drive: $RepoDrive; destination drive: $BackupDrive"
}
$script:BackupLogPath = Join-Path $ResolvedBackupRoot "edit-factory-backup.log"
Write-BackupLog "Backup run started. Destination=$ResolvedBackupRoot; retention_days=$RetentionDays"

$ArchiveName = 'edit-factory-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.tar.gz'
$ArchiveHostPath = Join-Path $ResolvedBackupRoot $ArchiveName
$Volume = $ResolvedBackupRoot + ':/backup'
$ComposePrefix = @('compose', '--env-file', $EnvFile, '-f', $ComposeFile, 'run', '--rm', '--no-deps', '--volume', $Volume, 'web')
$BackupCommand = "mkdir -p /backup/.work && export TMPDIR=/backup/.work && aivf-backup backup --db /app/state/jobs.db --state /app/state --knowledge /app/knowledge_base_v3 --output /app/output --uploads /app/uploads --backup /backup/$ArchiveName"

Write-BackupLog "Creating backup on $ResolvedBackupRoot ..."
Invoke-Docker ($ComposePrefix + @('sh', '-ec', $BackupCommand))

$VerifyCommand = "mkdir -p /backup/.work && export TMPDIR=/backup/.work && aivf-backup verify --backup /backup/$ArchiveName --marker /app/state/backup-restore-verified.json"
Write-BackupLog "Verifying archive hashes and SQLite integrity ..."
Invoke-Docker ($ComposePrefix + @('sh', '-ec', $VerifyCommand))

$RestoreCommand = @'
set -eu
mkdir -p /backup/.work
export TMPDIR=/backup/.work
trap 'rm -rf /backup/.restore-smoke' EXIT
aivf-backup restore --backup '/backup/ARCHIVE_PLACEHOLDER' --destination /backup/.restore-smoke
python -c '
import hashlib, json
from pathlib import Path
root = Path("/backup/.restore-smoke")
manifest = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()
bad = []
for entry in manifest.get("files", []):
    path = root / str(entry.get("name"))
    if not path.is_file() or digest(path) != str(entry.get("sha256")):
        bad.append(str(entry.get("name")))
assert not bad, "restored files differ from backup manifest: " + ", ".join(bad[:10])
assert (root / "jobs.db").is_file()
print("restore drill passed; verified files:", len(manifest.get("files", [])))
'
'@
$RestoreCommand = $RestoreCommand -replace 'ARCHIVE_PLACEHOLDER', $ArchiveName
Write-BackupLog "Running isolated restore drill; live volumes are not overwritten ..."
Invoke-Docker ($ComposePrefix + @('sh', '-ec', $RestoreCommand))

$Cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -LiteralPath $ResolvedBackupRoot -Filter 'edit-factory-backup-*.tar.gz' -File |
    Where-Object { $_.LastWriteTime -lt $Cutoff } |
    Remove-Item -Force

Write-BackupLog "SUCCESS: archive=$ArchiveHostPath"
Write-BackupLog "Retention: $RetentionDays days. Contents include database/state, knowledge, output and uploads."
Write-BackupLog "Token vaults, OAuth secrets and environment files are excluded. Protect the external drive with BitLocker."
