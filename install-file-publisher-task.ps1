[CmdletBinding()]
param([string]$TaskName = "EditFactory-Existing-File-Publisher")

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $PSScriptRoot).Path
$EnvFile = Join-Path $RepoRoot "publish-folder.env"
$EnvExample = Join-Path $RepoRoot "publish-folder.env.example"

if (-not (Test-Path $EnvFile)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host "Created publish-folder.env. Fill in the watch path and account credentials, then run this installer again."
    exit 1
}

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw "uv was not found on PATH. Install uv, reopen PowerShell, and run this script again."
}

$CurrentUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$Arguments = 'run --project "' + $RepoRoot + '" --extra youtube python -m ai_video_factory.existing_file_publisher daemon'
$Action = New-ScheduledTaskAction -Execute $Uv.Source -Argument $Arguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -WakeToRun
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited

# Restrict local credentials/config to the current Windows account.
& icacls.exe $EnvFile /inheritance:r /grant:r ($CurrentUser + ":(F)") | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict permissions on publish-folder.env"
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Watch stable existing video files and publish with durable per-platform state." -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Registered and started scheduled task: $TaskName"
Write-Host "The worker starts at the current user's logon and restarts after a process failure."
Write-Host "A powered-off PC cannot publish while off. Pending work is picked up after the next logon."
Write-Host ("View job state with: uv run --project " + $RepoRoot + " --extra youtube python -m ai_video_factory.existing_file_publisher status")
