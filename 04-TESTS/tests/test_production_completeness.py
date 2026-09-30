from pathlib import Path

from PIL import Image
import pytest

from ai_video_factory.copyright_policy import (
    CopyrightEvidence,
    CopyrightStatus,
    ExactMatchProvider,
    copyright_gate,
    fingerprint_file,
)
from ai_video_factory.factuality_guard import metadata_fact_gate
from ai_video_factory.security_audit import audit_source, high_severity
from ai_video_factory.filesystem_guard import safe_delete, safe_join, safe_rmtree
from ai_video_factory.human_review import composite_review_score
from ai_video_factory.learning_loop import LearningPolicy, train_policy
from ai_video_factory.observability_metrics import MetricsRegistry
from ai_video_factory.production_completeness import run_completeness
from ai_video_factory.thumbnail_quality import score_thumbnail


def test_safe_join_and_delete_are_fail_closed(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    child = safe_join(root, "nested", "file.txt")
    assert child == root / "nested" / "file.txt"
    with_root = root / "delete.txt"
    with_root.write_text("x", encoding="utf-8")
    assert safe_delete(root, with_root) == with_root
    with_root_dir = root / "dir"
    with_root_dir.mkdir()
    safe_rmtree(root, with_root_dir)
    assert not with_root_dir.exists()


def test_copyright_provider_and_gate(tmp_path: Path):
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"owned")
    digest = fingerprint_file(asset)
    provider = ExactMatchProvider(allowed_hashes=[digest])
    evidence = provider.verify("asset", digest)
    assert evidence.provider_status is CopyrightStatus.VERIFIED
    result = copyright_gate([evidence], require_provider=True)
    assert result["publish_blocked"] is False


def test_thumbnail_quality_score(tmp_path: Path):
    target = tmp_path / "thumbnail.png"
    image = Image.new("RGB", (1280, 720), (20, 20, 20))
    for x in range(200, 1080):
        for y in range(160, 560):
            image.putpixel((x, y), (220, 160, 50))
    image.save(target)
    report = score_thumbnail(target)
    assert 0.0 <= float(report["score"]) <= 1.0
    assert float(report["width"]) == 1280


def test_human_composite_score():
    scores = {key: 5 for key in ("hook", "pacing", "visual_relevance", "caption_readability", "audio", "rights", "metadata")}
    assert composite_review_score(scores) == 1.0


def test_learning_policy_changes_after_training():
    policy = LearningPolicy.default()
    before = policy.version
    trained = train_policy([
        {"features": {"hook": 1, "pacing": 1, "visual_relevance": 1}, "target": 1},
        {"features": {"hook": 0, "pacing": 0, "visual_relevance": 0}, "target": 0},
    ])
    assert trained.version > before
    assert trained.predict({"hook": 1, "pacing": 1, "visual_relevance": 1}) > 0.0


def test_metrics_registry_exposes_percentiles_and_prometheus():
    registry = MetricsRegistry()
    for value in (10, 20, 30, 40, 50):
        registry.observe_ms("render", value)
    registry.increment("renders")
    assert registry.percentile_ms("render", 0.95) >= 40
    assert "aivf_renders 1" in registry.prometheus()
    assert "aivf_render_avg_ms" in registry.prometheus()


def test_completeness_report_is_not_blocked(tmp_path: Path):
    report = run_completeness(tmp_path)
    names = {item.name for item in report}
    assert "command-execution-security" in names
    assert "filesystem-containment" in names
    assert "rights-policy" in names
    assert all(item.status != "BLOCKED" for item in report)


def test_unverified_copyright_is_blocked():
    evidence = CopyrightEvidence(
        asset_id="unknown",
        sha256="abc",
        rights_status="review_required",
        provider_status=CopyrightStatus.MANUAL_REVIEW,
    )
    assert copyright_gate([evidence], require_provider=True)["publish_blocked"] is True


def test_filesystem_guard_rejects_traversal(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(Exception):
        safe_join(root, "../escape")
    with pytest.raises(Exception):
        safe_join(root, "/absolute/path")


def test_factuality_numeric_claim_requires_human_review():
    report = metadata_fact_gate(
        "Minecraft hit 100 million views",
        "The clip reached 100 million views.",
        evidence=[{"text": "100 million views"}],
        fact_reviewed=False,
    )
    assert report["publish_blocked"] is True
    assert report["fact_review_required"] is True
    reviewed = metadata_fact_gate(
        "Minecraft hit 100 million views",
        "The clip reached 100 million views.",
        evidence=[{"text": "100 million views"}],
        fact_reviewed=True,
    )
    assert reviewed["publish_blocked"] is False


def test_security_audit_blocks_shell_apis(tmp_path: Path):
    source = tmp_path / "unsafe.py"
    source.write_text(
        "import os\nos.system('echo unsafe')\n",
        encoding="utf-8",
    )
    findings = audit_source(source)
    assert any(item.rule == "shell-execution" for item in findings)
    assert high_severity(findings)


def test_security_audit_blocks_dynamic_subprocess_shell(tmp_path: Path):
    source = tmp_path / "dynamic.py"
    source.write_text(
        "import subprocess\nflag = True\nsubprocess.run(['echo', 'x'], shell=flag)\n",
        encoding="utf-8",
    )
    findings = audit_source(source)
    assert any(item.rule == "dynamic-subprocess-shell" for item in findings)


def test_audio_measurement_contract_rejects_missing_or_nonfinite_values():
    from ai_video_factory.audio_normalization import AudioNormalizationError, _numeric_measurement
    with pytest.raises(AudioNormalizationError):
        _numeric_measurement({}, "input_i")
    with pytest.raises(AudioNormalizationError):
        _numeric_measurement({"input_i": "nan"}, "input_i")
