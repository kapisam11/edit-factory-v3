[CmdletBinding()]
param(
    [string]$TaskName = "EditFactoryV3-PlatformPublisher",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Root "RUN-PUBLISHER.ps1"
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw "Publisher runner not found: $Runner" }

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) { throw "Install uv for this Windows user before creating the scheduled task." }
$UvPath = $uvCommand.Source
if (-not (Test-Path -LiteralPath $UvPath -PathType Leaf)) { throw "uv executable path is not a regular file." }

$PowerShellPath = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellPath -PathType Leaf)) {
    $pwshCommand = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwshCommand) { throw "PowerShell was not found." }
    $PowerShellPath = $pwshCommand.Source
}

$Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -UvPath "{1}"' -f $Runner, $UvPath
$Action = New-ScheduledTaskAction -Execute $PowerShellPath -Argument $Arguments -WorkingDirectory $Root
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $Identity))
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType S4U -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$Task = New-ScheduledTask -Action $Action -Trigger $Triggers -Principal $Principal -Settings $Settings -Description "Runs Edit Factory v3's durable per-platform publishing queue independently of the dashboard. Real uploads remain disabled unless AIVF_MULTI_PUBLISH=1."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Registered scheduled task: $TaskName" -ForegroundColor Green
Write-Host "Runner: $Runner"
Write-Host "Logs: <AIVF_STATE_DIR>\logs\platform-publisher.log"
Write-Host "Network uploads are opt-in. Set AIVF_MULTI_PUBLISH=1 only after reviewing queued destinations and credentials."
Write-Host "The task uses a least-privilege, passwordless S4U logon for '$Identity'; no administrator elevation is requested."
Write-Host "Wake-from-sleep additionally requires Windows wake timers and firmware/power settings to permit waking."
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started task: $TaskName"
}
