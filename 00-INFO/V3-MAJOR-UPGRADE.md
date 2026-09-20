# Edit Factory v3 — Major Upgrade

Version 3 is a product upgrade, not a repository rename. The central rule is now enforced in code: **decide what the viewer should feel before deciding what the edit should say.**

## Four flagship systems

### 1. Emotion Director

The v3 planner creates a `CoreIdea` before any clip plan. It determines why people care, the emotional angle, the target emotion, the stakes, the watch-to-end reason, and the final payoff. It then locks exactly one supported edit type for the whole piece. Explicit overrides are validated against the supported edit types.

### 2. Retention Architect

The planner generates ranked visual/text/emotional hooks, a purpose-driven clip timeline, short on-screen copy, beat/drop timing, motion/transition instructions, and a retention event map. The default retention interval is two seconds and is constrained to the original 1–3 second rule.

### 3. Human Editor Gate

Every blueprint runs deterministic editorial checks: emotion exists, the opening hook is short, each clip has a purpose, overlays are concise, clips are not dead/overlong, visual changes are frequent, and the structure contains a real payoff plus final impact. The output includes a QC score and warnings instead of silently claiming quality.

### 4. Content Packaging + Learning Signals

Every blueprint includes platform profiles, thumbnail direction, title options, hashtags, description copy, and heuristic retention/completion/rewatch/share scores. Those scores are planning signals, not claims about real platform performance. The existing repository learning/analytics components remain the source for real post-publish feedback.

## The 40 v3 upgrades

The v3 engine exposes exactly 40 tracked capabilities so the release can be verified programmatically. The registry is evidence-labeled: deterministic contract capabilities are `implemented`, mixed semantic/lexical checks are `hybrid`, and editorial/visual approximations are `heuristic`. A heuristic entry is not a claim of human editorial review, platform prediction, or general AI semantic understanding.

1. Emotion-first core idea
2. Single edit-type lock
3. Visual hook
4. Text hook
5. Emotional hook
6. Hook A/B ranking
7. Purpose-driven clip plan
8. Adaptive clip count
9. Exact duration allocation
10. 2–6 word overlays
11. Overlay de-duplication
12. Music energy plan
13. Beat-grid sync
14. Drop-aware payoff
15. Retention event map
16. 1–3 second visual change rule
17. Camera motion planning
18. Transition restraint
19. Human-editor reject pass
20. Dead-moment detection
21. Repetition detection
22. AI-slideshow guard
23. Payoff validation
24. Hook-payoff continuity
25. Platform-safe variants
26. 9:16 Shorts profile
27. 9:16 TikTok profile
28. 9:16 Reels profile
29. 1:1 social profile
30. 16:9 long-form profile
31. Thumbnail concept
32. Title laboratory
33. Description generator
34. Hashtag pack
35. Silent-viewing readability
36. Safe-area awareness
37. Retention heuristic score
38. Completion heuristic score
39. Rewatch heuristic score
40. Shareability heuristic score

These are additive to the earlier 40-point production roadmap already present in the repository (caption timing, audio-first timeline, shot scoring, checkpoint/resume, provider retries, deterministic manifests, content strategy, trend research, upload packaging, YouTube publishing, playlist automation, disclosure handling, channel learning, and autonomous queueing).

## Runtime flow

```text
create_v3_blueprint()
    -> analyze_core_idea()
    -> choose_edit_type()
    -> generate_hooks()
    -> build_clip_plan()
    -> analyze_music()
    -> build_retention_map()
    -> human-editor QC
    -> metadata/platform packaging

run_v3_pipeline()
    -> persist v3_blueprint.json
    -> convert blueprint to production research summary
    -> call existing run_production_pipeline()
    -> existing scene intelligence / render / QC / packaging path
```

The existing renderer remains the single media-execution path. v3 adds a stronger planning contract instead of forking FFmpeg/media logic.

## CLI

```bash
aivf-v3 "Eggchan" \
  --context "The loyal friend who stayed when everyone else left" \
  --seconds 30 \
  --bpm 120 \
  --platform youtube_shorts \
  --output output/eggchan/v3_blueprint.json
```

The command validates the blueprint by default. `--no-validate` exists for inspecting an intentionally incomplete draft.

## Compatibility

The previous `aivf` and `aivf-content` entry points remain. The existing `run_production_pipeline()` remains public. v3 is available through the new `create_v3_blueprint()` and `run_v3_pipeline()` APIs, so existing users are not forced into a renderer rewrite.

## External-service boundary

Real AI provider calls, social publishing, analytics, OAuth, and external media services still require their respective credentials and network access. CI tests deterministic planning and adapter contracts without uploading content or consuming paid provider calls.
