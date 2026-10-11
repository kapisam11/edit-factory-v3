[CmdletBinding()]
param([string]$WatchDir = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $WatchDir) { $WatchDir = $env:AIVF_INCOMING_DIR }
if (-not $WatchDir) { $WatchDir = Join-Path $env:USERPROFILE "Videos\EditFactory-Incoming" }
$WatchDir = [System.IO.Path]::GetFullPath($WatchDir)
$StateDir = $env:AIVF_STATE_DIR
if (-not $StateDir) { $StateDir = Join-Path $Root "state" }
$StateDir = [System.IO.Path]::GetFullPath($StateDir)
$EnvFile = $env:AIVF_PUBLISH_ENV_FILE
if (-not $EnvFile) { $EnvFile = Join-Path $env:USERPROFILE ".config\EditFactory\publisher.env" }

New-Item -ItemType Directory -Force -Path $WatchDir, $StateDir | Out-Null
try { $relative = [System.IO.Path]::GetRelativePath($WatchDir, $StateDir) }
catch { $relative = "" }
if ($relative -ne "" -and -not $relative.StartsWith("..") -and -not [System.IO.Path]::IsPathRooted($relative)) {
    throw "Publisher state must be outside the watched video folder."
}

# Import a private, simple KEY=VALUE file. Never print secret values or use eval.
if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2]
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$env:AIVF_STATE_DIR = $StateDir
$env:AIVF_PUBLISH_DB = if ($env:AIVF_PUBLISH_DB) { $env:AIVF_PUBLISH_DB } else { Join-Path $StateDir "multi-platform-publisher.sqlite" }
$env:AIVF_PUBLISH_STAGING_DIR = if ($env:AIVF_PUBLISH_STAGING_DIR) { $env:AIVF_PUBLISH_STAGING_DIR } else { Join-Path $StateDir "publisher-staging" }
$env:AIVF_INCOMING_DIR = $WatchDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not on PATH for the scheduled-task user. Install uv and retry."
}
$arguments = @("--project", $Root, "aivf-publish-queue", "--watch-dir", $WatchDir)
if ($env:AIVF_MULTI_PLATFORM_PUBLISH -eq "1") { $arguments += "--publish" } else { $arguments += "--dry-run" }
if ($env:AIVF_PUBLISH_ALLOW_PUBLIC -eq "1") { $arguments += "--allow-public" }
$arguments += "daemon"

Write-Output ("Edit Factory publisher starting in {0}; actual uploads enabled: {1}; public opt-in: {2}" -f $WatchDir, ($env:AIVF_MULTI_PLATFORM_PUBLISH -eq "1"), ($env:AIVF_PUBLISH_ALLOW_PUBLIC -eq "1"))
& uv run @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
