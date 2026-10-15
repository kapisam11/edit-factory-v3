import pytest

from app.job_service import build_job_params

def _settings():
    return {
        'default_workflow': 'v3',
        'default_target_seconds': 30,
        'default_v3_platform': 'youtube_shorts',
        'default_v3_audience': 'general short-form viewers',
        'default_v3_bpm': 120,
    }

def test_job_service_builds_valid_v3_parameters():
    params = build_job_params(
        {'topic':'A subject','workflow':'v3','platform':'tiktok','target_seconds':'30','audience':'gamers','bpm':'140','edit_type':'Funny'},
        _settings(),
        allow_skip_qc=False,
    )
    assert params['workflow'] == 'v3'
    assert params['platform'] == 'tiktok'
    assert params['bpm'] == 140
    assert params['edit_type'] == 'Funny'

def test_job_service_rejects_invalid_v3_configuration():
    with pytest.raises(ValueError, match='unsupported platform|Unsupported V3 platform'):
        build_job_params(
            {'topic':'A subject','workflow':'v3','platform':'unknown'},
            _settings(),
            allow_skip_qc=False,
        )
    with pytest.raises(ValueError, match='Unsupported V3 edit type'):
        build_job_params(
            {'topic':'A subject','workflow':'v3','platform':'youtube_shorts','edit_type':'unknown'},
            _settings(),
            allow_skip_qc=False,
        )

def test_job_service_accepts_explicit_gaming_profile_and_persists_rights_fields():
    params = build_job_params(
        {
            "topic": "A 1v3 retake on Ascent",
            "workflow": "v3",
            "platform": "youtube_shorts",
            "target_seconds": "30",
            "audience": "FPS gamers",
            "content_profile": "gaming",
            "game_name": "Valorant",
            "gaming_angle": "strategy",
            "asset_origin": "original",
            "rights_basis": "owned",
            "commercial_use": "allowed",
            "permitted_uses": "Monetized gameplay",
            "rights_declared_by": "local-reviewer",
            "attribution_required": "false",
        },
        _settings(),
        allow_skip_qc=False,
    )
    assert params["content_profile"] == "gaming"
    assert params["game_name"] == "Valorant"
    assert params["gaming_angle"] == "strategy"
    assert params["source_metadata"]["asset_origin"] == "original"
    assert params["source_metadata"]["commercial_use"] == "allowed"
    assert params["source_metadata"]["permitted_uses"] == "Monetized gameplay"
    assert params["source_metadata"]["declared_by"] == "local-reviewer"
    assert params["source_metadata"]["declared_at"].endswith("Z")


def test_job_service_rejects_gaming_profile_without_game_name():
    with pytest.raises(ValueError, match="game name is required"):
        build_job_params(
            {
                "topic": "A last-second escape",
                "workflow": "v3",
                "content_profile": "gaming",
                "gaming_angle": "clutch",
            },
            _settings(),
            allow_skip_qc=False,
        )


def test_job_service_preserves_general_profile_default():
    params = build_job_params(
        {"topic": "A subject", "workflow": "v3"},
        _settings(),
        allow_skip_qc=False,
    )
    assert params["content_profile"] == "general"
