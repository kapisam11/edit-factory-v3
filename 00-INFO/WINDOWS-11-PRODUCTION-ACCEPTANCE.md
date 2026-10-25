# Windows 11 acceptance checklist (single-PC production)

Use this checklist on the actual Windows 11 PC with your normal footage, captions, music, output profile and local account. It is deliberately four practical checks rather than a month-long endurance plan. Passing CI alone is not production acceptance.

## 1. Start once with checks enabled

From the repository root, run `START-ALL.bat` without `-SkipChecks`. `uv` must be installed: the launcher now stops before starting the dashboard if `uv` is missing, rather than silently skipping code checks. Use `-SkipChecks` only as an explicit development opt-out, not for production acceptance. Wait for the checks to finish; inspect the terminal output if any step fails. Confirm the dashboard becomes healthy at the local URL printed by the launcher.

The local development Compose configuration should keep the dashboard bound to `127.0.0.1`, one concurrent job, and its current 4-CPU/8-GB container limits. Do not raise the CPU/memory limits or worker concurrency just because the PC has 32 GB RAM; measure a representative job first.

## 2. Render one representative 60-second video

Use the actual workflow and a real source video that resembles what you intend to publish. Include your usual subtitles/captions and audio settings, and target the intended platform profile. Do not use `-SkipChecks`, turn off semantic QC, or use synthetic test fixtures for this acceptance render.

Record the elapsed render time and observe CPU and memory usage in Task Manager or Docker Desktop while it runs. Keep the existing limits unless the measurement shows the renderer is CPU-bound and the PC has spare capacity. Keep concurrency at one during this first baseline.

The job must complete without errors, with `final.v3.mp4`, `v3_readiness.json`, `v3_render_qc.json`, `release_evidence.json`, and `artifact_manifest.json` consistent with the actual output. The technical report checks duration, dimensions, configured frame rate, output size, decode/readability sampling, retention contracts, and audio if `AIVF_REQUIRE_OUTPUT_AUDIO=1`. It does **not** certify story quality, caption readability, synchronization, copyright or commercial licensing.

## 3. Inspect the final MP4 manually

Play the entire generated MP4 from beginning to end in a normal player. Inspect:

- The first two seconds and whether the correct footage/hook actually appears.
- Correct aspect ratio/resolution and smooth playback; note black frames, frozen frames, or unexpected cuts.
- Caption visibility, readability, placement in the safe area, spelling, and timing/synchronization.
- Audio presence where expected, speech/music balance, clipping, and ending/payoff.
- The final frame and whether the video ends cleanly rather than cutting off.

Check source ownership/licences and AI/altered-content disclosure separately. A rights-review flag or a technically valid MP4 is not evidence that you have commercial permission. Keep human review enabled; the upload package requires the configured review dimensions (hook, pacing, visual relevance, caption readability, audio, rights and metadata) before `publish_ready` can be true.

## 4. Test one failed job, one restart, and a backup

First keep actual network publishing disabled. Copy an invalid/corrupt media file into a **test input location** and confirm the application rejects it with a clear validation failure instead of promoting it as a successful render. Do not queue a public upload for this test.

After a good render, restart the dashboard stack during an idle period:

```powershell
docker compose --env-file .\06-CONFIG-AND-DEPLOYMENT\.env -f .\06-CONFIG-AND-DEPLOYMENT\docker-compose.dev.yml restart web
```

Confirm the completed job remains visible, the final video is still playable, and the failed job is not shown as successful. A failed rerun of an existing package should retain its previous `final.v3.mp4`; the new regression test exercises that contract.

Create a verified backup on a different trusted NTFS drive:

```powershell
.\03-SIDE-CODE\scripts\windows\backup\BACKUP-EDIT-FACTORY.ps1 -Destination "E:\EditFactoryBackups"
```

Use an actual destination drive different from the repository drive; change `E:\EditFactoryBackups` to match your PC. The script restricts ACLs, copies the completed archive to that drive, and verifies the copied archive. The local archive is **not encrypted**; do not store it on a shared drive. Provider token/secret files are deliberately excluded, so accounts must be reauthorized after a disaster restore.

Perform a recovery test into a new, empty test directory—not over the live state:

```powershell
uv sync --frozen --python 3.12 --extra web --extra dev
uv run aivf-backup restore --backup "E:\EditFactoryBackups\edit-factory-v3-<timestamp>.tar.gz" --destination "E:\EditFactoryRestoreTest"
```

Replace the example filename with the one printed by the backup script. Inspect the restored main database, auxiliary publication database, uploads, finished output and knowledge files. The restore verifier validates hashes and SQLite integrity before extracting.

## Acceptance record

Record the test date, source video (or source hash), platform profile, output path/hash, CI commit SHA, elapsed render time, peak CPU/memory observed, whether all captions/audio were acceptable, the invalid-input result, restart result, backup filename/SHA-256, and restore result. If a critical check fails, fix the cause and repeat the affected checks before enabling unattended publishing.

## What this checklist does not prove

A mocked API test does not prove that your YouTube/Instagram/TikTok accounts have eligible account types, app approval, correct OAuth scopes, or a working live publishing path. Before turning on `AIVF_MULTI_PUBLISH=1` or passing `--publish`, upload a controlled private/self-only test to every enabled account, verify the actual remote post ID/URL and completed state, and test an ambiguous timeout/reconciliation. Never use a CI test to publish a public video.
