import pytest

from dashboard_worker import _skip_stages_for_workflow


def test_dashboard_workflows_map_to_expected_pipeline_stages():
    assert _skip_stages_for_workflow("default") == []
    assert _skip_stages_for_workflow("director") == []
    assert _skip_stages_for_workflow("fast") == ["research", "thumbnail", "voiceover", "music", "quality_control", "metrics"]
    assert _skip_stages_for_workflow("package_only") == ["auto_edit", "voiceover", "music", "quality_control", "metrics"]


def test_dashboard_workflow_rejects_unknown_value():
    with pytest.raises(ValueError):
        _skip_stages_for_workflow("not-a-workflow")
