# Windows 11 target-PC render benchmark

Use this runbook on the actual Windows 11 production PC. GitHub Actions runners cannot measure your machine's FFmpeg behavior, GPU availability, thermal throttling, real media access, or peak memory use.

## Before testing

1. Pull the current `main` branch and double-click `START-ALL.bat` without using any skip-check option. Let the locked install, compilation, Ruff, mypy, full pytest suite, and final health check finish; record any failure instead of treating the dashboard starting as proof that the checks passed.
2. Keep the current defaults for the first measurement: `AIVF_MAX_CONCURRENT_JOBS=1`, development Compose CPU limit 4, memory limit 8 GiB. Do not raise CPU limits or concurrency before collecting a representative measurement.
3. Choose an input video that you have permission to use commercially and that is representative of your normal workflow. Record its file name, size, duration, and SHA-256.

## Measure one real 60-second render

1. In the dashboard, run the actual V3 edit/render workflow you intend to use, with a 60-second target and the intended platform profile. Use the real footage, captions, audio and output settings you plan to publish. Run only one job.
2. Start this sampling loop in a second PowerShell window from the repository root before starting the render. It records per-second container CPU and memory readings until you press Ctrl+C:

```powershell
$Log = Join-Path (Get-Location) 'state\benchmark-container.csv'
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
while ($true) {
    $Stamp = Get-Date -Format 'o'
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}' |
        ForEach-Object { "$Stamp,$_" } | Tee-Object -FilePath $Log -Append
    Start-Sleep -Seconds 1
}
```

3. Record wall-clock start/finish, whether the PC remained responsive, the container CPU/memory peak from the CSV, the host CPU/GPU/RAM figures from Task Manager, render errors/warnings, and final MP4 size. Do not infer GPU use merely because the PC has a GPU.
4. Probe the finished file (replace the path with the actual result):

```powershell
ffprobe -v error -show_entries format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,sample_rate,channels -of json .\path\to\final.v3.mp4
```

5. Play the entire final MP4 from beginning to end. Explicitly inspect the first two seconds, intended footage, aspect ratio/crop, captions/subtitles (safe area, contrast, readability and timing), audio presence when expected, loudness and sync, black/frozen frames, and the ending/payoff. An FFprobe result or green automated QC cannot certify caption readability or creative coherence; record human acceptance with `human_acceptance.template.json` and verify it with `verify_human_acceptance.py`.

## How to decide whether to tune resource limits

- Keep concurrency at one until multiple representative jobs have been measured and failure/recovery tests pass.
- Leave the CPU limit at four if the renderer is not consistently CPU-bound or the host has little spare headroom.
- Only trial a higher `AIVF_CONTAINER_CPUS` if the render is demonstrably CPU-bound, the PC has spare CPU capacity, and the memory peak has comfortable headroom. Change one setting at a time and repeat the same benchmark.
- Stop and investigate if memory approaches the container limit, Windows becomes unresponsive, FFmpeg is killed, free disk space drops near the configured reserve, or the output fails visual/audio review.

## Practical release acceptance

- [ ] `START-ALL.bat` completed without skipped checks and all checks passed.
- [ ] One representative 60-second render completed and the benchmark results were recorded.
- [ ] The whole MP4 passed visual/audio/rights review.
- [ ] An intentionally invalid input failed clearly without damaging a successful result.
- [ ] Restarting the application after a successful render preserved the finished output and job state.
- [ ] A backup on a different drive passed archive verification and the restore drill.

Do not mark these boxes from source inspection or CI results. They require evidence collected on the target PC.