# 06 — Production Deployment

This guide is for the person responsible for putting Edit Factory on a real server or workstation.

## The important idea

There are two different kinds of verification:

```text
CI / Docker
    = the code builds and automated checks pass

Real production host
    = the deployed application actually works on the machine that will run it
```

You need both.

## Production settings

Set a strong secret and dashboard token through the deployment environment:

```text
FLASK_SECRET_KEY=<strong-random-secret>
AIVF_DASHBOARD_TOKEN=<strong-random-token>
```

For HTTPS, enable secure cookies:

```text
AIVF_COOKIE_SECURE=1
```

Do not use `AIVF_ALLOW_INSECURE_LOCAL=1` for an internet-facing deployment.

## Docker deployment

The repository supplies a production Dockerfile and Compose configuration.

Typical command:

```bash
docker compose up --build -d
```

Then inspect the service:

```bash
docker compose ps
docker compose logs --tail=200
```

## Persistent data

Production must persist the configured state/output storage. Job state is stored in SQLite and generated outputs/uploads are runtime data.

A container replacement should not erase the job database or generated outputs.

## Production acceptance test

Before calling the deployment verified, run this sequence on the **real target machine**:

### 1. Start the application

Confirm the dashboard is reachable and authentication works.

### 2. Run one real job

Use a real test video and allow the pipeline to create a real output.

Confirm the resulting media plays correctly.

### 3. Test cancellation

Start a long enough job that it is still running, cancel it, and confirm:

- the job becomes `cancelled`
- the worker stops
- FFmpeg stops
- no orphan media process continues running

### 4. Test restart recovery

Start a job, restart the application while work is active, and confirm the job becomes visible as `interrupted` rather than silently disappearing.

### 5. Test persistence

Restart/replace the application and confirm the SQLite state, logs, and expected outputs are still present.

### 6. Test shutdown

Stop the service cleanly and confirm workers do not remain behind.

## Release rule

Do not describe the deployment as fully verified until the automated checks **and** these target-host tests pass.

For the concise operational reference, see `OPERATIONS.md` and the release checklist.
