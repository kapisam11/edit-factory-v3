# 40-point implementation map

This document maps every item from the production roadmap to concrete code in the repository.

## 20 engineering upgrades

1. Word-level timestamps — `advanced_intelligence.generate_word_timestamps()` and `content_factory.build_word_level_captions()`.
2. Automatic line breaking — `content_factory.build_word_level_captions()`.
3. Caption emphasis — emphasis tokens in `build_word_level_captions()` and existing ASS choreography.
4. Audio-first timeline — `content_factory.audio_first_timeline()`.
5. Shot-selection scoring — `content_factory.score_shots()`.
6. Visual redundancy detection — `content_factory.detect_visual_redundancy()` and `duplicate_fingerprint()`.
7. Dead-time detection — `content_factory.detect_dead_time()`.
8. Silence/filler cleanup — `clean_voiceover_words()` and `clean_filler_words()`.
9. Dynamic pacing — `adaptive_pacing()`.
10. Better hook generation — `generate_hook_candidates()`.
11. Hook A/B generation — ranked multiple hooks from the same function.
12. Topic-specific emotion detection — `detect_emotion()`.
13. Adaptive clip count — `choose_clip_count()`.
14. Vertical/horizontal/square layouts — `LAYOUTS` / `get_layout()`.
15. Source-aware cropping — `plan_source_aware_crop()`.
16. Intermediate render validation — `validate_render()`.
17. Checkpoint/resume — `CheckpointStore`.
18. Artifact dependency graph — `artifact_hash()`, `dependency_fingerprint()`, `artifact_dependency_graph()`.
19. Provider-aware retries — `provider_retry()`.
20. Deterministic production — `deterministic_manifest()`.

## 20 product features

1. One-click Make Video — `aivf-content make` plus the existing factory CLI.
2. Content strategy engine — `generate_content_strategy()`.
3. Trend-aware research — `trend_research()`.
4. Competitor/content-gap research — `competitor_content_gap()`.
5. Shorts + long-form planning — `long_and_short_plan()`.
6. Automatic clip factory planning — `generate_clip_factory_plan()`.
7. Cross-platform packaging — existing `OUTPUT_PROFILES` plus `LAYOUTS`.
8. Thumbnail factory/ranking — `thumbnail_factory_plan()` layered over existing thumbnail variants.
9. Title laboratory — existing `rank_title_candidates()`.
10. Description generator — existing `build_description()`.
11. Tag/keyword pack — existing `generate_platform_tags()`.
12. Chapter generator — `generate_chapters()`.
13. Automatic upload package — `finalize_upload_package()`.
14. Direct YouTube uploader — `youtube_publisher.upload_video()`.
15. Playlist automation — `ensure_playlist()` and `add_video_to_playlist()`.
16. AI-disclosure manager — `disclosure_policy()` and `youtube_publisher.disclosure_setting()`.
17. Content fingerprint / duplicate detector — `duplicate_fingerprint()` and `detect_visual_redundancy()`.
18. Performance feedback loop — `fetch_video_statistics()`, `fetch_video_analytics()`, and `performance_feedback()`.
19. Channel-style learning — `learn_channel_style()`.
20. Autonomous content queue — `queue_jobs()` and `run_queue()`.

## Important operational boundary

YouTube OAuth, real-channel publishing and real analytics require the user's credentials and a real target channel. CI validates the code paths without attempting an external upload. Trend research is best-effort and remains offline-safe when the network is unavailable.
