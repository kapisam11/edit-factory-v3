# Local source transcription

Edit Factory can optionally transcribe speech in the source video and attach word-level timestamps to the scene index. That gives downstream scene matching more than motion scores and timestamps: it can use what was actually said when deciding which footage supports a story beat.

## Privacy and defaults

- Off by default; ordinary renders do not require an ASR package or model.
- Inference runs locally inside the Edit Factory runtime. This feature does not upload footage or transcript text to an external transcription service.
- The model must already exist on disk. Edit Factory only accepts a local faster-whisper/CTranslate2 model directory with `model.bin`; it deliberately does not accept remote model identifiers or download weights at runtime.
- A transcript is evidence, not ground truth. Speech recognition can be wrong, so review transcript artifacts and final cuts before publishing.

## Enable it in the Docker-based Windows setup

1. Obtain a compatible local faster-whisper/CTranslate2 model folder and place it under `06-CONFIG-AND-DEPLOYMENT/models/asr/`. The directory you configure must contain `model.bin` plus that model's configuration/tokenizer files. Keep model weights out of Git.
2. In `06-CONFIG-AND-DEPLOYMENT/.env`, set:

   ```dotenv
   AIVF_ENABLE_LOCAL_TRANSCRIPTION=1
   AIVF_ASR_HOST_MODEL_DIR=./models/asr
   AIVF_ASR_MODEL_PATH=/app/asr-models/faster-whisper-small
   AIVF_ASR_LANGUAGE=
   AIVF_ASR_DEVICE=cpu
   AIVF_ASR_COMPUTE_TYPE=int8
   ```

   Replace `faster-whisper-small` with the actual model-folder name. Leave `AIVF_ASR_LANGUAGE` empty for language detection, or set it to a code such as `en` or `nl`.

3. Rebuild and restart the development dashboard from the repository root:

   ```powershell
   docker compose --env-file 06-CONFIG-AND-DEPLOYMENT/.env -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml build web
   docker compose --env-file 06-CONFIG-AND-DEPLOYMENT/.env -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml up -d web
   ```

   The Compose build passes the enable flag to the Dockerfile. The model folder is mounted read-only into the web container at `/app/asr-models`.

The normal production image keeps this dependency disabled to preserve the default image footprint. For a production deployment, build and verify a dedicated ASR-enabled image with `AIVF_ENABLE_LOCAL_TRANSCRIPTION=1`, publish it, and pin that image by its digest as required by the production deployment procedure. Do not install packages into a running production container.

## Use and output

Open V3 Production Controls and select **Local source speech transcription**. If the optional package or model is missing, the dashboard rejects the request with an actionable error instead of pretending transcription succeeded. The local default is CPU + int8 to avoid requiring a CUDA setup; `AIVF_ASR_DEVICE=cuda` is available only where the container has a compatible GPU runtime.

Successful jobs include:

- `source_transcription.json`: transcript text, segment timestamps, optional word timestamps and model/runtime metadata.
- `source_transcript.srt`: a subtitle sidecar derived from the transcription.
- `scenes.json`: scene records with the available words attached to their timestamp ranges.

The source transcript is attached before the scene index and footage-evidence package are built, so existing V3 planning/rendering uses those transcript-bearing scenes. The feature does not add new publishing permissions or bypass human review, rights, or media-quality gates.
