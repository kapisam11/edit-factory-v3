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