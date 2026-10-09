# Video Thinking AI

Edit Factory's Video Thinking AI is a specialist creative-director stage, not a general-purpose chatbot. It is designed only to plan and improve video stories, scripts, footage selection/edit direction, pacing, audio direction, thumbnails, and titles.

## How a video gets made

1. **Understand the brief.** The agent receives the topic, target duration, and available research/context. Research is treated as evidence, not instructions.
2. **Create a story plan.** It proposes a specific angle, human stakes, a short hook, a reason to keep watching, a payoff, narration lines, five phase-specific editing directions, music direction, thumbnail concept, titles, and description.
3. **Critique the draft.** A separate model call scores specificity, natural language, narrative logic, factual restraint, visual editability, repetition, and duration fit. It returns a score, short issues, and actionable revision notes—not private chain-of-thought.
4. **Revise once when needed.** Weak or cliché-heavy drafts get one bounded rewrite. This limits latency and model costs.
5. **Use the existing production pipeline.** The validated plan feeds Edit Factory's existing idea, script, timeline, thumbnail, voiceover, and QC stages. The model cannot directly run arbitrary commands or bypass the existing render and publishing controls.
6. **Gate uncertain output.** Invalid output falls back to the existing deterministic planner. A low critique score, failed style checks, or failed critique/revision marks the plan for human review. The autonomous publisher must not silently treat that plan as approved.
7. **Retrain its own small preference model.** New YouTube retention/engagement results and your explicit 1–5 ratings become training examples. Edit Factory refits a local weighted-ridge model that predicts which previously tested edit settings are likely to work for your channel. Future recommendations use those predictions, with context matching for platform/content type. This is actual, lightweight retraining of the video-preference model; the OpenAI/Groq language model is not retrained.

## Enable it

The stage is enabled by default, but an AI provider key is required for model-backed planning. Configure one of the providers already supported by Edit Factory:

- \`OPENAI_API_KEY\` (model configured by \`AIVF_OPENAI_MODEL\`, default \`gpt-4o-mini\`)
- \`GROQ_API_KEY\` (model configured by \`AIVF_GROQ_MODEL\`, default \`llama-3.3-70b-versatile\`)

Set \`AIVF_VIDEO_THINKING_ENABLED=0\` to disable the stage. With no provider configured, the stage records \`not_configured\` and the existing deterministic planner remains available without making a network call.

The stage writes \`video_thinking.json\` in the package directory. It contains the validated creative plan, critique score/issues, whether revision was attempted, and whether human review is required. It does not store model credentials or private chain-of-thought.

## Personal learning / retraining

The local learner is deliberately small: it does not train a language model or require a GPU. It retrains a weighted ridge-regression model from prior editing settings plus actual outcomes. Features currently include cuts per minute, average shot duration, hook duration, music energy, platform, content type, caption style, voice, music style, and edit type. It ranks configurations already tried instead of inventing an untested configuration.

The model is saved as \`video_preference_model.json\` beside the configured history file. It is retrained automatically whenever Edit Factory builds its next learned recommendation, using the complete eligible feedback history. It uses retention/engagement data and explicit ratings; placeholder zero scores from videos without feedback are excluded. It starts recommending from the learned model after at least six eligible examples. Before then it uses the existing cold-start and nearest-history behaviour.

Set the durable paths for your deployment:

\`\`\`bash
AIVF_LEARNING_HISTORY_PATH=state/learning_history.json
AIVF_LEARNING_MODEL_PATH=state/video_preference_model.json
\`\`\`

After a video is produced, you can rate that package yourself. For example:

\`\`\`bash
aivf-learn rate --history state/learning_history.json --package-dir output/my-video --rating 5 --note "Strong hook and pacing"
\`\`\`

Ratings are from 1 (poor) to 5 (excellent). The rating command immediately attempts a retrain. To retrain manually or inspect whether enough examples exist:

\`\`\`bash
aivf-learn train --history state/learning_history.json
\`\`\`

The command prints model status, eligible sample count, and training-error diagnostics. Training error is not proof the model will generalize; judge improvements against later uploads it did not train on. The learner keeps the previous trained model while new history is below the sample minimum.

## Quality and trust boundaries

- The base OpenAI/Groq language model stays fixed. What gets retrained is Edit Factory's small local preference model, using measured video outcomes and your ratings; this learns editing preferences without attempting to recreate ChatGPT.
- Model-proposed facts are not proof. Existing research, factuality, rights, media, script, and editorial gates remain in force.
- It must not claim footage exists unless the supplied evidence supports that claim.
- It does not generate an Edit Factory/AI-tool watermark or promotional credit in public metadata.
- It does not remove source attribution or suppress YouTube's native altered/synthetic-media disclosure when required.
- Its critique score is an editorial heuristic, not an objective guarantee of quality, retention, monetization, or revenue.
- Each video can require two model calls (draft + critique) and a third for revision. Include that expected provider usage in the configured per-video cost estimate and provider billing limits. Usage is not billed or measured exactly by the heuristic score.
- Publishing safety defaults still apply: human approval, rights/factuality review, duplicate protection, daily limits, and emergency stop.

## Testing

The unit tests cover schema validation, missing-provider fallback, two-call review, bounded revision, injection-like model output, and delivery of the resulting script/edit directions into the existing planner. CI remains the required validation before merging changes.
