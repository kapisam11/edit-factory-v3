FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# Mirror the native tools needed by the project's media and OCR integration tests.
RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv resolves project dependencies from the committed uv.lock file.
RUN python -m pip install --no-cache-dir uv
