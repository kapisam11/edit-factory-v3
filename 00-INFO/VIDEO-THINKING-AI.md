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

To retrain manually:

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
