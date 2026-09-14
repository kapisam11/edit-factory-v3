# Structure notes

The repository is organized for two audiences at once: people and Python/runtime tooling.

The GitHub front page intentionally contains only `README.md` plus hidden Git/editor configuration and the numbered navigation folders:

```text
00-INFO/
01-MAIN-CODE/
02-WEB-FILES/
03-SIDE-CODE/
04-TESTS/
05-EXTENSIONS/
06-CONFIG-AND-DEPLOYMENT/
99-ARCHIVE/
```

The numbered folders are the human-facing organization layer. Runtime code is physically grouped inside those folders rather than left scattered across the repository root.

- `01-MAIN-CODE/` contains the core `ai_video_factory/` package, CLI/runtime launchers, and Python package metadata.
- `02-WEB-FILES/` contains the canonical Flask/WSGI application in `app/` plus dashboard templates and static browser assets.
- `03-SIDE-CODE/` contains helper tools and maintenance scripts.
- `04-TESTS/` contains automated tests.
- `05-EXTENSIONS/` contains optional resources such as prompts and learning/scoring data.
- `06-CONFIG-AND-DEPLOYMENT/` contains Docker, Compose, Gunicorn, dependency reference, and configuration files.
- `99-ARCHIVE/` contains retired material.

The application package/import names have been preserved where the runtime depends on them. CI explicitly sets the required `PYTHONPATH`, and Gunicorn changes into `01-MAIN-CODE` before loading the WSGI application. This keeps the physical organization clean without changing the public Python interfaces unnecessarily.

There are intentionally no tracked sample-output files in the repository's normal source tree.
