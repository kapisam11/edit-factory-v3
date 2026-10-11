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

The autonomous `aivf-autopublish daemon` is also YouTube-specific and generates content from its persisted topic queue; it does not watch a folder of existing videos. A fully unattended existing-file-to-YouTube/Instagram/TikTok workflow is therefore **not yet implemented or production-verified**.

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
