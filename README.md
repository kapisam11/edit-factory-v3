# Edit Factory v3

**Emotion-first AI-assisted video production for TikTok, YouTube Shorts, Instagram Reels, and upload-ready social packages.**

Edit Factory v3 does not begin with a script. It first decides what the viewer should feel, why they should care, what payoff makes them stay, and which single editing style best serves that emotion. It then plans hooks, clips, text, music, retention events, rendering, quality control, metadata, and publishing assets.

## Architecture

The V3 blueprint contract and engineering principles are documented in [docs/architecture.md](docs/architecture.md).
## v3 highlights

The v3 planner exposes a required capability registry on top of the existing production stack. It includes emotion-first story analysis, exact edit-type locking, evidence-aware hook candidate evaluation, adaptive clip planning, concise overlays, beat/drop synchronization, semantic retention anchors, automated editorial checks, platform-safe layouts, clearly labeled performance heuristics, thumbnail concepts, title options, descriptions, and hashtag packs.

The production pipeline also includes a video-only **Thinking AI** creative-director stage. It drafts a topic-specific story/edit plan, critiques it against editorial criteria, revises weak drafts once, and passes the validated script and phase-specific edit directions into the existing planner. A separate, small local preference model retrains from observed retention/engagement and explicit human ratings, then ranks previously tested edit profiles for future videos. The OpenAI/Groq language model stays fixed; Edit Factory learns channel-specific editing preferences without trying to recreate ChatGPT. See [00-INFO/VIDEO-THINKING-AI.md](00-INFO/VIDEO-THINKING-AI.md).

The repository also retains the previously implemented production features: scene intelligence, captions/subtitles, music intelligence and mixing, thumbnail tooling, upload packaging, channel/style learning, checkpoint/retry hardening, YouTube publishing support, dashboard/job handling, FFmpeg validation, Docker deployment, and CI.

**Publishing scope:** The repository has two separate existing-video workflows. `aivf-folder-publisher` is the opt-in, private-by-default YouTube-only folder watcher, with stable-file detection, FFprobe validation, a durable SQLite queue, immutable snapshots, and retry idempotency. `aivf-publish` is the opt-in per-platform/account queue and includes YouTube, Instagram Reels, and TikTok Direct Post adapters, durable per-destination state, bounded retry/reconciliation, and publication history. These adapters are implemented and covered by mocked tests; that does not prove your particular accounts are eligible or that your apps have approved permissions. Complete a controlled upload and verify the final remote post on every enabled account before unattended use. The topic-generation autonomous daemon remains separate. See [00-INFO/UPLOAD-PACKAGE.md](00-INFO/UPLOAD-PACKAGE.md).

## Guides

- [Architecture and V3 contract](docs/architecture.md)
- [Windows setup and production checklist](docs/windows.md)
- [Developer guide and repository map](docs/development.md)
- [Security policy](SECURITY.md)
- [Deployment and publishing operations](06-CONFIG-AND-DEPLOYMENT/README.md)
- [Engineering and operations notes](00-INFO/11-ENGINEERING-AND-OPERATIONS.md)

## Quick start

**Runtime requirement:** Python 3.11 or newer. Python 3.10 support was dropped with the security update to Pillow 13.0.0, which does not support Python 3.10. The lockfile pins the patched Pillow and certifi versions.

From the repository root:

```bash
python -m venv .venv
uv sync --frozen --python 3.12 --extra web --extra dev
```

Generate a v3 creative blueprint before rendering:

```bash
aivf-v3 "The friend everyone could trust" \
  --context "He stayed loyal when everyone else left" \
  --platform youtube_shorts \
  --seconds 30 \
  --output output/v3_blueprint.json
```

The generated JSON contains the emotional angle, selected edit type, ranked hook candidates with heuristic evaluation evidence, the clip plan, overlay text, music timing, semantic retention map, platform policy metadata, quality report, titles, hashtags, description, and thumbnail concept. Pre-render technical validity remains null until a rendered artifact is validated.

Existing CLIs remain available:

```bash
aivf --help
aivf-content --help
aivf-v3 --help
aivf-batch-clips --help
```

### Batch Clip Mode

Use Batch Clip Mode when you have one long source video and want several independent Shorts to review. It analyzes the source once, ranks likely moments using deterministic scene-importance, motion, audio, face, and available text evidence. When transcript text is available, it also gives explainable heuristic credit to hook-like cues near the beginning, payoff-like cues near the end, and supporting spoken context. These lexical cues are not semantic understanding or predicted views; when transcripts are absent, the mode falls back to audiovisual signals. Each window is extracted to its own workspace and run through the normal V3 planner, renderer, and quality checks.

On Windows, run this from the repository root in PowerShell (choose a new output directory for each run):

```powershell
aivf-batch-clips "D:\Videos\source.mp4" "The best moments from this match" --output-dir "output\match-batch-01" --count 5 --seconds 25 --platform youtube_shorts
```

The defaults are five candidates of about 25 seconds; request 1–10 candidates and 8–180 seconds per candidate. The source must be at least 8 seconds long. The output directory must be new or empty, and existing results are never overwritten. Open `output\match-batch-01\batch_manifest.json` to see source timestamps, explainable heuristic scores, each candidate's status, and relative paths to successful `final.v3.mp4` files. Each clip has its own package, so a render failure is recorded for that candidate and does not stop other candidates. The command returns exit code 2 for a partial batch and 1 if the batch produces no successful clip.

Scores are ranking heuristics, not predicted views. This mode does not bypass source-rights review, V3 quality checks, human publishing approval, or platform safeguards; it does not publish anything automatically. The existing single-clip production workflow is unchanged.

Run the production preflight diagnostics CLI:

```bash
aivf-diagnose
# inspect one media file more deeply
aivf-diagnose --media input.mp4 --deep --directory output
```

V3 renders now emit technical media-health, provenance, metadata-guardrail, and environment-diagnostics artifacts alongside the existing render/QC reports. Rights metadata is treated as a review signal; the pipeline does not infer that online media is licensed for reuse.

## Empirical editorial evaluation

V3 now separates pipeline validity from editorial-quality evidence. The repository
contains an `evaluation/` corpus layout, structured human annotations, automated
prediction storage, Spearman/MAE reporting, deterministic decision-graph replay,
and a creative-regression gate.

Human scores are never fabricated by the pipeline. Correlation is reported only
when at least three matched human annotations exist.

Each editorial decision records its operation, reason, confidence, evidence,
policy version, planner version, configuration hash, and source hash when available.
Low-confidence decisions can explicitly resolve to `DO_NOTHING`.

## Learning from your channel

Edit Factory retrains a small local preference model from real video performance and ratings. Configure `AIVF_LEARNING_HISTORY_PATH` and optionally `AIVF_LEARNING_MODEL_PATH`; the trained weights are stored in `video_preference_model.json`. Rate an output package with `aivf-learn rate --history state/learning_history.json --package-dir output/my-video --rating 5 --note "Strong hook and pacing"`. See [00-INFO/VIDEO-THINKING-AI.md](00-INFO/VIDEO-THINKING-AI.md) for how training data and recommendations work.

### Cross-platform analytics learning

The local preference model can import live lifetime views/engagement metrics from an authorized TikTok or Instagram professional account with `aivf-learn import-platform-live`; it matches post IDs only to known Edit Factory packages from learning history or the publishing ledger and skips unmatched or ambiguous videos. TikTok needs `video.list`; Instagram requires access to media insights. This is read-only analytics, not automatic publishing or a guarantee of retention data. See [Video Thinking AI](00-INFO/VIDEO-THINKING-AI.md).

## Windows one-click start

The full Windows startup guide, practical production requirements, disk safeguards, and final checklist are in [docs/windows.md](docs/windows.md).
## Developer guide

The Python API, repository map, and engineering/operations guidance are in [docs/development.md](docs/development.md).
## Dashboard

Run the production dashboard with:

```bash
gunicorn -c 06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py app.wsgi:app
```

Production dashboard access requires `AIVF_DASHBOARD_TOKEN` and a strong `FLASK_SECRET_KEY`.

### Editorial review, clip scoring, and saved preset versions

The dashboard adds an Editorial Review link to completed packages. The review workbench previews the original MP4 beside an editable SubRip caption track, allows corrections to subtitle wording/timing and upload title/description, validates cue timestamps and text limits, exports reviewed SRT files, and lets you restore earlier edits. Reviewed sidecars and revision snapshots are kept under each package's reviewed directory.

**Important:** saving caption changes never overwrites a previously validated MP4. For packages with the original source still available, an intact source hash, saved V3 plan/timeline, and original voiceover, click **Render corrected MP4** to render a separate output with the reviewed SRT. It refuses if the source has changed or required render artifacts are missing. The regular final MP4 remains untouched. Packages without those V3 artifacts can still export the reviewed SRT, but must be rendered separately in an editor or another workflow.

V3 source-scene evidence now includes an explainable editorial jury: hook strength, context independence, narrative fit, pacing, information density, visual energy, and semantic support. Top scene candidates and their reasons are retained with clip evidence. These deterministic heuristics are not predicted views or promises of virality; source-evidence matching remains the gate, so a high general score cannot make an unrelated scene count as evidence.

Saved production presets keep the last 20 revisions. Use Preset History next to Save Preset to inspect and restore a version. Restoring creates a new revision instead of removing later changes; the restore action requires the dashboard admin role. The review workbench also has a structured cue editor and metadata export, plus a 1–5 creator rating and note. Ratings are written to the existing `AIVF_LEARNING_HISTORY_PATH` (default `state/learning_history.json`) only when the package already has a matching learning-history row; it will return a clear message rather than fabricate a training example. Existing YouTube/TikTok/Instagram analytics import and the local preference model continue to provide measured performance feedback.

### Face-aware reframing

The compositor can use OpenCV face detection to sample a clip, smooth face-center movement, and guide the vertical crop. It is best-effort, not a guarantee that every person or object stays in frame. Missing OpenCV, cascade data, readable frames, or detected faces falls back to the existing crop rather than failing the render. Reviewed re-renders use the same saved V3 edit plan and voiceover in a separate temporary workspace, then validate video/audio streams before promoting a new file into `reviewed/renders/`.

Install the optional vision dependency with:

    uv sync --frozen --python 3.12 --extra web --extra dev --extra vision

Face-aware reframing runs in auto mode when the vision capability is available. To disable it, set AIVF_SMART_REFRAME=0 in the environment used to launch Edit Factory. The crop track uses bounded interpolation anchors so FFmpeg filter expressions stay manageable. Existing silence-aware segment detection, beat analysis, subtitle checks, publishing recovery, and channel-specific learning are reused rather than duplicated.


## Engineering and operations

For developer workflow and operational guidance, see [docs/development.md](docs/development.md).
## License and distribution

Edit Factory v3 is **source-available**, not MIT/open-source licensed. The complete terms are in **[LICENSE](LICENSE)**.

The official repository is:

**https://github.com/kapisam11/edit-factory-v3**

The license does not grant redistribution, mirroring, re-hosting, resale, package publishing, or charging for copies. The software is provided without warranty, and users are responsible for their own configuration, generated content, third-party services, and compliance obligations.


## Windows production requirements

The detailed guide contains the **Windows small-group production requirements** and **Final Windows production checklist**, including disk limits, crash recovery, backup/restore, rights review, and publishing gates: [Windows setup and production checklist](docs/windows.md).

`START-ALL.bat` is the development/supervised-local launcher. Production startup is separate; see [deployment and publishing operations](06-CONFIG-AND-DEPLOYMENT/README.md).


## Windows small-group production requirements

This repository targets supervised local use on a trusted Windows PC. The full [Windows setup and production checklist](docs/windows.md) includes setup and resource limits.

### Pre-paid-work checklist

- Complete a **real media smoke test** using representative footage, audio, and captions.
- Run a **backup/restore verification drill** and verify recovered state and output.
- Test **API failure and retry behavior**, including timeouts, rate limits, and temporary outages.
- Require **rights approval before publish** and human review for each deliverable.
- Keep **disk space headroom** above the configured minimum before long renders or uploads.

### Final Windows production checklist

Before paid work or unattended publishing, complete the [full checklist on Windows](docs/windows.md), including a real render/QC review, low-disk and rejected-upload checks, worker crash/restart recovery, a backup/restore verification drill, API failure and retry behavior, and controlled account acceptance tests.

- Folder publisher limits: `AIVF_FOLDER_MAX_FILE_BYTES`, `AIVF_FOLDER_MAX_STAGED_BYTES`, and `AIVF_FOLDER_MIN_FREE_DISK_BYTES`.
- **Crash/restart test:** verify interrupted jobs recover safely and never report false success.
- **Missing or malformed rights evidence** must block publishing pending human review.
- Complete a **private or unlisted account acceptance test** for each enabled platform before enabling public or unattended publishing.

`START-ALL.bat` **is the development/supervised-local launcher**. Production uses `START-PRODUCTION.bat` / `START-PRODUCTION.ps1`; first startup requires `START-PRODUCTION.ps1 -Migrate`. The production launcher **does not build source** and requires an immutable image digest and explicitly configured secrets. See [deployment operations](06-CONFIG-AND-DEPLOYMENT/README.md).
