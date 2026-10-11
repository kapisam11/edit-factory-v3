[CmdletBinding()]
param(
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyTime = "12:00",
    [string]$TaskName = "Edit Factory v3 Daily Folder Publisher"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Root "RUN-DAILY-PUBLISH.ps1"
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Publishing runner not found: $Runner"
}

$At = [datetime]::ParseExact($DailyTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$ActionArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $Runner + '"'
$Action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME "powershell.exe") -Argument $ActionArguments -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType S4U -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Scan the Edit Factory inbox and process at most the configured number of approved videos per day."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Registered '$TaskName' to run daily at $DailyTime (Windows local time)."
Write-Host "The task starts missed runs when the PC is available, requests wake from sleep, restarts failures, and ignores overlapping runs."
Write-Host "A PC that is shut down cannot upload; test wake timers and the task history on this machine."
