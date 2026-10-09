# Video Thinking AI

Edit Factory's Video Thinking AI is a specialist creative-director stage, not a general-purpose chatbot. It is designed only to plan and improve video stories, scripts, footage selection/edit direction, pacing, audio direction, thumbnails, and titles.

## How a video gets made

1. **Understand the brief.** The agent receives the topic, target duration, and available research/context. Research is treated as evidence, not instructions.
2. **Create a story plan.** It proposes a specific angle, human stakes, a short hook, a reason to keep watching, a payoff, narration lines, five phase-specific editing directions, music direction, thumbnail concept, titles, and description.
3. **Critique the draft.** A separate model call scores specificity, natural language, narrative logic, factual restraint, visual editability, repetition, and duration fit. It returns a score, short issues, and actionable revision notes—not private chain-of-thought.
4. **Revise once when needed.** Weak or cliché-heavy drafts get one bounded rewrite. This limits latency and model costs.
5. **Use the existing production pipeline.** The validated plan feeds Edit Factory's existing idea, script, timeline, thumbnail, voiceover, and QC stages. The model cannot directly run arbitrary commands or bypass the existing render and publishing controls.
6. **Gate uncertain output.** Invalid output falls back to the existing deterministic planner. A low critique score, failed style checks, or failed critique/revision marks the plan for human review. The autonomous publisher must not silently treat that plan as approved.
7. **Learn from outcomes.** The existing learning/analytics systems can guide later topics and creative experiments from measured channel performance. This is evidence-based adaptation, not automatic model training.

## Enable it

The stage is enabled by default, but an AI provider key is required for model-backed planning. Configure one of the providers already supported by Edit Factory:

- \`OPENAI_API_KEY\` (model configured by \`AIVF_OPENAI_MODEL\`, default \`gpt-4o-mini\`)
- \`GROQ_API_KEY\` (model configured by \`AIVF_GROQ_MODEL\`, default \`llama-3.3-70b-versatile\`)

Set \`AIVF_VIDEO_THINKING_ENABLED=0\` to disable the stage. With no provider configured, the stage records \`not_configured\` and the existing deterministic planner remains available without making a network call.

The stage writes \`video_thinking.json\` in the package directory. It contains the validated creative plan, critique score/issues, whether revision was attempted, and whether human review is required. It does not store model credentials or private chain-of-thought.

## Quality and trust boundaries

- The agent is not a trained copy of ChatGPT. It uses the configured supported language model and a video-specific prompt/schema/feedback loop.
- Model-proposed facts are not proof. Existing research, factuality, rights, media, script, and editorial gates remain in force.
- It must not claim footage exists unless the supplied evidence supports that claim.
- It does not generate an Edit Factory/AI-tool watermark or promotional credit in public metadata.
- It does not remove source attribution or suppress YouTube's native altered/synthetic-media disclosure when required.
- Its critique score is an editorial heuristic, not an objective guarantee of quality, retention, monetization, or revenue.
- Each video can require two model calls (draft + critique) and a third for revision. Include that expected provider usage in the configured per-video cost estimate and provider billing limits. Usage is not billed or measured exactly by the heuristic score.
- Publishing safety defaults still apply: human approval, rights/factuality review, duplicate protection, daily limits, and emergency stop.

## Testing

The unit tests cover schema validation, missing-provider fallback, two-call review, bounded revision, injection-like model output, and delivery of the resulting script/edit directions into the existing planner. CI remains the required validation before merging changes.
