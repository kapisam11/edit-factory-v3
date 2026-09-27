from ai_video_factory.upload_package import (
    build_description,
    generate_platform_tags,
    media_rights_report,
    rank_title_candidates,
)


def test_titles_are_specific_and_not_generic():
    ranked = rank_title_candidates(
        "Wemmbu Unstable Universe",
        hook="The final betrayal",
        strongest_angle="The moment everything changed",
        emotion="dramatic",
    )
    assert ranked
    top = ranked[0]["title"].lower()
    assert "unstable universe" in top
    assert top not in {"the real reason", "the hidden legend", "lost forever", "the real story"}
    assert len(top) <= 100


def test_tags_focus_on_topic_and_remove_generic_filler():
    tags = generate_platform_tags(
        "Wemmbu Unstable Universe: The Final Betrayal",
        "A dramatic look at the moment everything changed.",
        "Wemmbu Unstable Universe",
        source_records=[{"creator": "Wemmbu", "title": "Unstable Universe"}],
    )
    assert tags
    assert all(not tag.startswith("#") for tag in tags)
    assert all(tag.lower() not in {"viral", "trending", "youtube", "video"} for tag in tags)
    assert any("wemmbu" in tag.lower() for tag in tags)


def test_description_includes_source_credit():
    summary = {
        "hook": "The final betrayal",
        "strongest_angle": "The moment everything changed",
        "why_care": "The choice changed the whole story.",
        "payoff": "The fallout explains what happened next.",
        "source_metadata": {
            "creator": "Wemmbu",
            "title": "Unstable Universe",
            "url": "https://www.youtube.com/watch?v=example",
            "rights_basis": "explicit_permission",
            "source": "user_provided",
        },
    }
    description = build_description("Wemmbu Unstable Universe", summary, "", "youtube_shorts")
    assert "Wemmbu" in description
    assert "Unstable Universe" in description
    assert "https://www.youtube.com/watch?v=example" in description
    assert "Credits / source information" in description


def test_unverified_source_blocks_publish():
    summary = {
        "source_metadata": {
            "creator": "Wemmbu",
            "title": "Unstable Universe",
            "url": "https://www.youtube.com/watch?v=example",
            "rights_basis": "",
            "source": "user_provided",
        }
    }
    report = media_rights_report(summary)
    assert report["publish_blocked"] is True
    assert report["status"] == "review_required"


def test_explicit_permission_clears_publish_gate():
    summary = {
        "source_metadata": {
            "creator": "Wemmbu",
            "title": "Unstable Universe",
            "url": "https://www.youtube.com/watch?v=example",
            "rights_basis": "explicit_permission",
            "source": "user_provided",
        }
    }
    report = media_rights_report(summary)
    assert report["publish_blocked"] is False
    assert report["status"] == "cleared"
