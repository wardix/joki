import os

from joki.tools.memory import (
    _is_sensitive_key,
    _load_memory,
    _load_todo,
    _memory_path,
    _todo_path,
    handle_memory_store,
    handle_todo_create,
    handle_todo_done,
)


def test_is_sensitive_key_detects_credentials():
    assert _is_sensitive_key("db_password") is True
    assert _is_sensitive_key("github_token") is True
    assert _is_sensitive_key("API_KEY_main") is True
    assert _is_sensitive_key("auth_secret") is True
    assert _is_sensitive_key("preferred_editor") is False
    assert _is_sensitive_key("project_path") is False


def test_memory_store_password_rejected(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_reject")
    result = handle_memory_store({"key": "db_password", "value": "secret123"})
    assert "Error" in result
    assert "credential" in result or "memory_store" in result
    assert not os.path.exists(_memory_path("test_session_reject"))


def test_memory_store_token_rejected(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_reject2")
    result = handle_memory_store({"key": "github_token", "value": "ghp_xxx"})
    assert "Error" in result
    assert not os.path.exists(_memory_path("test_session_reject2"))


def test_memory_store_safe_key_allowed(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_session_allow")
    path = _memory_path("test_session_allow")
    try:
        result = handle_memory_store({"key": "preferred_editor", "value": "vim"})
        assert "Memory saved" in result
        mem = _load_memory("test_session_allow")
        assert mem.get("preferred_editor") == "vim"
    finally:
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# Regresi: model sering kirim "array" sebagai string (payload nyata
# poolside-laguna: items = "1. ...\n2. ...\n3. ...").
# ============================================================


def test_todo_create_accepts_numbered_string(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_str")
    path = _todo_path()
    try:
        result = handle_todo_create({"items": "1. Buat file\n2. Test fitur\n3. Verifikasi"})
        assert "Error" not in result
        assert "Rencana Pengerjaan dibuat (3 item)" in result
        items = _load_todo()
        assert items == ["Buat file", "Test fitur", "Verifikasi"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_todo_create_accepts_json_and_python_string(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_variants")
    path = _todo_path()
    try:
        result = handle_todo_create({"items": '["Buat file", "Test"]'})
        assert "Error" not in result
        assert _load_todo() == ["Buat file", "Test"]

        result = handle_todo_create({"items": "['Buat file', 'Test']"})
        assert "Error" not in result
        assert _load_todo() == ["Buat file", "Test"]
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_todo_create_rejects_empty(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_empty")
    for kosong in ("", None, {}, []):
        result = handle_todo_create({"items": kosong})
        assert "Error" in result
    assert "Error" in handle_todo_create({})
    assert not os.path.exists(_todo_path())


def test_todo_done_accepts_string_indices(monkeypatch):
    monkeypatch.setattr("joki.state._CURRENT_SESSION", "test_todo_indices")
    path = _todo_path()
    try:
        handle_todo_create({"items": ["Satu", "Dua", "Tiga"]})
        result = handle_todo_done({"indices": "1, 2"})
        assert "Item Rencana Pengerjaan 1 dan 2 selesai" in result
        assert _load_todo() == ["✅ Satu", "✅ Dua", "Tiga"]
    finally:
        if os.path.exists(path):
            os.remove(path)
