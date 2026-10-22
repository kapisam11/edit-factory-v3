# Developer guide

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

## Engineering & operations

The production architecture, database indexes, optional Redis caching, security scans, E2E checks, container publishing/deployment, coverage policy, and scaling strategy are documented in **[00-INFO/11-ENGINEERING-AND-OPERATIONS.md](00-INFO/11-ENGINEERING-AND-OPERATIONS.md)**.

The dashboard now has a reusable SQLite storage boundary, short-lived metadata caching with optional Redis backing, completed-job video preview, and retry support for failed/interrupted jobs. V3 also persists source manifests, creative provenance, content manifests, content-addressed job identity, and machine-readable editorial decisions. CI enforces the repository's existing coverage policy plus deterministic/editorial regression tests.
