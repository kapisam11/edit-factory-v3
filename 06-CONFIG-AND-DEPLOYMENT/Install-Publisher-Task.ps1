[CmdletBinding()]
param(
    [string]$WatchDir = (Join-Path $env:USERPROFILE "Videos\EditFactory-Incoming"),
    [string[]]$WakeTimesUtc = @("11:55", "17:55"),
    [string]$TaskName = "EditFactoryV3-MultiPlatformPublisher"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "RUN-PUBLISHER.ps1"
if (-not (Test-Path -LiteralPath $Runner)) { throw "Publisher runner is missing: $Runner" }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Install uv and make it available on PATH before installing this task." }
$WatchDir = [System.IO.Path]::GetFullPath($WatchDir)
$StateDir = Join-Path $Root "state"
New-Item -ItemType Directory -Force -Path $WatchDir, $StateDir | Out-Null
try { $relative = [System.IO.Path]::GetRelativePath($WatchDir, $StateDir) }
catch { $relative = "" }
if ($relative -ne "" -and -not $relative.StartsWith("..") -and -not [System.IO.Path]::IsPathRooted($relative)) {
    throw "State directory must be outside the watched video folder."
}

$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $Runner + '" -WatchDir "' + $WatchDir + '"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $Root
$triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
foreach ($wakeTime in $WakeTimesUtc) {
    if ($wakeTime -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') { throw "Wake time must use HH:MM 24-hour format: $wakeTime" }
    $triggers += New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($wakeTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture))
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero)
$identity = if ($env:USERDOMAIN) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { $env:USERNAME }
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description "Edit Factory v3 durable publishing queue. Uses the current user's limited session and persistent SQLite state." -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $identity." -ForegroundColor Green
Write-Host "Input folder: $WatchDir"
Write-Host "State folder: $StateDir"
Write-Host "This task starts after the user logs on. Daily wake triggers at $($WakeTimesUtc -join ', ') require Windows wake timers to be enabled in power settings."
Write-Host "Uploads are disabled unless AIVF_MULTI_PLATFORM_PUBLISH=1 is set in the private publisher env file."
Write-Host "Run: Start-ScheduledTask -TaskName '$TaskName'"
