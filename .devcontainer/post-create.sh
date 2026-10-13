#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

command -v uv >/dev/null
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
command -v tesseract >/dev/null

python --version
uv --version
ffmpeg -hide_banner -version | sed -n '1p'
ffprobe -hide_banner -version | sed -n '1p'
tesseract --version | sed -n '1p'

# Use the committed lockfile; do not silently resolve a new dependency set.
uv sync --frozen --python 3.12 --extra web --extra dev --extra ocr
uv pip check

echo
echo "Edit Factory v3 sandbox ready."
echo "Run .devcontainer/verify-media.sh for the synthetic FFmpeg + media test smoke."
