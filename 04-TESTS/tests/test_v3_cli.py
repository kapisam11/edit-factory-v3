import json

from ai_video_factory.v3_cli import main


def test_v3_cli_writes_valid_blueprint(tmp_path):
    output = tmp_path / "blueprint.json"
    code = main([
        "Eggchan",
        "--context", "The loyal friend who stayed and protected everyone.",
        "--seconds", "30",
        "--bpm", "120",
        "--output", str(output),
    ])

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"] == "3.0.0"
    assert payload["quality"]["passed"] is True
    assert len(payload["capabilities"]) == 40
    assert payload["clip_plan"][-1]["end"] == 30.0
