from ai_video_factory.config import (
    AIVFConfig,
    APIKeys,
    HardwareConfig,
    PipelineConfig,
    StyleProfile,
)


def test_nested_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = AIVFConfig()
    config.api_keys.openai = "secret-that-must-not-persist"
    config.save(str(path))

    loaded = AIVFConfig.load(str(path))

    assert isinstance(loaded.hardware, HardwareConfig)
    assert isinstance(loaded.api_keys, APIKeys)
    assert isinstance(loaded.get_pipeline(), PipelineConfig)
    assert isinstance(loaded.get_style(), StyleProfile)
    assert loaded.api_keys.openai == ""
    assert "secret-that-must-not-persist" not in path.read_text(encoding="utf-8")


def test_unknown_provider_is_rejected():
    config = AIVFConfig()
    try:
        config.set_api_key("unknown", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown providers must be rejected")



def test_config_validation_rejects_unknown_active_pipeline():
    config = AIVFConfig(active_pipeline="missing")
    try:
        config.validate()
    except ValueError as exc:
        assert "active_pipeline" in str(exc)
    else:
        raise AssertionError("unknown active pipeline must be rejected")


def test_config_save_is_not_left_with_partial_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    config = AIVFConfig()
    original_replace = __import__("os").replace
    def fail_replace(*args, **kwargs):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(__import__("os"), "replace", fail_replace)
    try:
        config.save(str(path))
    except OSError:
        pass
    else:
        raise AssertionError("simulated atomic replace failure must propagate")
    partials = list(tmp_path.glob(".aivf-config-*.partial"))
    assert not partials
    assert not path.exists()
    monkeypatch.setattr(__import__("os"), "replace", original_replace)
