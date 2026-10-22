# Architecture

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

## Engineering principles

Edit Factory v3 preserves working architecture instead of rewriting it for the version bump. New behavior is additive and testable. Media subprocesses remain argument-list based, runtime outputs are validated, optional AI/provider features have fallbacks, and the v3 blueprint is deterministic and offline-safe.
