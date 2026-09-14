# 03 — Using the CLI

The CLI lets you run the factory from a terminal instead of a browser.

## The normal command

After installation:

```bash
aivf --help
```

From a repository checkout, the same main launcher is available at:

```bash
python 01-MAIN-CODE/cli.py --help
```

## A simple mental model

```text
command
  + topic
  + optional raw video
  + optional feature switches
        ↓
    factory pipeline
        ↓
    output package
```

## Typical workflow

Start by choosing a topic:

```bash
python 01-MAIN-CODE/cli.py "Minecraft betrayal on SMP"
```

For an editing job that uses raw footage, provide the video and enable the auto-edit/director path supported by the current CLI help:

```bash
python 01-MAIN-CODE/cli.py "Minecraft betrayal on SMP" --input-video recording.mp4 --auto-edit
```

Always run `--help` on the version you have installed before copying older commands from the internet or old notes. Historical documentation in this repository is not the source of truth for current CLI flags.

## What the CLI can control

Depending on the installed feature set, the CLI can control things such as:

- workflow selection
- raw input video
- target duration
- optional learning/feedback features
- subtitle style
- NLE export
- optional AI providers

The exact available switches are defined by the current CLI implementation and `aivf --help`.

## Where the result goes

Generated files belong in the configured runtime/output area. They are not source code and should not be committed to Git.

## CLI vs dashboard

Use the **CLI** when you want scripts, automation, batch jobs, or development work.

Use the **dashboard** when you want a browser-based job queue and live progress/log viewing.

Both paths ultimately use the same application/pipeline code.
