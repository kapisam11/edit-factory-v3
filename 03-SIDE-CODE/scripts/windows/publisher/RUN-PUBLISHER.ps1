[CmdletBinding()]
param(
    [string]$UvPath = "",
    [switch]$Once
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
Set-Location -LiteralPath $Root

function Import-LocalEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).TrimStart() }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { continue }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$DeploymentEnv = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"
Import-LocalEnvironment $DeploymentEnv

if ([string]::IsNullOrWhiteSpace($env:AIVF_STATE_DIR)) {
    $env:AIVF_STATE_DIR = Join-Path $Root "state"
}
$StateDir = [Environment]::ExpandEnvironmentVariables($env:AIVF_STATE_DIR)
if (-not [System.IO.Path]::IsPathRooted($StateDir)) { $StateDir = Join-Path $Root $StateDir }
$StateDir = [System.IO.Path]::GetFullPath($StateDir)
$env:AIVF_STATE_DIR = $StateDir

$WatchDir = if ($env:AIVF_INCOMING_DIR) { $env:AIVF_INCOMING_DIR } else { Join-Path $Root "incoming" }
if (-not [System.IO.Path]::IsPathRooted($WatchDir)) { $WatchDir = Join-Path $Root $WatchDir }
$WatchDir = [System.IO.Path]::GetFullPath($WatchDir)

$DatabasePath = if ($env:AIVF_FOLDER_STATE_DB) { $env:AIVF_FOLDER_STATE_DB } else { Join-Path $StateDir "folder-publisher.sqlite" }
if (-not [System.IO.Path]::IsPathRooted($DatabasePath)) { $DatabasePath = Join-Path $Root $DatabasePath }
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)

if ([string]::IsNullOrWhiteSpace($UvPath)) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) { throw "uv was not found. Install uv or pass -UvPath to the runner." }
    $UvPath = $uvCommand.Source
}
if (-not (Test-Path -LiteralPath $UvPath -PathType Leaf)) { throw "uv executable was not found at the configured path." }

$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogPath = Join-Path $LogDir "platform-publisher.log"
$Mutex = New-Object System.Threading.Mutex($false, "Local\EditFactoryV3PlatformPublisher")
$ownsMutex = $false

try {
    try { $ownsMutex = $Mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw "Another local publisher runner already owns the single-instance mutex." }

    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        if ((Get-Item -LiteralPath $LogPath).Length -gt 10MB) {
            $rotated = "$LogPath.1"
            Remove-Item -LiteralPath $rotated -Force -ErrorAction SilentlyContinue
            Move-Item -LiteralPath $LogPath -Destination $rotated -Force
        }
    }

    $secretValues = [System.Collections.Generic.List[string]]::new()
    Get-ChildItem Env: | Where-Object { $_.Name -match '(?i)(TOKEN|SECRET|PASSWORD|CLIENT_KEY|ACCESS_KEY|REFRESH)' } | ForEach-Object {
        $value = [string]$_.Value
        if ($value.Length -ge 5) { $secretValues.Add($value) }
    }

    function Write-SafeLog([string]$Message) {
        $safe = $Message
        foreach ($secret in $secretValues) { if ($secret) { $safe = $safe.Replace($secret, "<REDACTED>") } }
        $safe = $safe -replace '(?i)(access_token|refresh_token|client_secret|client_key|authorization|password)\s*["'']?\s*[:=]\s*["'']?[^,\s"'']+', '$1=<REDACTED>'
        Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffK"), $safe) -Encoding UTF8
    }

    Write-SafeLog "Publisher runner starting. watch_dir=$WatchDir state_dir=$StateDir"
    Write-SafeLog ("Network publishing explicitly enabled: " + ($env:AIVF_MULTI_PUBLISH -eq "1"))
    if (-not (Test-Path -LiteralPath $WatchDir -PathType Container)) { New-Item -ItemType Directory -Path $WatchDir -Force | Out-Null }
    New-Item -ItemType Directory -Path (Split-Path -Parent $DatabasePath) -Force | Out-Null

    $uvArgs = @("run", "--frozen", "--extra", "youtube", "aivf-publish", "--watch-dir", $WatchDir, "--db", $DatabasePath)
    if ($env:AIVF_MULTI_PUBLISH -eq "1") { $uvArgs += "--publish" }
    if ($Once) { $uvArgs += "run-once" } else { $uvArgs += "daemon" }

    Write-SafeLog ("Starting durable platform queue in " + $(if ($Once) { "run-once" } else { "daemon" }) + " mode.")
    & $UvPath @uvArgs 2>&1 | ForEach-Object { Write-SafeLog ([string]$_) }
    $exitCode = $LASTEXITCODE
    Write-SafeLog "Publisher runner exited with code $exitCode."
    exit $exitCode
}
finally {
    if ($ownsMutex) { try { $Mutex.ReleaseMutex() } catch { } }
    $Mutex.Dispose()
}
