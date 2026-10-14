$ErrorActionPreference = 'Stop'

function Test-FfmpegBinDirectory {
    param([Parameter(Mandatory = $true)][string]$Directory)

    $ffmpegPath = Join-Path $Directory 'ffmpeg.exe'
    $ffprobePath = Join-Path $Directory 'ffprobe.exe'
    if (-not (Test-Path $ffmpegPath -PathType Leaf) -or -not (Test-Path $ffprobePath -PathType Leaf)) {
        return $false
    }

    # Verify the executables, not merely Chocolatey shim filenames.
    try {
        & $ffmpegPath -version *> $null
        $ffmpegExit = $LASTEXITCODE
        & $ffprobePath -version *> $null
        $ffprobeExit = $LASTEXITCODE
        return ($ffmpegExit -eq 0 -and $ffprobeExit -eq 0)
    }
    catch {
        return $false
    }
}

function Find-FfmpegBinDirectory {
    # A successful package-manager process is not sufficient: Chocolatey can
    # report success while its feed has failed. Accept only a directory that
    # actually contains both executables used by the media tests.
    $searchRoots = [System.Collections.Generic.List[string]]::new()
    $ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $ffprobeCommand = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($ffmpegCommand -and $ffprobeCommand) {
        $ffmpegPath = $ffmpegCommand.Source
        $ffprobePath = $ffprobeCommand.Source
        if ($ffmpegPath -and $ffprobePath) {
            $ffmpegDirectory = Split-Path -Parent $ffmpegPath
            $ffprobeDirectory = Split-Path -Parent $ffprobePath
            if ($ffmpegDirectory -eq $ffprobeDirectory -and (Test-FfmpegBinDirectory -Directory $ffmpegDirectory)) {
                return $ffmpegDirectory
            }
            $searchRoots.Add($ffmpegDirectory)
            $searchRoots.Add($ffprobeDirectory)
        }
    }

    if ($env:ChocolateyInstall) {
        $searchRoots.Add((Join-Path $env:ChocolateyInstall 'bin'))
        $chocolateyLib = Join-Path $env:ChocolateyInstall 'lib'
        if (Test-Path $chocolateyLib -PathType Container) {
            Get-ChildItem -Path $chocolateyLib -Directory -Filter '*ffmpeg*' -ErrorAction SilentlyContinue |
                ForEach-Object { $searchRoots.Add($_.FullName) }
        }
    }

    $wingetRoots = @()
    if ($env:LOCALAPPDATA) {
        $wingetRoots += Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    }
    if ($env:ProgramFiles) {
        $wingetRoots += Join-Path $env:ProgramFiles 'WinGet\Packages'
    }
    $wingetRoots = $wingetRoots | Where-Object { Test-Path $_ -PathType Container }

    foreach ($wingetRoot in $wingetRoots) {
        Get-ChildItem -Path $wingetRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'ffmpeg' } |
            ForEach-Object { $searchRoots.Add($_.FullName) }
    }

    foreach ($knownRoot in @('C:\ffmpeg', 'C:\Program Files\FFmpeg')) {
        if (Test-Path $knownRoot -PathType Container) {
            $searchRoots.Add($knownRoot)
        }
    }

    foreach ($root in ($searchRoots | Select-Object -Unique)) {
        if (-not (Test-Path $root -PathType Container)) {
            continue
        }

        $directFfmpeg = Join-Path $root 'ffmpeg.exe'
        $directFfprobe = Join-Path $root 'ffprobe.exe'
        if (Test-FfmpegBinDirectory -Directory $root) {
            return $root
        }

        $candidates = Get-ChildItem -Path $root -Filter 'ffmpeg.exe' -File -Recurse -ErrorAction SilentlyContinue
        foreach ($candidate in $candidates) {
            if (Test-FfmpegBinDirectory -Directory $candidate.DirectoryName) {
                return $candidate.DirectoryName
            }
        }
    }

    return $null
}

$binDirectory = Find-FfmpegBinDirectory
$chocoExitCode = $null
$wingetExitCode = $null

if (-not $binDirectory) {
    $choco = Get-Command choco.exe -ErrorAction SilentlyContinue
    if ($choco) {
        Write-Host 'Installing FFmpeg with Chocolatey.'
        try {
            & $choco.Source install ffmpeg -y --no-progress --execution-timeout 1200
            $chocoExitCode = $LASTEXITCODE
        }
        catch {
            Write-Warning "Chocolatey FFmpeg install raised an error: $($_.Exception.Message)"
            $chocoExitCode = -1
        }

        $binDirectory = Find-FfmpegBinDirectory
        if (-not $binDirectory) {
            Write-Warning "Chocolatey did not produce usable ffmpeg.exe and ffprobe.exe (exit code: $chocoExitCode); falling back to Winget."
        }
    }
    else {
        Write-Warning 'Chocolatey is not available; trying Winget.'
    }
}

if (-not $binDirectory) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "FFmpeg is unavailable and neither Chocolatey nor Winget could provide it. Chocolatey exit code: $chocoExitCode."
    }

    try {
        & $winget.Source install --exact --id Gyan.FFmpeg --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity
        $wingetExitCode = $LASTEXITCODE
    }
    catch {
        Write-Warning "Winget FFmpeg install raised an error: $($_.Exception.Message)"
        $wingetExitCode = -1
    }

    $binDirectory = Find-FfmpegBinDirectory
    if (-not $binDirectory) {
        throw "FFmpeg installation did not produce usable ffmpeg.exe and ffprobe.exe. Chocolatey exit code: $chocoExitCode; Winget exit code: $wingetExitCode."
    }
}

$env:PATH = "$binDirectory;$env:PATH"

# This installer is invoked as a separate PowerShell process in GitHub Actions.
# Updating this process's PATH alone does not make FFmpeg available to later steps.
# Persist the verified directory through the runner's supported PATH environment file.
if ($env:GITHUB_PATH) {
    if ($binDirectory -match "[`r`n]") {
        throw 'Resolved FFmpeg directory contains a newline and cannot be safely added to GITHUB_PATH.'
    }
    Add-Content -LiteralPath $env:GITHUB_PATH -Value $binDirectory -Encoding utf8
    Write-Host 'Persisted the verified FFmpeg directory for subsequent GitHub Actions steps.'
}

$ffmpegPath = Join-Path $binDirectory 'ffmpeg.exe'
$ffprobePath = Join-Path $binDirectory 'ffprobe.exe'
if (-not (Test-Path $ffmpegPath -PathType Leaf) -or -not (Test-Path $ffprobePath -PathType Leaf)) {
    throw "Resolved FFmpeg directory does not contain both required executables: $binDirectory"
}

Write-Host "Using FFmpeg from: $binDirectory"
& $ffmpegPath -version
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg -version failed with exit code $LASTEXITCODE."
}
& $ffprobePath -version
if ($LASTEXITCODE -ne 0) {
    throw "ffprobe -version failed with exit code $LASTEXITCODE."
}
