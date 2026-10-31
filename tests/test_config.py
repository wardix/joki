import json
import os
from unittest.mock import patch

from joki.config import _load_models


@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {}, clear=True)
def test_load_models_default(mock_config_path):
    # If config file doesn't exist, it should return _DEFAULT_MODELS
    # and auto create config.
    mock_config_path.exists.return_value = False
    
    models = _load_models()
    
    assert "gemma4" in models
    assert models["gemma4"]["api_keys"] == [""]

@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {"JOKI_GEMMA4_KEY": "test-key-123"}, clear=True)
def test_load_models_with_env(mock_config_path):
    mock_config_path.exists.return_value = False
    
    models = _load_models()
    
    assert models["gemma4"]["api_keys"] == ["test-key-123"]

@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {}, clear=True)
def test_load_models_from_file(mock_config_path):
    mock_config_path.exists.return_value = True
    custom_models = {
        "models": {
            "custom_model": {
                "name": "Custom Model",
                "base_url": "http://localhost",
                "model": "custom:latest",
                "api_keys": ["custom-key"],
                "provider": "openai",
                "max_tokens": 8192
            }
        }
    }
    mock_config_path.read_text.return_value = json.dumps(custom_models)
    
    models = _load_models()
    
    assert "custom_model" in models
    assert models["custom_model"]["api_keys"] == ["custom-key"]


# --- Test tambahan (dari eksplorasi test coverage lebih detail) ---
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def cfg_mod():
    import joki.config as c
    importlib.reload(c)
    return c


def test_default_models_present(cfg_mod):
    """_DEFAULT_MODELS harus memuat setidaknya satu model."""
    assert cfg_mod._DEFAULT_MODELS
    assert "gemma4" in cfg_mod._DEFAULT_MODELS


def test_data_dir_is_under_home(cfg_mod):
    """Data dir harus berada di bawah home pengguna."""
    dd = Path(cfg_mod._get_data_dir())
    home = Path.home()
    assert str(dd).startswith(str(home))


def test_config_path_is_path(cfg_mod):
    """_get_config_path harus mengembalikan Path yang valid (bisa relatif absolut)."""
    p = Path(cfg_mod._get_config_path())
    assert p.suffix == ".json"


def test_load_models_returns_dict(cfg_mod):
    """_load_models harus mengembalikan dict mapping model name -> config."""
    models = cfg_mod._MODELS
    assert isinstance(models, dict)
    # Setiap model harus minimal punya 'name' dan 'model'
    for m in models.values():
        assert isinstance(m, dict)
        assert "name" in m
        assert "model" in m


def test_fallback_targets_valid():
    """Setiap 'fallback' di file config.json harus menunjuk model yang ada di
    config dan bukan dirinya sendiri. Mencegah rantai fallback mati/dangling
    (mis. fallback ke model yang sudah dihapus atau kena limit permanen)."""
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parents[1] / "joki" / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json tidak ada")
    models = (json.loads(cfg_path.read_text()) or {}).get("models") or {}
    assert models, "config.json tanpa models"
    for key, m in models.items():
        fb = (m or {}).get("fallback")
        if not fb:
            continue
        assert fb in models, f"model '{key}': fallback '{fb}' tidak ada di config.json"
        assert fb != key, f"model '{key}': fallback menunjuk dirinya sendiri"


def test_judge_model_valid():
    """Kalau config.json menetapkan 'judge_model', entry-nya harus ada di
    models. Kalau tidak, verifier diam-diam memakai model worker (self-judge)
    sehingga klaim palsu lebih mudah lolos."""
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parents[1] / "joki" / "config.json"
    if not cfg_path.exists():
        pytest.skip("config.json tidak ada")
    data = json.loads(cfg_path.read_text()) or {}
    jm = str(data.get("judge_model") or "").strip()
    if not jm:
        pytest.skip("judge_model tidak diset")
    models = data.get("models") or {}
    assert jm in models, f"judge_model '{jm}' tidak ada di config.json (models)"
