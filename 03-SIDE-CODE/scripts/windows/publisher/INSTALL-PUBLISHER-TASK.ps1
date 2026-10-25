[CmdletBinding()]
param(
    [string]$TaskName = "EditFactoryV3-PlatformPublisher",
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyWakeTime = "20:00",
    [switch]$StartNow,
    [switch]$UseInteractiveLogon
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
$Runner = Join-Path $PSScriptRoot "RUN-PUBLISHER.ps1"
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
$WakeParts = $DailyWakeTime.Split(":")
$WakeAt = (Get-Date).Date.AddHours([int]$WakeParts[0]).AddMinutes([int]$WakeParts[1])
$StartupTrigger = New-ScheduledTaskTrigger -AtStartup
$LogOnTrigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$DailyWakeTrigger = New-ScheduledTaskTrigger -Daily -At $WakeAt
$Triggers = @($StartupTrigger, $LogOnTrigger, $DailyWakeTrigger)
if ($UseInteractiveLogon) {
    $Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
} else {
    # S4U logon deliberately has no network access; publishers need outbound API access.
    $Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Password -RunLevel Limited
}
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$Task = New-ScheduledTask -Action $Action -Trigger $Triggers -Principal $Principal -Settings $Settings -Description "Runs Edit Factory v3's durable per-platform publishing queue independently of the dashboard. Real uploads remain disabled unless AIVF_MULTI_PUBLISH=1."

if ($UseInteractiveLogon) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
    Write-Host "Interactive logon selected: the task can run only while this user is logged on." -ForegroundColor Yellow
} else {
    $Credential = Get-Credential -UserName $Identity -Message "Enter the Windows account password (not the Windows Hello PIN). Task Scheduler stores this credential securely so the publisher can run after reboot without a logged-in session."
    if (-not $Credential) { throw "No Windows credentials supplied; the scheduled task was not registered." }
    $TaskPassword = $null
    try {
        if (-not [string]::Equals([string]$Credential.UserName, $Identity, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Credential username must match the current Windows identity '$Identity'."
        }
        $TaskPassword = $Credential.GetNetworkCredential().Password
        Register-ScheduledTask -TaskName $TaskName -InputObject $Task -User $Identity -Password $TaskPassword -Force | Out-Null
    } finally {
        $TaskPassword = $null
        if ($Credential -and $Credential.Password) { $Credential.Password.Dispose() }
        $Credential = $null
    }
}
Write-Host "Registered scheduled task: $TaskName" -ForegroundColor Green
Write-Host "Runner: $Runner"
Write-Host "Logs: <AIVF_STATE_DIR>\logs\platform-publisher.log"
Write-Host "Daily wake trigger: $DailyWakeTime local time (Windows may still be prevented from waking by power-plan or firmware settings)."
Write-Host "Network uploads are opt-in. Set AIVF_MULTI_PUBLISH=1 only after reviewing queued destinations and credentials."
Write-Host "The task runs as '$Identity' with least privilege; no administrator elevation is requested."
if (-not $UseInteractiveLogon) { Write-Host "Task Scheduler stores the supplied logon credential; it is not written to this repository." }
else { Write-Host "Interactive mode will not run before login; use the default password logon for unattended reboot recovery." }
Write-Host "Wake-from-sleep additionally requires Windows wake timers and firmware/power settings to permit waking."
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started task: $TaskName"
}
