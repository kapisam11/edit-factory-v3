# Gaming quality and publishing gates

Edit Factory V3 now has an explicit **Gaming clips & commentary** profile for videos made from gameplay and a mixture of original, AI-generated, and licensed assets. It extends the existing V3 plan/render/package workflow; it does not create a second rendering engine.

## Configure a gaming job

In the dashboard select **V3 Production → Gaming clips & commentary**, enter the game name, describe the actual event in **Topic**, and choose an editorial angle:

- **Clutch / epic play:** decisive action, stakes, visible decision, evidenced outcome.
- **Strategy / educational commentary:** specific problem, gameplay evidence, decision and alternative, observed result, takeaway.
- **Funny moment / story:** setup, original action/reaction, timing, creator perspective, payoff.
- **Challenge / attempt:** creator-supplied rules, starting situation, attempts, turning point and real result.
- **Gaming story:** verified opening, minimal context, conflict, evidenced turning point and actual ending.

The game name and topic are creator-supplied context; they are not evidence that the described event happened. Scene analysis and `clip_source_evidence.json` must support critical beats. Missing or weak critical evidence causes the strict V3 planning stage to fail rather than invent a clutch, win, kill, reaction, weapon, or game mechanic. Review `gaming_profile.json` and `clip_source_evidence.json` before accepting the cut.

## Source provenance and commercial use

For gaming jobs, explicitly declare:

- Asset origin: original recording, AI-generated, licensed, or third party.
- Rights basis and any permission/license evidence URL.
- Whether commercial use is permitted under the applicable publisher/media terms.
- Permitted uses and restrictions, plus whether attribution is required and the exact attribution text.
- Who made the rights declaration and when.

These declarations are stored in `source_manifest.json`, `provenance.json`, and the gaming profile report. `commercial_use=review_required` or `not_allowed`, unknown origin, missing rights evidence where required, missing declaration identity/time, or missing required attribution blocks the upload package. Marking commercial use as allowed is a documented declaration, not a legal conclusion or automatic verification of a publisher's terms. Do not claim permission you have not checked.

## Objective output checks

The upload package records `media_contract.caption_quality` and `media_contract.audio_quality`.

Caption QC checks SRT cue syntax, timestamps that are monotonic and within the actual video duration, empty captions, overlapping cues, and conservative text line/character limits. Invalid timing or likely text overflow blocks `publish_ready` and includes a cue number and timestamp where available. ASS-to-SRT conversion emits standard three-digit millisecond timestamps.

Audio signal QC runs FFmpeg loudness analysis in normal non-test operation. It checks that integrated loudness and true peak are measurable and within broad delivery bounds, flags minimal true-peak headroom and wide loudness range, and records long silence candidates with timestamps. Severe silence candidates can block gaming packages.

These are measurable checks, not proof of polished sound or readable visuals. Integrated loudness does not isolate narration from game audio or prove voice/music balance. SRT-based checks cannot prove burned-in subtitle placement or platform safe margins. A person must still listen to the finished video and review captions at phone size.

For deterministic tests, audio signal analysis can be mocked or disabled with `AIVF_ENABLE_AUDIO_SIGNAL_QC=0`; this must not be interpreted as production signal verification. Do not leave the override disabled in normal production if you expect audio signal QC to run.

## Publishing decision

Keep the decisions separate:

1. **Technical:** the expected media profile, audio stream, decoding, caption cues, and signal checks pass.
2. **Editorial:** source evidence supports the claims, the angle is coherent, commentary adds creator perspective, and the payoff is real.
3. **Rights:** origin, rights basis, commercial-use declaration, evidence and attribution obligations are resolved.
4. **Human review:** the final cut and platform metadata are approved.
5. **Publish:** only a package with `publish_ready=true` may proceed.

A passing MP4 or model score does not establish eligibility for the YouTube Partner Program, TikTok Creator Rewards, or any other monetization program. Platform eligibility, each game's publisher terms, and actual account permissions remain separate checks. Automated public publishing should stay disabled until your own real-media workflow and platform accounts have been verified.
