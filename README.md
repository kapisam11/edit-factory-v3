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
 retention change every 1–3 sec
                ↓
      human-editor QC pass
                ↓
 FFmpeg production + validation
                ↓
 titles + thumbnail + metadata
                ↓
     upload-ready package
```

The goal is a result that feels intentionally edited by a human: no AI slideshow pacing, no filler clips, no repeated overlays, no random effects, and no dead sections.

## v3 highlights

The v3 planner adds an explicit 40-capability creative/retention contract on top of the existing production stack. It includes emotion-first story analysis, exact edit-type locking, hook A/B ranking, adaptive clip planning, concise overlays, beat/drop synchronization, a 1–3 second retention map, human-editor rejection checks, platform-safe layouts, heuristic retention/completion/rewatch/share scores, thumbnail concepts, title options, descriptions, and hashtag packs.

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

The generated JSON contains the emotional angle, selected edit type, ranked hooks, complete clip plan, overlay text, music timing, retention map, platform profiles, quality report, titles, hashtags, description, and thumbnail concept.

Existing CLIs remain available:

```bash
aivf --help
aivf-content --help
aivf-v3 --help
```

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

`run_v3_pipeline()` writes `v3_blueprint.json` first and then hands the creative brief to the existing production renderer so v3 planning does not duplicate the proven media execution path.

## Repository map

```text
Edit Factory v3
├── README.md
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

Start with **[00-INFO/00-START-HERE.md](00-INFO/00-START-HERE.md)**. The concrete 40-point implementation mapping is documented in **[00-INFO/40-POINT-IMPLEMENTATION.md](00-INFO/40-POINT-IMPLEMENTATION.md)**.

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

The dashboard now has a reusable SQLite storage boundary, short-lived metadata caching with optional Redis backing, completed-job video preview, and retry support for failed/interrupted jobs. CI also enforces an 80% minimum coverage gate for the selected high-value V3/runtime modules.

## License and distribution

Edit Factory v3 is **source-available**, not MIT/open-source licensed. The complete terms are in **[LICENSE](LICENSE)**.

The official repository is:

**https://github.com/kapisam11/edit-factory-v3**

The license does not grant redistribution, mirroring, re-hosting, resale, package publishing, or charging for copies. The software is provided without warranty, and users are responsible for their own configuration, generated content, third-party services, and compliance obligations.
