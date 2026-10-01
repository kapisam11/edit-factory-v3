import json
import sqlite3
from pathlib import Path

from ai_video_factory.authorization import role_allows, Role
from ai_video_factory.cache_lifecycle import CacheLifecycle
from ai_video_factory.factuality_guard import metadata_fact_gate
from ai_video_factory.feedback_store import FeedbackStore
from ai_video_factory.human_review import create_review, review_gate
from ai_video_factory.job_backend import SQLiteJobBackend
from ai_video_factory.metadata_guardrails import build_upload_metadata
from ai_video_factory.rights_policy import make_declaration, rights_gate
from ai_video_factory.scene_detect_adapter import scene_windows
from ai_video_factory.security_audit import high_severity, scan_repository
from ai_video_factory.hardware import ffmpeg_preset_for
from ai_video_factory.production_readiness import run_readiness

def test_security_audit_has_no_high_findings():
    root=Path(__file__).resolve().parents[2]
    assert not high_severity(scan_repository(root))

def test_factuality_blocks_unsupported_numbers():
    report=metadata_fact_gate("Minecraft in 2026", "The event had 400 players.")
    assert report["publish_blocked"] is True
    reviewed=metadata_fact_gate(
        "Minecraft in 2026", "The event had 400 players.",
        evidence=[{"text":"The event had 400 players."}], fact_reviewed=True,
    )
    assert reviewed["publish_blocked"] is False

def test_metadata_contains_factuality():
    report=build_upload_metadata("Minecraft clutch", hook="The final clutch")
    assert "factuality" in report

def test_rights_gate_requires_evidence_for_third_party():
    bad={"asset_id":"x","source":"youtube","rights_basis":"commercial_license","declared_by":"tester","declared_at":"2026-09-29T10:00:00Z"}
    assert rights_gate([bad])["publish_blocked"] is True
    good=make_declaration(
        asset_id="x",source="licensed",rights_basis="commercial_license",
        evidence_url="https://example.com/permission",license_url="https://example.com/license",
    )
    assert rights_gate([good])["publish_blocked"] is False

def test_human_review_requires_all_dimensions():
    review=create_review("tester", {key:5 for key in ("hook","pacing","visual_relevance","caption_readability","audio","rights","metadata")}, approved=True)
    assert review_gate(review)["publish_blocked"] is False

def test_rbac():
    assert role_allows(Role.ADMIN, Role.EDITOR)
    assert not role_allows(Role.VIEWER, Role.EDITOR)

def test_job_backend_reclaims_expired_and_rejects_stale_worker(tmp_path):
    backend=SQLiteJobBackend(tmp_path/"queue.db",visibility_timeout=0.01)
    backend.enqueue("j1",{"topic":"test"})
    first=backend.claim()
    assert first and first.job_id=="j1"
    import time
    time.sleep(0.02)
    assert backend.recover_expired()==1
    second=backend.claim()
    assert second and second.job_id=="j1"
    assert first.worker_token != second.worker_token
    assert backend.complete(first.job_id, first.worker_token) is False
    assert backend.complete(second.job_id, second.worker_token) is True

def test_feedback_store_aggregates(tmp_path):
    store=FeedbackStore(tmp_path/"feedback.db")
    store.add("v1","youtube","Storytelling","2026-09-29T10:00:00Z",{"retention":0.8})
    store.add("v2","youtube","Storytelling","2026-09-29T11:00:00Z",{"retention":0.6})
    data=store.aggregate("youtube")
    assert data["youtube:Storytelling"]["means"]["retention"]==0.7

def test_cache_lifecycle(tmp_path):
    cache=CacheLifecycle(tmp_path/"cache",max_bytes=10,ttl_seconds=1)
    (cache.root/"a.tmp").write_bytes(b"12345678901")
    assert cache.enforce_size()==1

def test_scene_adapter_handles_missing_file():
    assert scene_windows("not-a-file.mp4",10.0)==[]

def test_hardware_presets_are_known():
    assert ffmpeg_preset_for("h264_nvenc")["codec"]=="h264_nvenc"
    assert ffmpeg_preset_for("h264_amf")["codec"]=="h264_amf"

def test_readiness_reports_exactly_34_items():
    items=run_readiness(Path(__file__).resolve().parents[2])
    assert len(items)==34
    assert [item.number for item in items]==list(range(1,35))


def test_log_redaction_patterns():
    from ai_video_factory.production_guardrails import redact_log_message
    value=redact_log_message("Authorization: Bearer abc123 token=xyz secret: nope password=bad")
    assert "abc123" not in value
    assert "xyz" not in value
    assert "nope" not in value
    assert "bad" not in value


def test_asset_import_returns_renamed_uri(tmp_path):
    from ai_video_factory.asset_manager import AssetManager
    source=tmp_path/"clip.mp3"
    source.write_bytes(b"fake")
    manager=AssetManager(tmp_path/"assets")
    first=manager.import_asset(str(source),"sfx")
    second=manager.import_asset(str(source),"sfx")
    assert first=="assets://sfx/clip.mp3"
    assert second=="assets://sfx/clip-2.mp3"


def test_upload_publish_review_gate():
    from ai_video_factory.upload_package import finalize_upload_package
    import os
    old=os.environ.get("AIVF_REQUIRE_HUMAN_REVIEW")
    os.environ["AIVF_REQUIRE_HUMAN_REVIEW"]="1"
    try:
        result=finalize_upload_package("/tmp/aivf-review-test",topic="test",summary={"human_review":None})
        assert result["publish_ready"] is False
    finally:
        if old is None: os.environ.pop("AIVF_REQUIRE_HUMAN_REVIEW",None)
        else: os.environ["AIVF_REQUIRE_HUMAN_REVIEW"]=old


def test_rights_declaration_timestamp_is_valid():
    future = make_declaration(
        asset_id="x",
        source="licensed",
        rights_basis="owned",
        declared_by="tester",
        now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(days=1),
    )
    assert rights_gate([future])["publish_blocked"] is True
