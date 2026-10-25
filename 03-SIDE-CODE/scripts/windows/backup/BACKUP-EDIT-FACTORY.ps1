[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
    [ValidateRange(1, 365)]
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
function Get-EditFactoryRepositoryRoot([string]$StartPath) {
    $candidate = [System.IO.DirectoryInfo]::new((Resolve-Path -LiteralPath $StartPath).Path)
    while ($null -ne $candidate) {
        $projectFile = Join-Path $candidate.FullName "pyproject.toml"
        $mainCodeDir = Join-Path $candidate.FullName "01-MAIN-CODE"
        if ((Test-Path -LiteralPath $projectFile -PathType Leaf) -and
            (Test-Path -LiteralPath $mainCodeDir -PathType Container)) {
            return $candidate.FullName
        }
        $candidate = $candidate.Parent
    }
    throw "Could not locate the Edit Factory repository root from '$StartPath'."
}

$Root = Get-EditFactoryRepositoryRoot -StartPath $PSScriptRoot
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

$PsArgs = $Prefix + @("ps", "-q", "web")
$RunningId = (& docker @PsArgs | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$RunningId)) {
    throw "The Edit Factory web container is not running. Start it with START-ALL.bat first."
}
$RunningId = ([string]$RunningId).Trim()

# Build the backup utility into the current checkout without restarting the running dashboard.
$BuildArgs = $Prefix + @("build", "web")
& docker @BuildArgs
if ($LASTEXITCODE -ne 0) { throw "Could not build the backup utility image." }

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Name = "edit-factory-v3-$Timestamp.tar.gz"
$ContainerBackup = "/app/state/backup-staging/$Name"
$ContainerVerify = "/app/state/backup-staging/verify-$Name"
$ContainerRestore = "/app/state/backup-staging/restore-drill-$Name"
$ArchivePath = Join-Path $Destination $Name

$BackupArgs = $Prefix + @(
    "run", "--rm", "--no-deps", "--entrypoint", "aivf-backup", "web",
    "backup",
    "--db", "/app/state/jobs.db",
    "--backup", $ContainerBackup,
    "--knowledge", "/app/knowledge_base_v3",
    "--output", "/app/output",
    "--uploads", "/app/uploads",
    "--state", "/app/state"
)
$VerifyArgs = $Prefix + @(
    "run", "--rm", "--no-deps", "--entrypoint", "aivf-backup", "web",
    "verify", "--backup", $ContainerVerify, "--marker", "/tmp/aivf-backup-verified.json"
)
$RestoreArgs = $Prefix + @(
    "run", "--rm", "--no-deps", "--entrypoint", "aivf-backup", "web",
    "restore", "--backup", $ContainerVerify, "--destination", $ContainerRestore
)

# Independently verify the extracted restore against the archive manifest, including DB integrity.
$RestoreCheckCode = @"
import hashlib, json, sqlite3
from pathlib import Path
root = Path(r"$ContainerRestore").resolve()
manifest = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
verified = 0
for entry in manifest.get("files", []):
    name = str(entry.get("name") or "")
    path = (root / name).resolve()
    path.relative_to(root)
    if not path.is_file() or digest(path) != str(entry.get("sha256") or "").lower():
        raise SystemExit("restored file missing or hash mismatch: " + name)
    if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
        with sqlite3.connect(str(path)) as connection:
            if str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                raise SystemExit("restored SQLite integrity check failed: " + name)
    verified += 1
if not (root / "jobs.db").is_file():
    raise SystemExit("restored jobs.db is missing")
print("restore drill passed; verified files:", verified)
"@
$RestoreCheckArgs = $Prefix + @(
    "run", "--rm", "--no-deps", "--entrypoint", "python", "web",
    "-c", $RestoreCheckCode
)

try {
    & docker @BackupArgs
    if ($LASTEXITCODE -ne 0) { throw "Backup creation failed inside the container." }

    & docker cp "${RunningId}:${ContainerBackup}" $ArchivePath
    if ($LASTEXITCODE -ne 0) { throw "Could not copy the backup archive to $ArchivePath." }

    & icacls $ArchivePath /inheritance:r /grant:r "${Identity}:F" "SYSTEM:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict archive permissions on the destination drive." }

    # Verify the exact copy that landed on the destination drive, not only the source archive.
    & docker cp $ArchivePath "${RunningId}:${ContainerVerify}"
    if ($LASTEXITCODE -ne 0) { throw "Could not stage the copied archive for verification." }

    & docker @VerifyArgs
    if ($LASTEXITCODE -ne 0) { throw "Backup verification failed. Do not rely on this archive." }

    # Restore the verified external copy into a new, isolated directory; never touch live data paths.
    & docker @RestoreArgs
    if ($LASTEXITCODE -ne 0) { throw "Isolated restore drill failed. Do not rely on this archive." }
    & docker @RestoreCheckArgs
    if ($LASTEXITCODE -ne 0) { throw "Restored-file/hash/SQLite verification failed. Do not rely on this archive." }

    $Hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
    Write-Host "Verified Edit Factory backup: $ArchivePath" -ForegroundColor Green
    Write-Host "SHA-256: $Hash"
    Write-Host "State token/secret files are intentionally excluded; authorize provider accounts again after restore."
    Write-Host "The archive is not encrypted. Enable BitLocker on the external drive and keep it access-restricted."
    $Cutoff = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $Destination -Filter "edit-factory-v3-*.tar.gz" -File |
        Where-Object { $_.LastWriteTime -lt $Cutoff } |
        Remove-Item -Force
}
finally {
    if (-not [string]::IsNullOrWhiteSpace([string]$RunningId)) {
        & docker exec $RunningId sh -lc "rm -f '$ContainerBackup' '$ContainerVerify'; rm -rf '$ContainerRestore'" *> $null
    }
}
