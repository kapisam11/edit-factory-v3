import hashlib
import json

import pytest

from ai_video_factory.editorial_review import (
    EditorialReviewError,
    apply_caption_edits,
    approve_editorial_review,
    build_editorial_review,
    require_approved_review,
    save_review_atomic,
    validate_caption_cues,
)


def sample_cues():
    return [
        {"id": "c1", "text": "This is", "start": 0.0, "end": 0.8},
        {"id": "c2", "text": "the important part", "start": 0.8, "end": 1.8},
    ]


def revision(cues):
    encoded = json.dumps(cues, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_caption_edit_uses_revision_and_reports_changed_ids():
    cues = sample_cues()
    result = apply_caption_edits(cues, [{"id": "c2", "text": "the key part"}], expected_revision=revision(cues), media_duration=2)
    assert result["cues"][1]["text"] == "the key part"
    assert result["changed_ids"] == ["c2"]
    with pytest.raises(EditorialReviewError, match="stale"):
        apply_caption_edits(cues, [], expected_revision="old-revision")


@pytest.mark.parametrize("cues", [
    [{"id": "x", "text": "bad", "start": 1, "end": 0.5}],
    [{"id": "x", "text": "one", "start": 0, "end": 1}, {"id": "y", "text": "two", "start": 0.9, "end": 2}],
    [{"id": "x", "text": "", "start": 0, "end": 1}],
    [{"id": "x", "text": "too late", "start": 2, "end": 3}],
])
def test_caption_validation_rejects_invalid_timing_or_content(cues):
    with pytest.raises(EditorialReviewError):
        validate_caption_cues(cues, media_duration=2)


def test_review_requires_approval_of_exact_revision(tmp_path):
    review = build_editorial_review(
        job_id="job-123",
        candidates=[{"id": "clip-a", "start": 1, "end": 8, "score": 0.82, "evidence": ["speech_match"]}],
        captions=sample_cues(),
        media_duration=10,
    )
    with pytest.raises(EditorialReviewError, match="required"):
        require_approved_review(review, revision=review["revision"])
    approved = approve_editorial_review(review, expected_revision=review["revision"])
    require_approved_review(approved, revision=approved["revision"])
    changed = dict(approved)
    changed["captions"] = [{"id": "c1", "text": "silently changed", "start": 0, "end": 0.8}]
    with pytest.raises(EditorialReviewError):
        require_approved_review(changed, revision=changed["revision"])
    save_review_atomic(tmp_path / "review.json", approved)
    assert json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))["status"] == "approved"


def test_review_rejects_bad_source_bounds_and_duplicate_ids():
    with pytest.raises(EditorialReviewError):
        build_editorial_review(job_id="job", candidates=[{"id": "bad", "start": 9, "end": 11}], media_duration=10)
    with pytest.raises(EditorialReviewError):
        build_editorial_review(job_id="job", candidates=[{"id": "x", "start": 0, "end": 1}, {"id": "x", "start": 2, "end": 3}])
