from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_video_factory import quality_control_v3, upload_package
from ai_video_factory.gaming_profile import (
    build_gaming_planning_context,
    build_gaming_profile_report,
    validate_gaming_brief,
)
from ai_video_factory.media_quality_gates import audio_signal_report, validate_srt_content
from ai_video_factory.rights_policy import make_declaration, rights_gate


def _good_rights() -> dict[str, object]:
    return make_declaration(
        asset_id="gameplay",
        source="user_provided",
        rights_basis="owned",
        declared_by="local-reviewer",
        asset_origin="original",
        commercial_use="allowed",
        permitted_uses="Monetized gameplay",
        now=datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc),
    )


def test_gaming_profile_requires_game_event_and_supported_angle() -> None:
    assert validate_gaming_brief("", "Valorant clutch", "clutch")
    assert validate_gaming_brief("Valorant", "", "clutch")
    assert validate_gaming_brief("Valorant", "Valorant clutch", "exploit-evasion")


def test_gaming_planning_context_has_angle_and_grounding_constraints() -> None:
    context = build_gaming_planning_context(
        game_name="Valorant",
        event_brief="A 1v3 retake on Ascent",
        angle="strategy",
    )
    assert "Game supplied by creator: Valorant" in context
    assert "Creator's event brief" in context
    assert "strategy" in context.lower()
    assert "Never invent" in context
    assert "verified creator context" in context


def test_gaming_report_blocks_unsupported_critical_action() -> None:
    report = build_gaming_profile_report(
        game_name="Minecraft",
        event_brief="A last-second escape from a trap",
        angle="storytelling",
        footage_evidence={"scene_count": 2, "top_scenes": [{"id": "s1"}, {"id": "s2"}]},
        clip_evidence=[
            {
                "clip_index": 1,
                "purpose": "Hook",
                "status": "unsupported",
                "reason": "No visible evidence of the claimed escape",
            }
        ],
        source_metadata={
            "asset_origin": "original",
            "rights_basis": "owned",
            "commercial_use": "allowed",
        },
    )
    assert report["publish_blocked"] is True
    assert report["clip_evidence"]["critical_unverified_clips"][0]["clip_index"] == 1
    assert report["event_brief_status"] == "creator_supplied_not_proof_of_event"
    assert report["human_review_status"] == "required"


def test_gaming_report_blocks_unverified_commercial_use() -> None:
    report = build_gaming_profile_report(
        game_name="Minecraft",
        event_brief="A cave escape",
        angle="clutch",
        footage_evidence={"scene_count": 1, "top_scenes": [{"id": "s1"}]},
        clip_evidence=[],
        source_metadata={"asset_origin": "original", "rights_basis": "owned"},
    )
    assert report["publish_blocked"] is True
    assert "commercial use has not been explicitly verified as allowed" in report["rights_provenance"]["unresolved_items"]


def test_srt_reference_cues_pass_timing_and_readability_checks() -> None:
    srt = (
        "1\n00:00:00,000 --> 00:00:01,200\n"
        "They committed to the retake.\n\n"
        "2\n00:00:01,200 --> 00:00:02,900\n"
        "The smoke created one safe angle.\n"
    )
    report = validate_srt_content(srt, duration_seconds=3.0)
    assert report["passed"] is True
    assert report["cue_count"] == 2
    assert report["human_visibility_review_required"] is True


def test_srt_cue_beyond_video_duration_is_blocked_with_timestamp() -> None:
    srt = "1\n00:00:09,000 --> 00:00:12,000\nThe round is over.\n"
    report = validate_srt_content(srt, duration_seconds=10.0)
    issue = next(error for error in report["errors"] if error["code"] == "caption_exceeds_video")
    assert report["passed"] is False
    assert issue["timestamp_seconds"] == pytest.approx(10.0)


def test_srt_text_overflow_is_blocked() -> None:
    srt = "1\n00:00:00,000 --> 00:00:01,000\n" + ("long " * 12) + "\n"
    report = validate_srt_content(srt, duration_seconds=2.0)
    assert report["passed"] is False
    assert any(error["code"] == "caption_text_overflow" for error in report["errors"])


def test_ass_to_srt_emits_valid_three_digit_milliseconds() -> None:
    converted = upload_package._ass_to_srt(
        "[Events]\n"
        "Dialogue: 0,0:00:01.23,0:00:02.45,Default,,0,0,0,,A clean retake\\Nwith no invented claims\n"
    )
    assert "00:00:01,230 --> 00:00:02,450" in converted
    assert "A clean retake with no invented claims" in converted
    assert validate_srt_content(converted, duration_seconds=3.0)["passed"] is True


def test_audio_signal_report_passes_a_balanced_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        quality_control_v3,
        "check_audio_levels",
        lambda _path: {
            "integrated_lufs": "-16.0",
            "true_peak": "-1.5",
            "loudness_range": "7.0",
            "silence_segments": [],
        },
    )
    report = audio_signal_report("reference.mp4", is_gaming=True)
    assert report["passed"] is True
    assert report["metrics"]["integrated_lufs"] == pytest.approx(-16.0)
    assert report["human_listening_review_required"] is True


def test_audio_signal_report_flags_clipping_risk_with_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        quality_control_v3,
        "check_audio_levels",
        lambda _path: {
            "integrated_lufs": "-16.0",
            "true_peak": "-0.05",
            "loudness_range": "8.0",
            "silence_segments": [],
        },
    )
    report = audio_signal_report("clipped.mp4", is_gaming=True)
    assert report["passed"] is False
    issue = next(error for error in report["errors"] if error["code"] == "true_peak_clipping_risk")
    assert "dBTP" in issue["message"]


def test_rights_gate_requires_commercial_use_declaration_for_gaming() -> None:
    complete = _good_rights()
    assert rights_gate([complete], require_commercial_use=True)["publish_blocked"] is False
    incomplete = make_declaration(
        asset_id="gameplay",
        source="user_provided",
        rights_basis="owned",
        declared_by="local-reviewer",
        asset_origin="original",
        commercial_use="review_required",
        now=datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc),
    )
    report = rights_gate([incomplete], require_commercial_use=True)
    assert report["publish_blocked"] is True
    assert any("commercial use" in error for error in report["checked"][0]["errors"])


def test_package_blocks_rights_only_source_manifest_without_titles_or_urls() -> None:
    good = _good_rights()
    payload = {
        "requires_rights_declaration": True,
        "requires_commercial_rights_review": True,
        "source_manifest": {
            "schema_version": "1.0.0",
            "rights_status": "owned",
            "asset_origin": good["asset_origin"],
            "commercial_use": good["commercial_use"],
            "permitted_uses": good["permitted_uses"],
            "declared_by": good["declared_by"],
            "declared_at": good["declared_at"],
            "metadata": {
                **good,
                "title": "",
                "creator": "",
                "url": "",
            },
        },
    }
    report = upload_package.media_rights_report(payload)
    assert report["publish_blocked"] is False
    assert report["status"] == "cleared"


def test_final_upload_package_blocks_caption_cue_after_video_end(monkeypatch, tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    video = package / "final.mp4"
    video.write_bytes(b"fake video used with mocked ffprobe")
    captions = package / "captions.srt"
    captions.write_text(
        "1\n00:00:09,000 --> 00:00:12,000\nLate caption outside the render.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_OUTPUT_FPS", "30")
    monkeypatch.setenv("AIVF_OUTPUT_FPS_TOLERANCE", "0.5")
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "1")
    monkeypatch.setenv("AIVF_ENABLE_AUDIO_SIGNAL_QC", "0")
    monkeypatch.setattr(
        upload_package,
        "_probe_media",
        lambda _path: {
            "available": True,
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "30/1",
                    "r_frame_rate": "30/1",
                    "duration": "10.0",
                    "codec_name": "h264",
                },
                {"codec_type": "audio", "duration": "10.0", "codec_name": "aac"},
            ],
            "format": {
                "duration": "10.0",
                "size": "1000",
                "format_name": "mov,mp4",
            },
        },
    )

    manifest = upload_package.finalize_upload_package(
        str(package),
        topic="Valorant retake",
        summary={"facts_reviewed": True},
        script="The player makes a retake.",
        platform="youtube_shorts",
        final_video=str(video),
        caption_path=str(captions),
    )
    assert manifest["media_contract"]["passed"] is False
    assert any("caption_exceeds_video" in error for error in manifest["media_contract"]["errors"])
    assert manifest["publish_ready"] is False
