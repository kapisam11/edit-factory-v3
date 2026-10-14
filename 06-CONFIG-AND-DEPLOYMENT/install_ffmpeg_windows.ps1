$ErrorActionPreference = 'Stop'

function Find-FfmpegBinDirectory {
    # A successful package-manager process is not sufficient: Chocolatey can
    # report success while its feed has failed. Accept only a directory that
    # actually contains both executables used by the media tests.
    $ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $ffprobeCommand = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($ffmpegCommand -and $ffprobeCommand) {
        $ffmpegPath = $ffmpegCommand.Source
        $ffprobePath = $ffprobeCommand.Source
        if ($ffmpegPath -and $ffprobePath) {
            $ffmpegDirectory = Split-Path -Parent $ffmpegPath
            $ffprobeDirectory = Split-Path -Parent $ffprobePath
            if ($ffmpegDirectory -eq $ffprobeDirectory) {
                return $ffmpegDirectory
            }
        }
    }

    $searchRoots = [System.Collections.Generic.List[string]]::new()
    if ($env:ChocolateyInstall) {
        $searchRoots.Add((Join-Path $env:ChocolateyInstall 'bin'))
        $chocolateyLib = Join-Path $env:ChocolateyInstall 'lib'
        if (Test-Path $chocolateyLib -PathType Container) {
            Get-ChildItem -Path $chocolateyLib -Directory -Filter '*ffmpeg*' -ErrorAction SilentlyContinue |
                ForEach-Object { $searchRoots.Add($_.FullName) }
        }
    }

    $wingetRoots = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'),
        (Join-Path $env:ProgramFiles 'WinGet\Packages')
    ) | Where-Object { $_ -and (Test-Path $_ -PathType Container) }

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
        if ((Test-Path $directFfmpeg -PathType Leaf) -and (Test-Path $directFfprobe -PathType Leaf)) {
            return $root
        }

        $candidates = Get-ChildItem -Path $root -Filter 'ffmpeg.exe' -File -Recurse -ErrorAction SilentlyContinue
        foreach ($candidate in $candidates) {
            $candidateFfprobe = Join-Path $candidate.DirectoryName 'ffprobe.exe'
            if (Test-Path $candidateFfprobe -PathType Leaf) {
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
