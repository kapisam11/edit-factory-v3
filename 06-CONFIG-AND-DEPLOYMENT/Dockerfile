FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AIVF_STATE_DIR=/app/state \
    AIVF_UPLOAD_DIR=/app/uploads \
    AIVF_OUTPUT_DIR=/app/output

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr ca-certificates fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
COPY 01-MAIN-CODE ./01-MAIN-CODE
COPY 02-WEB-FILES ./02-WEB-FILES
COPY 03-SIDE-CODE ./03-SIDE-CODE
COPY 04-TESTS ./04-TESTS
COPY 05-EXTENSIONS ./05-EXTENSIONS
COPY 06-CONFIG-AND-DEPLOYMENT ./06-CONFIG-AND-DEPLOYMENT

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir '/app[web]'

RUN groupadd --system aivf \
    && useradd --system --gid aivf --create-home aivf \
    && mkdir -p /app/output /app/uploads /app/knowledge_base_v2 /app/state \
    && chown -R aivf:aivf /app

USER aivf
EXPOSE 5000

CMD ["gunicorn", "-c", "/app/06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py", "app.wsgi:app"]
