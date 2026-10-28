# Windows setup and production checklist

## Windows one-click start

On Windows, double-click **[START-ALL.bat](../START-ALL.bat)** in the repository root. It is the main local launcher in the repository root, next to the root README.

It will:
1. Check that Docker Desktop is installed and running.
2. Create a local development `06-CONFIG-AND-DEPLOYMENT/.env` with generated secrets when one does not exist.
3. Validate the Docker Compose configuration.
4. Build and start the local development dashboard stack using `docker-compose.dev.yml` (the launcher does not require a pre-published immutable production image).
5. Stop a previously running web container (without deleting the database or volumes) so an old healthy instance cannot satisfy the new launch's health poll.
6. Open the animated studio loading screen in a fullscreen Edge/Chrome kiosk window, then build/start the current dashboard. The splash waits for the current web service to respond, remains visible for at least four seconds, and then shows the Edit Factory welcome page with a sign-in button. Sign-in is not required on the loading or welcome screen.
7. Once the dashboard health check passes, a separate **Access Details** window opens with the dashboard token and admin-console unlock code. The values start masked and have Show/Hide and Copy controls; the token and unlock code are never printed into the command window. The launcher command window can then close normally while the Access Details window stays open.
8. To reopen the credentials window later without restarting Edit Factory, double-click **[SHOW-LOCAL-CREDENTIALS.bat](../SHOW-LOCAL-CREDENTIALS.bat)**. It reads the local ignored `.env` file and does not display the Flask session secret or other API keys. A real startup failure still leaves its error window visible. Normal startup does not run the full developer test suite, so unrelated test/tool failures do not make the app appear to have failed to launch.
8. When you deliberately want to run lint, type checks, and tests, use `START-ALL.bat -RunChecks`. FFmpeg/FFprobe-dependent integration tests require those tools on the Windows host; missing host tools should be reported as environment skips.
9. Select **Clip Existing Footage** to retain the source-based editing workflow, or **Create a Video from an Idea** to choose a supplied topic or ask Edit Factory for a surprise topic. Idea-to-Video creates a script/storyboard, searches only license-filtered Wikimedia Commons image/video assets (plus optional eligible Commons background audio), renders vertical variants, and creates platform-specific upload packages and captions. Review `rights_manifest.json` and each package's `human_review` metadata; this workflow deliberately keeps `publish_ready=false`. Automated license metadata is not a guarantee of reuse rights. TikTok/Instagram live-account acceptance and any real Windows startup/render acceptance remain manual checks.

The local Compose profile publishes the dashboard only on `127.0.0.1` by default and limits admission to one concurrent job by default. On the intended 32 GB Windows PC, the development container currently defaults to a 4-CPU limit and 8 GB memory; keep those conservative settings and concurrency of one until a representative 60-second render has been measured. Set `AIVF_DASHBOARD_PORT` in `06-CONFIG-AND-DEPLOYMENT/.env` to choose another host port; the launcher follows the same setting. The stack stays running after the launcher finishes. Use `START-ALL.bat -RunChecks` when you deliberately want the full developer checks. Use Docker Compose to stop the stack. These defaults protect a single-PC setup; they do not configure unattended multi-platform uploads.

The PowerShell implementation is **[START-ALL.ps1](../START-ALL.ps1)**.

### Production startup is a separate, deliberate path

`START-ALL.bat` is the development/supervised-local launcher. It builds `docker-compose.dev.yml`, generates local development secrets, and is **not** the production launch path.

For the immutable production image, use **[START-PRODUCTION.bat](../START-PRODUCTION.bat)** or [START-PRODUCTION.ps1](../START-PRODUCTION.ps1). Configure `AIVF_IMAGE` as a full `image@sha256:<64-hex-digest>` and set real `FLASK_SECRET_KEY` and `AIVF_DASHBOARD_TOKEN` values in `06-CONFIG-AND-DEPLOYMENT/.env`. First startup requires the explicit `START-PRODUCTION.ps1 -Migrate` command; ordinary restarts use `START-PRODUCTION.ps1` and verify the schema without modifying it. The production launcher does not build source, prints no secrets, and refuses an unbacked image version change. See [the deployment guide](../06-CONFIG-AND-DEPLOYMENT/README.md) for backup and release-upgrade limitations.

### Windows small-group production requirements (practical local scope)

This guidance is for supervised use on one trusted Windows 10/11 PC by a few trusted users—not an internet-facing multi-tenant service.

- Use Windows 10/11 with Docker Desktop (WSL2 backend) running locally.
- Use Python 3.12 for local checks (`uv`, `pytest`, `mypy`, and `ruff`) and install FFmpeg/FFprobe where the Windows tools and any non-container tasks can resolve them.
- Keep the dashboard bound to `127.0.0.1`. Do not expose it to a LAN, reverse proxy, or public internet without a separate authentication, session/CSRF, TLS, and file-access review.
- Keep render concurrency conservative (start at `AIVF_MAX_CONCURRENT_JOBS=1`); increase it only after representative renders show acceptable peak RAM, VRAM, disk, thermal, and recovery behavior.
- Keep publishing disabled during installation and testing. Enable a platform only after reviewing its rights/approval checklist and completing a controlled private or unlisted account acceptance test.
- Keep generated tokens, client secrets, and the deployment `.env` file out of Git, ordinary support bundles, and backups. The development launcher also avoids printing secret values during Compose validation or normal startup. After restoring a backup, reauthorize provider accounts because credential files are intentionally excluded.

### File-size and disk-space safeguards

The effective upload limit is the smaller of the dashboard's configured maximum upload size and `AIVF_MAX_INPUT_BYTES` (default 2 GiB). Media admission also checks duration, dimensions, frame rate, and stream metadata before expensive processing. Final output size is bounded by `AIVF_MAX_OUTPUT_BYTES` (default 5 GiB).

For the existing-folder publisher, configure:

- `AIVF_FOLDER_MAX_FILE_BYTES` (default 8 GiB): maximum incoming file size.
- `AIVF_FOLDER_MAX_STAGED_BYTES` (default 20 GiB): staging quota for immutable upload snapshots.
- `AIVF_FOLDER_MIN_FREE_DISK_BYTES` (defaults to `AIVF_MIN_FREE_DISK_BYTES`, normally 20 GiB): disk space that must remain free in addition to the space needed for a new staged copy.
- `AIVF_MIN_FREE_DISK_BYTES` (default 20 GiB): shared media admission/free-space floor.

Set these values deliberately for the actual drive hosting uploads, staging, state, and output. Limits are admission safeguards, not a substitute for monitoring the drive or keeping a separate backup. If a disk-space check fails, free space or adjust the configured policy before retrying; do not delete active job workspaces manually.

### Final Windows production checklist

Complete and record these steps on the target PC before accepting paid work or enabling unattended publishing:

1. **Installation and exposure:** start with a clean checkout, generated secrets, a working Docker Desktop/WSL2 installation, and localhost-only dashboard access. Verify that login and sensitive routes reject unauthenticated requests.
2. **Real media smoke test:** render representative source footage with audio and captions; confirm the resulting video plays, has expected dimensions/duration/audio, and passes the project's media/QC checks. Review the actual image and sound yourself.
3. **Resource and cleanup test:** confirm upload/file-size limits and disk-space safeguards are active. Test a rejected oversized file and a low-disk condition; confirm temporary uploads/staging files are cleaned and the last known-good output is preserved.
4. **Crash/restart test:** run a test render, terminate the worker while it is active, restart the service, and verify the job is marked interrupted/recoverable rather than incorrectly successful. Confirm no orphan FFmpeg/FFprobe process remains and a retry does not overwrite another attempt's output.
5. **Backup/restore verification drill:** run `.\03-SIDE-CODE\scripts\windows\backup\BACKUP-EDIT-FACTORY.ps1` to a different physical drive, verify the archive, restore into an isolated destination, and check the restored database, knowledge, uploads, and output. Keep the backup drive encrypted (for example, BitLocker) and test restore, not just backup creation.
6. **API failure and retry behavior:** exercise mocked network timeouts, HTTP 429/rate limits, HTTP 5xx responses, and malformed media/probe output before allowing any real post. Verify the job displays a useful error code/message, credentials and signed URLs are not logged, and an uncertain publication is reconciled rather than blindly posted twice.
7. **Rights approval before publish:** confirm every source has an explicit rights basis/evidence/declaration, then complete a valid human review with all required scoring dimensions. Missing or malformed rights evidence, missing approval, or an invalid media contract must keep `publish_ready` false.
8. **Controlled account acceptance:** only after the previous checks pass, test one video at private/unlisted visibility on each intended platform/account. Confirm the remote ID/status and visibility before increasing volume or enabling automatic publication.
9. **Daily operations:** keep an eye on queue status, failed/interrupted jobs, resource usage, output quality, API status, and disk headroom. Verify the last backup is readable and rerun the restore drill after significant storage/schema changes.

### Pre-paid-work checklist

Before accepting paid editing work, run this short checklist on your own machine:

1. Complete a **real media smoke test** with representative footage and audio.
2. Run a **backup/restore verification drill** and confirm recovered state/output.
3. Simulate **API failure and retry behavior** (timeouts, rate limits, temporary outages) and confirm jobs fail with actionable errors.
4. Confirm **rights approval before publish** and human review gates are completed for every deliverable.
5. Keep **disk space headroom** above your configured minimum before long renders or uploads.
