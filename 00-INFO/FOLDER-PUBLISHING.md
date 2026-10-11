# Existing-file publishing on Windows

This workflow is for already rendered MP4 files in a local folder. It does not edit videos. It uses a durable SQLite queue and processes at most one eligible destination job per run; AIVF_FOLDER_MAX_VIDEOS_PER_DAY defaults to 1. The current direct uploader is YouTube only. Instagram and TikTok destination jobs are recorded as BLOCKED_UNSUPPORTED rather than falsely reported as published. They remain blocked until real authenticated adapters are implemented and tested.

## 1. Install and authorize YouTube once

Install the project and its YouTube integration from PowerShell in the repository root:

    uv sync --extra web --extra dev --extra youtube

Create a Google OAuth client in the Google API Console and enable the YouTube Data API for that project. Save its client-secrets file outside Git. Set YOUTUBE_CLIENT_SECRETS to that path and YOUTUBE_AUTH_INTERACTIVE=1 for a one-time interactive authorization through the app's supported authorization flow. After the refreshable token is saved, reset YOUTUBE_AUTH_INTERACTIVE=0. The scheduled publisher never opens an OAuth browser.

Set these values in 06-CONFIG-AND-DEPLOYMENT/.env or the Task Scheduler process environment:

- AIVF_INBOX_DIR — folder to scan; defaults to the repository inbox folder.
- AIVF_STATE_DIR — durable state directory; defaults to the repository state folder.
- YOUTUBE_CLIENT_SECRETS — path to the OAuth client-secrets JSON.
- YOUTUBE_TOKEN_PATH — path to the already-authorized refresh token.
- AIVF_YOUTUBE_ACCOUNT_ID — stable label for this authorized destination account; default is default.
- AIVF_FOLDER_STABILITY_SECONDS — require unchanged size and modification time for this many seconds; default 60.
- AIVF_FOLDER_MAX_VIDEOS_PER_DAY — hard cap of successful uploads per UTC day; default 1.
- AIVF_FOLDER_ALLOW_PUBLIC — must be exactly 1 before a sidecar that requests public visibility can be eligible. Default is 0.

The queue database is stored at AIVF_STATE_DIR/folder-publishing.sqlite3. Back up the state directory and OAuth token securely. Do not put OAuth tokens in the inbox or commit them to Git.

## 2. Add a video and its metadata sidecar

Put a ready-to-publish MP4 in the inbox and create a UTF-8 JSON file with the same base name. For example, inbox/story.mp4 needs inbox/story.json:

    {
      "title": "A checked title",
      "description": "Caption for YouTube",
      "tags": ["story", "shorts"],
      "platforms": ["youtube_shorts"],
      "privacy_status": "private",
      "approved": true,
      "rights_cleared": true,
      "ai_generated": false,
      "realistic_alteration": false,
      "disclosure_reviewed": false,
      "platform_metadata": {
        "youtube": {
          "title": "A checked title",
          "description": "A platform-specific description",
          "tags": ["story", "shorts"],
          "privacy_status": "private"
        }
      }
    }

Set approved only after the final video and metadata are reviewed. Set rights_cleared only after you have permission/license rights for the footage and audio. For AI-generated or realistic altered/synthetic media, mark the relevant field truthfully and set disclosure_reviewed only after reviewing the platform disclosure. The uploader sets YouTube's synthetic-media declaration when realistic_alteration is true. This workflow does not determine whether content is legally licensed.

Accepted destination names in platforms are youtube, youtube_shorts, instagram_reels, and tiktok. This list is accepted so each intended destination can be tracked independently. At present only YouTube has a direct uploader; jobs for Instagram and TikTok are safely blocked as unsupported, and the tool does not claim otherwise. Optional platform_metadata overrides are limited to title, description, tags, and privacy_status.

The intake accepts regular, non-symlink MP4 files, checks maximum file size, and validates video stream, duration, dimensions, and frame rate with ffprobe. Incomplete files are ignored until stable. Invalid media or metadata is reported in scan output and is not uploaded.

## 3. Run the queue manually

    uv run --extra youtube aivf-folder-publish scan
    uv run --extra youtube aivf-folder-publish list
    uv run --extra youtube aivf-folder-publish run-once

To run a continuous local watcher instead of Task Scheduler:

    uv run --extra youtube aivf-folder-publish watch --interval 60

The watcher respects the daily cap. Review queue state with list. A crashed job stays recorded as UPLOADING; the next attempt uses the same stable idempotency key and delegates remote reconciliation to the existing YouTube uploader. An ambiguous upload or exhausted retries moves to NEEDS_REVIEW, not a blind re-upload.

If a queued job moves to NEEDS_REVIEW, inspect the reason and current sidecar. After resolving it, run the explicit approval command:

    uv run --extra youtube aivf-folder-publish approve --job-id JOB_ID

Approval revalidates media and metadata and refuses changed source bytes, uncleared rights, unreviewed disclosures, or public uploads without explicit environment opt-in. A changed sidecar never silently causes a second upload of the same video.

## 4. Register a daily Windows task

From PowerShell in the repository root, register a daily task for Windows local time (example: noon):

    Set-ExecutionPolicy -Scope Process Bypass
    .\REGISTER-DAILY-PUBLISHING.ps1 -DailyTime "12:00"

The task invokes RUN-DAILY-PUBLISH.ps1, loads local settings from the deployment .env without printing values, starts the CLI, and logs failures in Task Scheduler history. The task uses StartWhenAvailable, wake-from-sleep, restart, and ignore-overlap settings. Windows power policies and hardware can still prevent wake from sleep. A machine that is completely shut down or lacks internet cannot publish at the requested time; verify Task Scheduler history, wake timers, and next-run behavior on the actual PC.

Unregister the task with:

    Unregister-ScheduledTask -TaskName "Edit Factory v3 Daily Folder Publisher" -Confirm:$false

## Scope and release status

This is a code-level local queue implementation, not evidence of a real-account acceptance test. A real YouTube private upload, remote-ID confirmation, interrupted upload, and Windows reboot/sleep/network recovery must still be exercised on the target machine. Direct Instagram/TikTok upload remains unsupported until authenticated adapters, processing/status reconciliation, and destination-account tests are implemented. Do not enable unattended public publishing until those gates are passed.
