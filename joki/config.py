import os, json
from pathlib import Path
from joki.state import *
# ============================================================
# CONFIG
# ============================================================
def _get_data_dir():
    """Return stable data directory: ~/.local/share/joki/"""
    return os.path.join(os.path.expanduser("~"), ".local", "share", "joki")

def _get_config_path():
    """Return config path: same directory as joki.py"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_CONFIG_PATH = Path(_get_config_path())

_DEFAULT_MODELS = {
    "gemma4": {
        "name": "Gemma 4 (31B lokal)",
        "base_url": "http://localhost:11434",
        "model": "gemma4:31b",
        "api_keys": [""],
        "provider": "ollama",
        "fallback": "",
    },
    "deepseek": {
        "name": "DeepSeek V4 Flash",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key": "",
        "provider": "openai",
    },
}

def _load_models():
    """Load model configs from config.json, fallback to _DEFAULT_MODELS.

    Normalizes each model so it always has an `api_keys` list
    (migrates legacy `api_key` string into the list).
    Auto-create config.json with template if not exists.
    """
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            models = data.get("models", {})
            if models:
                for k, m in models.items():
                    if "api_keys" not in m or not isinstance(m["api_keys"], list):
                        old = m.pop("api_key", "")
                        m["api_keys"] = [old] if old else []
                    
                    if not m.get("api_keys") or not m["api_keys"][0]:
                        env_key = f"JOKI_{k.upper()}_KEY"
                        if "openrouter" in m.get("base_url", "").lower():
                            env_key = "JOKI_OPENROUTER_KEY"
                        val = os.environ.get(env_key, "")
                        if val:
                            m["api_keys"] = [val]
                return models
        except Exception:
            pass
    raw = dict(_DEFAULT_MODELS)
    for k, m in raw.items():
        if "api_keys" not in m or not isinstance(m["api_keys"], list):
            old = m.pop("api_key", "")
            m["api_keys"] = [old] if old else []
            
        if not m.get("api_keys") or not m["api_keys"][0]:
            env_key = f"JOKI_{k.upper()}_KEY"
            if "openrouter" in m.get("base_url", "").lower():
                env_key = "JOKI_OPENROUTER_KEY"
            val = os.environ.get(env_key, "")
            if val:
                m["api_keys"] = [val]
                
    _auto_create_config()
    return raw

def _auto_create_config():
    """Create ~/.config/joki/config.json with template if it doesn't exist."""
    try:
        template = {
            "models": {
                "gemma4": {
                    "name": "Gemma 4 (31B Cloud)",
                    "base_url": "https://ollama.com/v1",
                    "model": "gemma4:31b-cloud",
                    "api_keys": [""],
                    "provider": "openai",
                    "fallback": "gemini",
                    "default": True
                },
                "gemini": {
                    "name": "Gemini 3 Flash Preview (OpenRouter)",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "google/gemini-3-flash-preview",
                    "api_keys": [""],
                    "provider": "openai",
                    "default": False
                }
            }
        }
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(template, indent=2))
    except Exception:
        pass

_MODELS = _load_models()

default_model = next((v for v in _MODELS.values() if v.get("default")), next(iter(_MODELS.values())))
_current_model_config = dict(default_model)

# ============================================================
# TOOL DEFINITIONS
# ============================================================
