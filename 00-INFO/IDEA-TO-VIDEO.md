# Idea-to-Video workflow

Edit Factory has two distinct production modes:

- **Clip Existing Footage** uses a video you supply. Keep using it for the existing scene-selection, clip-ranking, editing, and repurposing features.
- **Create a Video from an Idea** accepts a topic—or chooses an evergreen topic when you leave the topic blank—and uses the Director pipeline to research, plan and script a vertical video without an uploaded source.

## What the source-free workflow does

1. Runs the existing topic research, planning, script, and thumbnail stages.
2. Attempts narration with the configured text-to-speech provider.
3. Searches Wikimedia Commons for eligible CC BY licenses without NC/ND/SA restrictions or public-domain/CC0 assets. It prefers source video where eligible; otherwise it uses licensed images in a motion slideshow. Unverified YouTube/Reddit content is not downloaded into this generated video.
4. Builds a 9:16 video with on-screen captions, creates a matching SRT caption file, and uses Commons background music only when the downloaded track passes the same conservative licensing filter and media validation. If narration, music, or eligible visuals are unavailable, the job records the limitation; it must not invent that a missing track was included.
5. Validates the generated MP4 and creates YouTube Shorts, TikTok and Instagram Reels render variants, plus platform-specific copy/paste upload packages, titles, descriptions, tags, thumbnails and rights metadata.
6. Leaves every generated upload package with `publish_ready=false`, `human_review.status=review_required`, and an explicit approval-required policy.

A blank topic chooses from a small built-in set of evergreen ideas. This is intentionally deterministic in topic scope and does not promise live trend discovery. The normal research stages may use configured providers, but a generated script or a metadata-quality score is not proof that a factual claim is correct. Review factual claims and the finished video yourself.

## Rights are still a review gate

The generator only *selects* assets whose Commons metadata passes its conservative filter. Metadata can be incomplete, mistaken, or change later. A source URL or attribution is not by itself proof of commercial permission. Inspect `rights_manifest.json`, the linked source and license pages, attribution requirements, any restrictions, and the final use of the asset. If the selected source is not appropriate for your intended commercial use, replace it or do not publish.

Music is optional: if no eligible Commons audio is found or download/probe fails, the video is produced without background music and a warning is recorded. The workflow deliberately does not use an external TTS/music provider's soundtrack as a substitute for license evidence.

## Publishing status

This workflow prepares upload packages; it does not automatically publish the generated Idea-to-Video job. Packages intentionally remain not publish-ready. YouTube has an explicit authenticated upload implementation elsewhere in the repository, but this does not prove that your account has passed a live acceptance test. TikTok and Instagram adapters still need real-account acceptance testing. Do not enable automatic/public publishing on the basis of mock tests or an existing package alone.

## Package artifacts

Look inside the job's output package for:

- `final_idea_video.mp4`: primary vertical render.
- `renders/youtube_shorts.mp4`, `renders/tiktok.mp4`, `renders/instagram_reels.mp4`: platform-sized outputs.
- `script.txt`, `storyboard.json`, `captions.srt`, and the thumbnail artifacts.
- `idea_upload_packages.json` and `upload_package.json`: package index and per-platform metadata.
- `upload/<platform>/title.txt`, `description.txt`, `tags.txt`, `metadata.json`, and captions when available.
- `rights_manifest.json`: source URLs, creator, license/evidence links, declared rights basis and the required human review status.

The first real end-to-end acceptance run should use a specific topic, inspect the output visually and audibly, and confirm every output package stays `publish_ready=false` until a real review/approval process exists.
