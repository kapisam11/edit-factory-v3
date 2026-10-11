[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Match the local dashboard's .env location without ever echoing its values.
$EnvFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\.env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

if (-not $env:AIVF_INBOX_DIR) {
    $env:AIVF_INBOX_DIR = Join-Path $Root "inbox"
}
if (-not $env:AIVF_STATE_DIR) {
    $env:AIVF_STATE_DIR = Join-Path $Root "state"
}
if (-not $env:YOUTUBE_AUTH_INTERACTIVE) {
    $env:YOUTUBE_AUTH_INTERACTIVE = "0"
}

$Uv = Get-Command "uv" -ErrorAction SilentlyContinue
if ($Uv) {
    & $Uv.Source run --project $Root --extra youtube aivf-folder-publish run-once
    if ($LASTEXITCODE -ne 0) {
        throw "Folder publishing run failed with exit code $LASTEXITCODE."
    }
    exit 0
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $Python) {
    & $Python -m ai_video_factory.folder_publisher run-once
    if ($LASTEXITCODE -ne 0) {
        throw "Folder publishing run failed with exit code $LASTEXITCODE."
    }
    exit 0
}

throw "Install uv, or create .venv and install the project with the YouTube extra. See 00-INFO/FOLDER-PUBLISHING.md."
