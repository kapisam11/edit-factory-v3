from ai_video_factory.runtime_capabilities import CapabilityStatus, capabilities, require

def test_capability_status_requires_actionable_optional_dependency():
    status = CapabilityStatus('vision', False, "the 'vision' extra", 'OpenCV frame analysis')
    try:
        status.require()
    except RuntimeError as exc:
        assert "vision" in str(exc)
        assert "optional extra" in str(exc)
    else:
        raise AssertionError('missing capability must fail with an actionable error')

def test_capability_registry_exposes_expected_core_features():
    detected = capabilities()
    assert {'vision','ocr','audio_analysis','embeddings','diarization','groq','ffmpeg','ffprobe'} <= set(detected)
    assert all(isinstance(item.available, bool) for item in detected.values())