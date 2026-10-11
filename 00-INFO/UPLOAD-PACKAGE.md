# Upload-ready content workflow

Edit Factory now treats the final output as a content package, not just an MP4.

A normal run creates an `upload/` folder containing:

- `title.txt` — the selected title
- `description.txt` — copy/paste description
- `tags.txt` — ranked topic-relevant tags
- `metadata.json` — title candidates, scores, platform profile, media probe, hashes, and disclosure review state
- `README-UPLOAD.md` — the short upload checklist

The package also writes `upload_package.json` at the job root so other tools can consume the same manifest.

## CLI

```bash
python 02-WEB-FILES/app/cli.py "Minecraft betrayal on SMP" --raw-video gameplay.mp4
```

Choose another platform metadata profile with:

```bash
python 02-WEB-FILES/app/cli.py "Minecraft betrayal on SMP" --platform youtube_shorts
```

Supported profiles are `youtube_shorts`, `youtube`, `tiktok`, and `instagram_reels`.

These are **output-format and metadata profiles**, not a promise that each platform can be uploaded to automatically. The current direct authenticated upload implementation is YouTube-only. The repository does not currently provide working direct Instagram Reels or TikTok upload adapters with authorization, remote-processing polling, and duplicate recovery.

The autonomous `aivf-autopublish daemon` remains YouTube-specific and generates content from its persisted topic queue; it does not watch a folder of existing videos. Existing files can use the separate `aivf-folder-publisher` command below. That path is YouTube-only: Instagram Reels and TikTok direct publishing remain unimplemented. The folder pipeline has automated regression coverage but still requires Windows Task Scheduler/reboot/sleep acceptance and real-account upload/recovery tests before it is considered production-verified.

## Existing video folder -> YouTube

The folder publisher is separate from the topic-generation daemon. It watches a local folder recursively for MP4, MOV, M4V, MKV, and WebM files. A file must remain unchanged for the configured settle interval, pass FFprobe checks, and be copied to a content-addressed staging snapshot before it can enter the upload-ready state. Upload attempts always use that snapshot, so a retry does not silently switch to bytes that were edited after the first attempt.

Uploads are disabled by default. Start with a scan-only pass:

```bash
uv run --extra youtube aivf-folder-publisher --watch-dir ./incoming scan
uv run --extra youtube aivf-folder-publisher --watch-dir ./incoming status
```

Review each file's rights and disclosure status, then approve it. The approval command is a human attestation that you have permission to publish the media; it does not determine copyright or platform policy for you. The AI/alteration status must be explicit:

```bash
uv run --extra youtube aivf-folder-publisher --watch-dir ./incoming approve clip.mp4 --not-ai-generated --no-realistic-alteration
```

For AI-generated or realistically altered media, declare the relevant flag instead and use `--acknowledge-disclosure` only after reviewing the required disclosure. You can edit the adjacent `clip.mp4.publish.json` sidecar directly to set the title, description, tags, privacy, thumbnail, or caption. The sidecar approval flags must remain true, rights must be reviewed, and any required disclosure review must be acknowledged before upload.

Only after scan/status and sidecar review should the actual publisher be enabled:

```bash
uv run --extra youtube aivf-folder-publisher --watch-dir ./incoming --publish daemon
```

Set `YOUTUBE_CLIENT_SECRETS` and `YOUTUBE_TOKEN_PATH` to the already-authorized YouTube OAuth files. The process never opens a browser unexpectedly. Private is the default upload visibility; public uploads require the additional explicit `--allow-public` opt-in and a sidecar requesting public visibility. Configure `AIVF_FOLDER_MAX_VIDEOS_PER_DAY` (default 2), `AIVF_FOLDER_SETTLE_SECONDS` (default 30), `AIVF_FOLDER_MAX_FILE_BYTES`, and `AIVF_FOLDER_MAX_STAGED_BYTES` to suit the host. Keep the SQLite database and staging directory on persistent storage, and do not run multiple copies against different YouTube accounts with the same database.

The queue records remote IDs and refuses to retry ambiguous outcomes without re-entering the existing uploader's idempotency reconciliation. Recovery is deliberately fail-closed: a corrupted or missing staged snapshot blocks automated retry rather than uploading different bytes under the same identity. This does not substitute for verifying the remote video on the actual account.

## YouTube publishing

Publishing is deliberately explicit. The factory never uploads just because a video finished rendering.

Install the optional integration:

```bash
python -m pip install -e '.[youtube]'
```

Then run:

```bash
python 02-WEB-FILES/app/cli.py "Minecraft betrayal on SMP" \
  --raw-video gameplay.mp4 \
  --youtube-upload \
  --youtube-client-secrets client_secret.json \
  --youtube-privacy private
```

The first run opens the standard OAuth consent flow and stores the refreshable token at the configured token path. Keep that token outside the repository.

The default privacy setting is `private` so you can inspect the result before publishing. You can explicitly choose `unlisted` or `public` after verifying the package.

## AI / altered-content review

The manifest intentionally records `review_required` instead of making an automatic legal/platform decision. Review the generated or altered media and use the platform's current disclosure controls when needed.

## Learning loop

The selected title, title alternatives, scoring reasons, tags, hashes, and platform profile are preserved in the manifest. This gives the existing learning system structured inputs it can use later when performance feedback is added.
