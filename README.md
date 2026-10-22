# Edit Factory v3

**Emotion-first AI-assisted video production for TikTok, YouTube Shorts, Instagram Reels, and upload-ready social packages.**

Edit Factory v3 does not begin with a script. It first decides what the viewer should feel, why they should care, what payoff makes them stay, and which single editing style best serves that emotion. It then plans hooks, clips, text, music, retention events, rendering, quality control, metadata, and publishing assets.

## The v3 creative contract

```text
Topic + context + optional raw video
                ↓
       core emotional idea
                ↓
        one edit type only
                ↓
 visual + text + emotional hook
                ↓
 purpose-driven clip timeline
                ↓
 text overlays + music beat grid
                ↓
 semantic retention + attention-gap constraint
                ↓
 explainable editorial decisions + human feedback
                ↓
 FFmpeg production + validation
                ↓
 titles + thumbnail + metadata
                ↓
     upload-ready package
```

The goal is a result that feels intentionally edited by a human. The creative layer is AI-assisted planning plus deterministic editorial rules and automated QC; it is not presented as learned human taste or a guarantee of audience performance.

## v3 highlights

The v3 planner exposes a required capability registry on top of the existing production stack. It includes emotion-first story analysis, exact edit-type locking, evidence-aware hook candidate evaluation, adaptive clip planning, concise overlays, beat/drop synchronization, semantic retention anchors, automated editorial checks, platform-safe layouts, clearly labeled performance heuristics, thumbnail concepts, title options, descriptions, and hashtag packs.

The production pipeline also includes a video-only **Thinking AI** creative-director stage. It drafts a topic-specific story/edit plan, critiques it against editorial criteria, revises weak drafts once, and passes the validated script and phase-specific edit directions into the existing planner. A separate, small local preference model retrains from observed retention/engagement and explicit human ratings, then ranks previously tested edit profiles for future videos. The OpenAI/Groq language model stays fixed; Edit Factory learns channel-specific editing preferences without trying to recreate ChatGPT. See [00-INFO/VIDEO-THINKING-AI.md](00-INFO/VIDEO-THINKING-AI.md).

The repository also retains the previously implemented production features: scene intelligence, captions/subtitles, music intelligence and mixing, thumbnail tooling, upload packaging, channel/style learning, checkpoint/retry hardening, YouTube publishing support, dashboard/job handling, FFmpeg validation, Docker deployment, and CI.

**Publishing scope:** The repository has two separate existing-video workflows. `aivf-folder-publisher` is the opt-in, private-by-default YouTube-only folder watcher, with stable-file detection, FFprobe validation, a durable SQLite queue, immutable snapshots, and retry idempotency. `aivf-publish` is the opt-in per-platform/account queue and includes YouTube, Instagram Reels, and TikTok Direct Post adapters, durable per-destination state, bounded retry/reconciliation, and publication history. These adapters are implemented and covered by mocked tests; that does not prove your particular accounts are eligible or that your apps have approved permissions. Complete a controlled upload and verify the final remote post on every enabled account before unattended use. The topic-generation autonomous daemon remains separate. See [00-INFO/UPLOAD-PACKAGE.md](00-INFO/UPLOAD-PACKAGE.md).

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

Use Batch Clip Mode when you have one long source video and want several independent Shorts to review. It analyzes the source once, ranks likely moments using deterministic scene-importance, motion, audio, face, and available text evidence, then chooses non-overlapping source windows. Each window is extracted to its own workspace and run through the normal V3 planner, renderer, and quality checks.

On Windows, run this from the repository root in PowerShell (choose a new output directory for each run):

```powershell
aivf-batch-clips "D:\\Videos\\source.mp4" "The best moments from this match" --output-dir "output\\match-batch-01" --count 5 --seconds 25 --platform youtube_shorts
```

The defaults are five candidates of about 25 seconds; request 1–10 candidates and 8–180 seconds per candidate. The source must be at least 8 seconds long. The output directory must be new or empty, and existing results are never overwritten. Open `output\\match-batch-01\\batch_manifest.json` to see source timestamps, explainable heuristic scores, each candidate's status, and relative paths to successful `final.v3.mp4` files. Each clip has its own package, so a render failure is recorded for that candidate and does not stop other candidates. The command returns exit code 2 for a partial batch and 1 if the batch produces no successful clip.

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

On Windows, double-click **[START-ALL.bat](START-ALL.bat)** in the repository root. It is the main local launcher and stays next to this README.

It will:
1. Check that Docker Desktop is installed and running.
2. Create a local development `06-CONFIG-AND-DEPLOYMENT/.env` with generated secrets when one does not exist.
3. Validate the Docker Compose configuration.
4. Build and start the local development dashboard stack using `docker-compose.dev.yml` (the launcher does not require a pre-published immutable production image).
5. Wait for `/api/health` to become healthy.
6. Open the dashboard login page in your browser and show the local dashboard token in the launcher window.
7. When `uv` is installed, run dependency, Python compilation, Ruff, mypy, and the full pytest suite.
8. Run a final dashboard health check and report failures with a non-zero exit code.

The local Compose profile publishes the dashboard only on `127.0.0.1` by default and limits admission to one concurrent job by default. On the intended 32 GB Windows PC, the development container currently defaults to a 4-CPU limit and 8 GB memory; keep those conservative settings and concurrency of one until a representative 60-second render has been measured. Set `AIVF_DASHBOARD_PORT` in `06-CONFIG-AND-DEPLOYMENT/.env` to choose another host port; the launcher follows the same setting. The stack stays running after the launcher finishes. Use `START-ALL.bat -SkipChecks` only for later faster starts, not the initial verification, or use Docker Compose to stop the stack. These defaults protect a single-PC setup; they do not configure unattended multi-platform uploads.

The PowerShell implementation is **[START-ALL.ps1](START-ALL.ps1)**.

### Production startup is a separate, deliberate path

`START-ALL.bat` is the development/supervised-local launcher. It builds `docker-compose.dev.yml`, generates local development secrets, and is **not** the production launch path.

For the immutable production image, use **[START-PRODUCTION.bat](START-PRODUCTION.bat)** or [START-PRODUCTION.ps1](START-PRODUCTION.ps1). Configure `AIVF_IMAGE` as a full `image@sha256:<64-hex-digest>` and set real `FLASK_SECRET_KEY` and `AIVF_DASHBOARD_TOKEN` values in `06-CONFIG-AND-DEPLOYMENT/.env`. First startup requires the explicit `START-PRODUCTION.ps1 -Migrate` command; ordinary restarts use `START-PRODUCTION.ps1` and verify the schema without modifying it. The production launcher does not build source, prints no secrets, and refuses an unbacked image version change. See [the deployment guide](06-CONFIG-AND-DEPLOYMENT/README.md) for backup and release-upgrade limitations.

### Windows small-group production requirements (practical local scope)

This guidance is for supervised use on one trusted Windows 10/11 PC by a few trusted users—not an internet-facing multi-tenant service.

- Use Windows 10/11 with Docker Desktop (WSL2 backend) running locally.
- Use Python 3.12 for local checks (`uv`, `pytest`, `mypy`, and `ruff`) and install FFmpeg/FFprobe where the Windows tools and any non-container tasks can resolve them.
- Keep the dashboard bound to `127.0.0.1`. Do not expose it to a LAN, reverse proxy, or public internet without a separate authentication, session/CSRF, TLS, and file-access review.
- Keep render concurrency conservative (start at `AIVF_MAX_CONCURRENT_JOBS=1`); increase it only after representative renders show acceptable peak RAM, VRAM, disk, thermal, and recovery behavior.
- Keep publishing disabled during installation and testing. Enable a platform only after reviewing its rights/approval checklist and completing a controlled private or unlisted account acceptance test.
- Keep generated tokens, client secrets, and the deployment `.env` file out of Git, ordinary support bundles, and backups. After restoring a backup, reauthorize provider accounts because credential files are intentionally excluded.

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
5. **Backup/restore verification drill:** run `BACKUP-EDIT-FACTORY.ps1` to a different physical drive, verify the archive, restore into an isolated destination, and check the restored database, knowledge, uploads, and output. Keep the backup drive encrypted (for example, BitLocker) and test restore, not just backup creation.
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

## Python API

```python
from ai_video_factory import V3Config, create_v3_blueprint, run_v3_pipeline

blueprint = create_v3_blueprint(
    "Eggchan",
    context="The loyal friend everyone could rely on",
    config=V3Config(target_seconds=30, platform="youtube_shorts"),
)

result = run_v3_pipeline(
    "input.mp4",
    "Eggchan",
    "output/eggchan",
    context="The loyal friend everyone could rely on",
    target_seconds=30,
)
```

`run_v3_pipeline()` atomically writes `v3_blueprint.json`, reads it back through the strict V3 contract, validates the deserialized blueprint, and only then hands a serialized creative brief to the existing production renderer. This keeps V3 planning separate from the proven media execution path.

## Repository map

```text
Edit Factory v3
├── README.md
├── START-ALL.bat                     <- one-click Windows launcher
├── START-ALL.ps1                     <- Windows launcher implementation
├── LICENSE
├── pyproject.toml
├── uv.lock
├── CONTRIBUTING.md
├── SECURITY.md
├── .github/                          <- CI and repository automation
├── 00-INFO/                          <- guides, architecture, release docs
├── 01-MAIN-CODE/                     <- core package + v3 planner/pipeline
├── 02-WEB-FILES/                     <- dashboard application + HTML/CSS
├── 03-SIDE-CODE/                     <- helper tools + maintenance scripts
├── 04-TESTS/                         <- automated tests
├── 05-EXTENSIONS/                    <- prompts, learning, scoring resources
├── 06-CONFIG-AND-DEPLOYMENT/         <- Docker, Gunicorn, Compose, configuration
└── 99-ARCHIVE/                       <- retired material
```

Start with **[00-INFO/00-START-HERE.md](00-INFO/00-START-HERE.md)**. The V3 intermediate-representation contract is documented in **[00-INFO/V3-BLUEPRINT-CONTRACT.md](00-INFO/V3-BLUEPRINT-CONTRACT.md)**, and the concrete 40-point implementation mapping is documented in **[00-INFO/40-POINT-IMPLEMENTATION.md](00-INFO/40-POINT-IMPLEMENTATION.md)**.

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


## Engineering principles

Edit Factory v3 preserves working architecture instead of rewriting it for the version bump. New behavior is additive and testable. Media subprocesses remain argument-list based, runtime outputs are validated, optional AI/provider features have fallbacks, and the v3 blueprint is deterministic and offline-safe.

## Engineering & operations

The production architecture, database indexes, optional Redis caching, security scans, E2E checks, container publishing/deployment, coverage policy, and scaling strategy are documented in **[00-INFO/11-ENGINEERING-AND-OPERATIONS.md](00-INFO/11-ENGINEERING-AND-OPERATIONS.md)**.

The dashboard now has a reusable SQLite storage boundary, short-lived metadata caching with optional Redis backing, completed-job video preview, and retry support for failed/interrupted jobs. V3 also persists source manifests, creative provenance, content manifests, content-addressed job identity, and machine-readable editorial decisions. CI enforces the repository's existing coverage policy plus deterministic/editorial regression tests.

## License and distribution

Edit Factory v3 is **source-available**, not MIT/open-source licensed. The complete terms are in **[LICENSE](LICENSE)**.

The official repository is:

**https://github.com/kapisam11/edit-factory-v3**

The license does not grant redistribution, mirroring, re-hosting, resale, package publishing, or charging for copies. The software is provided without warranty, and users are responsible for their own configuration, generated content, third-party services, and compliance obligations.
