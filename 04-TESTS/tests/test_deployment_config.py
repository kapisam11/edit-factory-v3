from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_gunicorn_config_enforces_supported_process_model(monkeypatch):
    config_path = ROOT / "06-CONFIG-AND-DEPLOYMENT" / "gunicorn.conf.py"
    monkeypatch.delenv("AIVF_GUNICORN_WORKERS", raising=False)
    monkeypatch.delenv("AIVF_GUNICORN_THREADS", raising=False)
    config = runpy.run_path(str(config_path))
    assert config["workers"] == 1
    assert 2 <= config["threads"] <= 16

    monkeypatch.setenv("AIVF_GUNICORN_WORKERS", "2")
    with pytest.raises(RuntimeError, match="AIVF_GUNICORN_WORKERS"):
        runpy.run_path(str(config_path))
