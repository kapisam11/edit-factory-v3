# Editorial review and caption correction

The ai_video_factory.editorial_review module provides backend primitives for an operator to inspect candidate clips and timed captions before rendering.

- Candidate IDs and source bounds, caption timing, cue overlap, and caption length are validated.
- Caption edits use a revision token, so stale editors cannot overwrite newer changes.
- Review manifests start as needs_review. Rendering callers can enforce require_approved_review against the exact revision before rendering.
- save_review_atomic persists a manifest through a same-directory temporary file and atomic replacement.
- Candidate evidence and metadata are retained in the review manifest.

Example:

    from ai_video_factory.editorial_review import (
        build_editorial_review, approve_editorial_review, require_approved_review,
    )

    review = build_editorial_review(
        job_id="job-123",
        candidates=[{"id": "clip-a", "start": 12.0, "end": 35.0,
                     "score": 0.87, "evidence": ["transcript_match", "scene_change"]}],
        captions=[{"id": "cue-1", "text": "The key moment", "start": 12.2, "end": 13.1}],
        media_duration=120.0,
    )
    # Present candidates and captions to the operator before this step.
    approved = approve_editorial_review(review, expected_revision=review["revision"])
    require_approved_review(approved, revision=approved["revision"])

Integration boundary: these are backend primitives; this commit does not claim every existing dashboard render path is already gated by this review manifest. Callers must enforce require_approved_review before invoking a renderer. Live Windows rendering and platform upload acceptance remain environment checks.
