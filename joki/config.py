import json
import os
import sys
from pathlib import Path

from rich.markup import escape

from joki.constants import MAX_TOKENS
from joki.display import _color_warn
from joki.state import *


# ============================================================
# CONFIG
# ============================================================
def _get_data_dir():
    """Return data directory: folder project joki (self-contained).

    Semua data (memories, todos, sessions, checkpoints, skills, plugins, dll.)
    disimpan langsung di folder repo ini. Build standalone (frozen) tetap
    memakai ~/.local/share/joki karena folder aplikasi read-only.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".local", "share", "joki")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_config_path():
    """Return config path: same directory as joki.py, or ~/.config/joki/config.json
    when running as a frozen (PyInstaller) executable."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".config", "joki", "config.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_CONFIG_PATH = Path(_get_config_path())

_DEFAULT_MODELS = {
    "gemma4": {
        "name": "Gemma 4 (31B Cloud)",
        "base_url": "https://ollama.com/v1",
        "model": "gemma4:31b-cloud",
        "api_keys": [""],
        "provider": "openai",
        "max_tokens": 32768,
        "context_window": 256000,
        "default": True,
        "max_iterations": 0,
        "dry_threshold": 12,
    },
}

def _load_models():
    """Load model configs from config.json, fallback to _DEFAULT_MODELS.

    Normalizes each model so it always has an `api_keys` list
    (migrates legacy `api_key` string into the list).
    Validates required fields: name, base_url, model, provider, max_tokens.
    Skips invalid models with a warning.
    Auto-create config.json with template if not exists.
    """
    _REQUIRED_FIELDS = ["name", "base_url", "model", "provider", "max_tokens"]

    def _normalize_model(k, m):
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

        # Ensure optional fields have consistent defaults
        m.setdefault("context_window", MAX_TOKENS)
        m.setdefault("max_iterations", 0)
        m.setdefault("dry_threshold", 12)

        return m

    def _is_valid(k, m):
        missing = [f for f in _REQUIRED_FIELDS if f not in m]
        if missing:
            _console.print(f"[{_color_warn()}]Konfigurasi model '{k}' tidak valid — missing: {', '.join(missing)}. Model ini akan di-skip.[/{_color_warn()}]")
            return False
        if not isinstance(m.get("api_keys", []), list):
            _console.print(f"[{_color_warn()}]Konfigurasi model '{k}' tidak valid — 'api_keys' harus berupa list. Model ini akan di-skip.[/{_color_warn()}]")
            return False
        return True

    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            models = data.get("models", {})
            if models:
                result = {}
                for k, m in models.items():
                    m = _normalize_model(k, m)
                    if _is_valid(k, m):
                        result[k] = m
                    else:
                        m.clear()
                if result:
                    for k, m in result.items():
                        fb = m.get("fallback", "")
                        if fb and fb not in result:
                            _console.print(f"[{_color_warn()}]Model '{k}' fallback ke '{fb}' yang tidak ada di config — fallback diabaikan.[/{_color_warn()}]")
                            m.pop("fallback", None)
                    return result
                _console.print(f"[{_color_warn()}]Semua model di config.json tidak valid — fallback ke default.[/{_color_warn()}]")
            else:
                _console.print(f"[{_color_warn()}]config.json tidak memiliki model — fallback ke default.[/{_color_warn()}]")
        except Exception as e:  # noqa: BLE001
            _console.print(f"[dim]Warning: Gagal memuat config.json: {escape(str(e))}. Fallback ke model default.[/dim]")
    raw = dict(_DEFAULT_MODELS)
    for k, m in raw.items():
        _normalize_model(k, m)
    _auto_create_config()
    return raw

def _auto_create_config():
    """Create ~/.config/joki/config.json with template if it doesn't exist."""
    try:
        template = {
            "theme": "system",
            "models": {
                "gemma4": {
                    "name": "Gemma 4 (31B Cloud)",
                    "base_url": "https://ollama.com/v1",
                    "model": "gemma4:31b-cloud",
                    "api_keys": [""],
                    "provider": "openai",
                    "max_tokens": 32768,
                    "default": True
                }
            }
        }
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(template, indent=2))
    except Exception as e:  # noqa: BLE001
        _console.print(f"[dim]Warning: Gagal membuat config template: {escape(str(e))}[/dim]")

_MODELS = _load_models()

default_model = next((v for v in _MODELS.values() if v.get("default")), next(iter(_MODELS.values())))
_current_model_config.clear()
_current_model_config.update(default_model)


# ============================================================
# LSP CONFIG
# ============================================================
def _get_lsp_config():
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            return data.get("lsp", {})
        except Exception:  # noqa: BLE001, S110
            pass
    return {}


# ============================================================
# TOOL DEFINITIONS
# ============================================================
