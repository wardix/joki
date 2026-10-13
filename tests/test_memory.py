import os

from joki.tools.memory import (
    _is_sensitive_key,
    _load_memory,
    _memory_path,
    handle_memory_store,
)


def test_is_sensitive_key_detects_credentials():
    assert _is_sensitive_key("db_password") is True
    assert _is_sensitive_key("github_token") is True
    assert _is_sensitive_key("API_KEY_main") is True
    assert _is_sensitive_key("auth_secret") is True
    assert _is_sensitive_key("preferred_editor") is False
    assert _is_sensitive_key("project_path") is False


def test_memory_store_password_rejected(monkeypatch):
    monkeypatch.setattr("joki.tools.memory._CURRENT_SESSION", "test_session_reject")
    result = handle_memory_store({"key": "db_password", "value": "secret123"})
    assert "Error" in result
    assert "credential" in result or "memory_store" in result
    assert not os.path.exists(_memory_path("test_session_reject"))


def test_memory_store_token_rejected(monkeypatch):
    monkeypatch.setattr("joki.tools.memory._CURRENT_SESSION", "test_session_reject2")
    result = handle_memory_store({"key": "github_token", "value": "ghp_xxx"})
    assert "Error" in result
    assert not os.path.exists(_memory_path("test_session_reject2"))


def test_memory_store_safe_key_allowed(monkeypatch):
    monkeypatch.setattr("joki.tools.memory._CURRENT_SESSION", "test_session_allow")
    path = _memory_path("test_session_allow")
    try:
        result = handle_memory_store({"key": "preferred_editor", "value": "vim"})
        assert "Memory saved" in result
        mem = _load_memory("test_session_allow")
        assert mem.get("preferred_editor") == "vim"
    finally:
        if os.path.exists(path):
            os.remove(path)
