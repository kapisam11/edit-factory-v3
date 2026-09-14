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
