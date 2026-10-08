FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH" \
    AIVF_STATE_DIR=/app/state \
    AIVF_UPLOAD_DIR=/app/uploads \
    AIVF_OUTPUT_DIR=/app/output

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr ca-certificates fonts-dejavu curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
COPY uv.lock ./uv.lock
COPY LICENSE ./LICENSE
COPY 01-MAIN-CODE ./01-MAIN-CODE
COPY 02-WEB-FILES ./02-WEB-FILES
COPY 03-SIDE-CODE ./03-SIDE-CODE
COPY 06-CONFIG-AND-DEPLOYMENT ./06-CONFIG-AND-DEPLOYMENT

RUN python -m pip install --no-cache-dir --upgrade pip uv==0.12.17 \
    && uv sync --frozen --no-dev --extra web --extra ocr

# Production ML assets are baked into the immutable image from an immutable source
# commit. Runtime network downloads remain disabled by asset_manager.py.
ARG MOBILENET_COMMIT=97406996b1eee2d40eb0a00ae567cf41e23369f9
RUN mkdir -p /app/.models/mobilenet_ssd \
    && curl --fail --location --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 \
       "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/$MOBILENET_COMMIT/deploy.prototxt" \
       -o /app/.models/mobilenet_ssd/deploy.prototxt \
    && curl --fail --location --retry 5 --retry-all-errors --connect-timeout 15 --max-time 600 \
       "https://github.com/chuanqi305/MobileNet-SSD/raw/$MOBILENET_COMMIT/mobilenet_iter_73000.caffemodel" \
       -o /app/.models/mobilenet_ssd/mobilenet.caffemodel \
    && test -s /app/.models/mobilenet_ssd/deploy.prototxt \
    && test -s /app/.models/mobilenet_ssd/mobilenet.caffemodel \
    && sha256sum /app/.models/mobilenet_ssd/deploy.prototxt /app/.models/mobilenet_ssd/mobilenet.caffemodel > /app/.models/mobilenet_ssd/SHA256SUMS \
    && python 03-SIDE-CODE/tools/generate_model_manifest.py /app/.models/mobilenet_ssd $MOBILENET_COMMIT
RUN echo "AIVF_MODEL_LOCK_BUILD_MANIFEST" && cat /app/.models/mobilenet_ssd/model-manifest.json
RUN groupadd --system aivf \
    && useradd --system --gid aivf --create-home aivf \
    && mkdir -p /app/output /app/uploads /app/knowledge_base_v3 /app/state \
    && chown -R aivf:aivf /app

USER aivf
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD curl --fail --silent http://127.0.0.1:5000/api/health || exit 1

CMD ["gunicorn", "-c", "/app/06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py", "app.wsgi:app"]
