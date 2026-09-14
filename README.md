# Edit Factory v2

**AI-assisted video production and auto-editing for short-form content.**

Edit Factory takes a topic and optional raw footage, then can research, plan, script, edit, render, quality-check, and package a short video.

## New here? Start here

Read **[00-INFO/00-START-HERE.md](00-INFO/00-START-HERE.md)** first.

The repository is organized so the GitHub front page stays clean and project material is grouped by purpose:

```text
Edit Factory v2
├── README.md                         <- project overview and quick start
├── LICENSE                           <- source-available license and restrictions
├── pyproject.toml                    <- canonical Python packaging configuration
├── uv.lock                           <- reproducible dependency lockfile
├── CONTRIBUTING.md                   <- contribution/development rules
├── SECURITY.md                       <- security reporting guidance
├── .github/                          <- CI, repository automation, CODEOWNERS
├── .vscode/                          <- editor configuration
├── 00-INFO/                          <- guides, documentation, project map
├── 01-MAIN-CODE/                     <- core Python application + CLI runtime
├── 02-WEB-FILES/                     <- dashboard application + HTML/CSS
├── 03-SIDE-CODE/                     <- helper tools + maintenance scripts
├── 04-TESTS/                         <- automated tests
├── 05-EXTENSIONS/                    <- prompts, learning, scoring resources
├── 06-CONFIG-AND-DEPLOYMENT/        <- Docker, Gunicorn, Compose, configuration
└── 99-ARCHIVE/                       <- retired material
```

The root intentionally keeps only project-essential metadata/configuration and GitHub-recognized project files. Documentation, application code, tools, tests, extensions, deployment files, and archived material remain in their dedicated folders.

## What does it do?

```text
Topic + optional raw video
          ↓
   research / planning
          ↓
      script + hooks
          ↓
    editing decisions
          ↓
       FFmpeg work
          ↓
     quality control
          ↓
    upload-ready package
```

## Quick start

From the repository root:

```bash
python -m venv .venv
python -m pip install -e '.[web,dev]'
python 01-MAIN-CODE/cli.py --help
```

Run a simple job:

```bash
python 01-MAIN-CODE/cli.py "Minecraft betrayal on SMP"
```

The installed CLI is also available as:

```bash
aivf --help
```

Run the production dashboard:

```bash
gunicorn -c 06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py app.wsgi:app
```

Production dashboard access requires `AIVF_DASHBOARD_TOKEN` and a strong `FLASK_SECRET_KEY`.

## License and distribution

Edit Factory v2 is **source-available**, not MIT/open-source licensed. The complete terms are in **[LICENSE](LICENSE)**.

The project is provided free of charge from the official GitHub repository:

**https://github.com/kapisam11/edit-factory-v2**

The license does not grant redistribution, mirroring, re-hosting, resale, package publishing, or charging for copies. People who want the project should obtain it directly from the official GitHub repository unless `kapisam11` gives separate written permission.

The software is provided without warranty, and liability is limited to the maximum extent permitted by applicable law. Users are responsible for their own use, configuration, generated content, third-party services, and compliance with applicable laws and terms.

## Where things live

- **Main application:** `01-MAIN-CODE/`
- **Dashboard and browser files:** `02-WEB-FILES/`
- **Tools and maintenance:** `03-SIDE-CODE/`
- **Tests:** `04-TESTS/`
- **Extensions/resources:** `05-EXTENSIONS/`
- **Docker/config/deployment:** `06-CONFIG-AND-DEPLOYMENT/`
- **Old material:** `99-ARCHIVE/`

For a file-by-file explanation, read **[00-INFO/05-PROJECT-MAP.md](00-INFO/05-PROJECT-MAP.md)**.
