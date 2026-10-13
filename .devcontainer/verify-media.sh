#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
export PYTHONPATH="01-MAIN-CODE:02-WEB-FILES:03-SIDE-CODE${PYTHONPATH:+:$PYTHONPATH}"

command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
command -v uv >/dev/null

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/aivf-media-smoke.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT
fixture="$tmpdir/synthetic-video-with-audio.mp4"

echo "1/3: Generate a small synthetic H.264 + AAC fixture with FFmpeg."
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc=size=360x640:rate=24" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -t 3 -shortest -pix_fmt yuv420p \
  -c:v libx264 -preset ultrafast -crf 30 \
  -c:a aac -b:a 96k "$fixture"

echo "2/3: Verify the container, duration, video geometry, and audio stream."
ffprobe -v error -show_entries format=duration:stream=codec_type,codec_name,width,height,sample_rate,channels \
  -of json "$fixture" | python -c '
import json, sys
data = json.load(sys.stdin)
streams = data.get("streams", [])
video = [s for s in streams if s.get("codec_type") == "video"]
audio = [s for s in streams if s.get("codec_type") == "audio"]
duration = float(data.get("format", {}).get("duration", 0))
assert len(video) == 1, f"expected one video stream, got {len(video)}"
assert video[0].get("codec_name") == "h264", video[0]
assert video[0].get("width") == 360 and video[0].get("height") == 640, video[0]
assert len(audio) == 1, f"expected one audio stream, got {len(audio)}"
assert audio[0].get("codec_name") == "aac", audio[0]
assert duration >= 2.8 and duration <= 3.2, f"unexpected duration: {duration}"
print(f"PASS: H.264 360x640 + AAC audio, duration={duration:.3f}s")
'

echo "3/3: Run the rendering, media-health, and output-QC regression tests."
uv run pytest -q \
  04-TESTS/tests/test_media_hardening.py \
  04-TESTS/tests/test_media_host.py \
  04-TESTS/tests/test_real_media_health.py \
  04-TESTS/tests/test_real_render_integration.py \
  04-TESTS/tests/test_render_engine.py \
  04-TESTS/tests/test_render_engine_hardening.py \
  04-TESTS/tests/test_quality_control_v3.py \
  04-TESTS/tests/test_v3_golden_media.py

echo "PASS: FFmpeg fixture generation and selected media/render regression tests."
