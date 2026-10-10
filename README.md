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

## Quick start

From the repository root:

```bash
python -m venv .venv
python -m pip install -e '.[web,dev]'
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
```

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

## Windows one-click start

On Windows, double-click **[START-ALL.bat](START-ALL.bat)** in the repository root. It is the main local launcher and stays next to this README.

It will:
1. Check that Docker Desktop is installed and running.
2. Create a local development `06-CONFIG-AND-DEPLOYMENT/.env` with generated secrets when one does not exist.
3. Validate the Docker Compose configuration.
4. Build and start the complete Edit Factory dashboard stack.
5. Wait for `/api/health` to become healthy.
6. Open the dashboard login page in your browser and show the local dashboard token in the launcher window.
7. When `uv` is installed, run dependency, Python compilation, Ruff, mypy, and the full pytest suite.
8. Run a final dashboard health check and report failures with a non-zero exit code.

The stack stays running after the launcher finishes. Use `START-ALL.bat -SkipChecks` for a faster startup, or `docker compose ... down` to stop the stack.

The PowerShell implementation is **[START-ALL.ps1](START-ALL.ps1)**.

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
