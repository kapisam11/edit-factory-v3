# Video Thinking AI

Edit Factory's Video Thinking AI is a specialist creative-director stage, not a general-purpose chatbot. It plans and improves video stories, scripts, footage selection/edit direction, pacing, audio direction, thumbnails, and titles.

## How a video gets made

1. **Understand the brief.** The agent receives the topic, target duration, and available research/context. Research is treated as evidence, not instructions.
2. **Create a story plan.** It proposes a specific angle, human stakes, a short hook, a reason to keep watching, a payoff, narration lines, phase-specific editing directions, music direction, thumbnail concept, titles, and description.
3. **Critique the draft.** A separate model call scores specificity, natural language, narrative logic, factual restraint, visual editability, repetition, and duration fit. It returns a score, brief issues and revision notes—not private chain-of-thought.
4. **Revise once when needed.** Weak or cliché-heavy drafts get one bounded rewrite. This limits latency and model costs.
5. **Use the existing production pipeline.** The validated plan feeds the story/script planner and footage-aware timeline. The model cannot run arbitrary commands or bypass rendering, rights, factuality, or publishing guards.
6. **Gate uncertain output.** Invalid output falls back to the deterministic planner. A low critique score, failed style checks, or failed critique/revision requires human review before autonomous publishing can approve the package.
7. **Learn from outcomes.** New YouTube retention/engagement results and explicit 1–5 ratings become training examples for a small local editing-preference model. This is lightweight retraining of video-editing preferences; the OpenAI/Groq language model itself is not retrained.

## Enable it

The stage is enabled by default, but model-backed planning requires a configured provider:

- OPENAI_API_KEY, with the model configured by AIVF_OPENAI_MODEL
- GROQ_API_KEY, with the model configured by AIVF_GROQ_MODEL

Set AIVF_VIDEO_THINKING_ENABLED=0 to disable the stage. Without a provider key, it records not_configured and the deterministic planner remains available without a network call.

The stage writes video_thinking.json in the output package. It contains the validated creative plan, critique score/issues, whether revision was attempted, and whether human review is required. It does not store credentials or private chain-of-thought.

## Personal learning and retraining

The local learner is deliberately small and needs no GPU. It retrains a weighted ridge-regression model from editing settings plus actual outcomes. Features include cuts per minute, average shot duration, hook duration, music energy, platform, content type, caption style, voice, music style, and edit type. It ranks configurations already tried instead of inventing an untested configuration.

The model is saved as video_preference_model.json beside the configured history file. It is retrained automatically whenever Edit Factory builds its next learned recommendation, using the eligible feedback history. Retention/engagement data and explicit ratings are usable outcomes; placeholder zero scores from videos without feedback are excluded. At least six eligible examples are required before the learned model is used for recommendations.

### Cold start

The creative director also receives built-in editorial principles for strong hooks, story escalation, footage-grounded cuts, pacing, music and payoff. These are general editorial heuristics, not training data: they have no fabricated views, retention, engagement or reward labels. Actual channel-specific preferences come from your ratings, corrections and real video analytics.

Set durable paths in your deployment:

    AIVF_LEARNING_HISTORY_PATH=state/learning_history.json
    AIVF_LEARNING_MODEL_PATH=state/video_preference_model.json

After a video is produced, rate the package and optionally supply the corrected script you would have preferred:

    aivf-learn rate --history state/learning_history.json --package-dir output/my-video --rating 5 --note "Stronger hook, less text, let the payoff breathe" --corrected-script path/to/my-edited-script.txt

Ratings are from 1 (poor) to 5 (excellent). The rating command stores the feedback and attempts a retrain. Corrected scripts are bounded examples of your preferred writing style; they are not treated as factual evidence for future topics.

### Import earlier videos from your channel

When Edit Factory already has the output package and saved edit settings for a video, you can import that video's real stats from the authorized YouTube channel instead of waiting for new uploads:

    aivf-learn import-youtube --history state/learning_history.json --output-root output --state-dir state --max-videos 100

This uses the channel uploads playlist and YouTube Analytics with the OAuth token configured for Edit Factory. It only imports a channel video when it can match the video ID to a local Edit Factory package containing recorded edit settings. It reports unmatched videos rather than assigning them guessed/default editing settings. It also avoids duplicate imports on repeat runs. If there is no authorized token, the command will report that it cannot access the channel; it does not create fake metrics.

### Import TikTok or Instagram Reels performance exports

You can also feed real per-post metrics from a TikTok or Instagram export into the local learner. This path intentionally uses a **file you export and review yourself**; it does not call either platform's analytics API or require their credentials.

Prepare one CSV row per post with a total-metrics snapshot. The required columns are `post_id` (or `video_id`, `media_id`), and `package_dir` (the local Edit Factory package path relative to `output/`). Use real values for at least one useful outcome: retention percentage, or positive views with likes/comments. Supported metric columns include `views`, `video_views`, `plays`, `likes`, `comments`, `average_view_percentage`, and `retention` (a fraction such as `0.75` means 75%). `published_at` is optional. A JSON list of the same row objects is supported too.

Example CSV:

```csv
post_id,package_dir,views,likes,comments,average_view_percentage,published_at
TT123,my-game-short,24000,1800,96,82.5,2026-10-01T12:00:00Z
IG456,my-reel,9100,620,31,68.0,2026-10-03T12:00:00Z
```

Import files separately for each provider:

```powershell
uv run aivf-learn import-platform-export --history state/learning_history.json --platform tiktok --metrics-file state/exports/tiktok.csv --output-root output
uv run aivf-learn import-platform-export --history state/learning_history.json --platform instagram_reels --metrics-file state/exports/instagram.csv --output-root output
```

Use exact local package names or paths under the output root. The importer verifies that the package already contains recorded media metrics and editing settings; it skips unmatched/missing-settings packages and rejects IDs that would be reassigned to a different package. Re-importing the same platform/post ID updates its history row instead of adding duplicate training weight. It does not infer a package from a title, interpret impressions as views, or synthesize missing measurements.

TikTok/Instagram provider exports do not share one universal column schema, and column names can vary by account/export version. Normalize the exported file to the documented column names above before importing. This provides a real-data ingestion route, **not automatic cross-platform API synchronization**; fetching stats directly, verifying that the export is complete, and testing on your authorized accounts remain separate work.

### Sync live TikTok or Instagram Reels metrics

The learner can fetch lifetime aggregate metrics directly from an already-authorized publishing account, then update matching Edit Factory history rows. This does not publish anything. It needs the existing publisher credentials plus read permissions for the account.

```powershell
uv run aivf-learn import-platform-live --history state/learning_history.json --platform tiktok --account-id creator --output-root output --state-dir state --max-videos 100
uv run aivf-learn import-platform-live --history state/learning_history.json --platform instagram_reels --account-id brand --output-root output --state-dir state --max-videos 100
```

TikTok needs the user-granted `video.list` scope in addition to any publishing scope. The Display API provides public-video views, likes, comments and shares; it does not provide audience retention. See the [official TikTok video-list documentation](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list/).

Instagram needs a professional account, the configured pinned Graph API version, and app permissions that allow reading the account's media and insights. The live importer reads Reels/video media and requests aggregate views/engagement insights. Availability varies by account, permission, and Graph API version; credentials and permissions must be authorized outside Edit Factory.

To avoid training on the wrong video, a remote post ID is matched only from an existing platform learning-history record or the durable publishing ledger. When the publisher watched a copy from an incoming folder, the importer can use a bounded exact-file SHA-256 match against media under the output root. It never joins videos by title. IDs that remain unmatched or map to multiple output packages are skipped and shown in the report. You can first import the provider's export using the section above to establish an explicit ID-to-package mapping.

The importer records current lifetime counters, not a historical daily time series. It updates the same platform/post history record on repeat runs rather than duplicating it. After importing, the command retrains the preference model and reports the available holdout evaluation. Low-view posts or records without sufficient metrics are not silently given a fabricated reward.

### Retrain manually

    aivf-learn train --history state/learning_history.json

### Check whether it actually learned

Every train/rate command also runs a retrospective holdout evaluation when enough eligible videos exist. To run the evaluation by itself:

    aivf-learn evaluate --history state/learning_history.json

When the history has timestamps, the evaluation holds out the newest videos. Without a complete timeline it uses a deterministic hash split. It reports mean absolute error (MAE) for the model against a simple training-mean baseline and says whether the model beat that baseline. Ten eligible results are required for this diagnostic (at least six for training and two for holdout). Below that threshold it reports insufficient data rather than inventing labels.

This retrospective split is a diagnostic, not proof of future success. Keep checking performance on later uploads that did not influence training or tuning. More varied examples and more future videos will make the evaluation more informative.

## Quality and trust boundaries

- The base OpenAI/Groq language model stays fixed. Only the small local preference model is retrained.
- Model-proposed facts are not proof. Research, factuality, rights, media, script and editorial gates remain in force.
- The agent must not claim footage exists unless supplied evidence supports that claim.
- It does not add an Edit Factory/AI-tool watermark or promotional credit to public metadata.
- It does not remove source attribution or suppress YouTube's native altered/synthetic-media disclosure when required.
- Critique scores are editorial heuristics, not guarantees of quality, retention, monetization or revenue.
- Each video can require two model calls (draft + critique) and a third for revision. Include those expected calls in cost estimates and provider limits.
- Publishing safety defaults still apply: human approval, rights/factuality review, duplicate protection, daily limits and emergency stop.

## Testing

Unit tests cover schema validation, missing-provider fallback, bounded revision, injection-like model output, delivery of scripts/edit directions into the planner, creator-feedback context and held-out evaluation. CI is the required validation before merging.
