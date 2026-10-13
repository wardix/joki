import os
import shutil
import tempfile
import time

from joki.state import _WORKSPACE_ROOT, BACKUP_DIR
from joki.tools.files import (
    _BACKUP_MANIFEST,
    _save_manifest,
    handle_config_edit,
    handle_edit_file,
    handle_read_file,
    handle_search_code,
    handle_write_file,
)


def test_search_code_head_tail():
    tmp = tempfile.mkdtemp(prefix="joki_test_search_ht_")
    try:
        path = os.path.join(tmp, "big.py")
        with open(path, "w") as f:
            for i in range(250):
                f.write(f"MARK baris {i:03d}\n")
        out = handle_search_code({"path": tmp, "pattern": "MARK"})
        assert "MARK baris 000" in out  # head ada
        assert "MARK baris 249" in out  # tail ada
        assert "dipotong" in out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _temp_path(name="test.txt"):
    tmp = tempfile.mkdtemp(prefix="joki_test_files_")
    return os.path.join(tmp, name), tmp


def _project_temp_path(name="test.txt"):
    tmp = tempfile.mkdtemp(prefix="test_temp_", dir=_WORKSPACE_ROOT)
    return os.path.join(tmp, name), tmp


def test_write_read_roundtrip():
    path, tmpdir = _project_temp_path()
    try:
        result = handle_write_file({"path": path, "content": "hello world\nline2\n"})
        assert "Written" in result

        content = handle_read_file({"path": path})
        assert "hello world" in content
        assert "line2" in content
    finally:
        shutil.rmtree(tmpdir)


def test_edit_file():
    path, tmpdir = _project_temp_path()
    try:
        handle_write_file({"path": path, "content": "foo\nbar\nbaz\n"})
        handle_read_file({"path": path})
        result = handle_edit_file({"path": path, "old_text": "bar", "new_text": "REPLACED"})
        assert "Edited" in result

        content = handle_read_file({"path": path})
        assert "foo" in content
        assert "REPLACED" in content
        assert "baz" in content
    finally:
        shutil.rmtree(tmpdir)


def test_edit_file_fuzzy_whitespace():
    path, tmpdir = _project_temp_path()
    try:
        handle_write_file({"path": path, "content": "def foo():\n    return 1\n"})
        handle_read_file({"path": path})
        result = handle_edit_file({
            "path": path,
            "old_text": "    return 1",
            "new_text": "    return 42"
        })
        assert "Edited" in result
        content = handle_read_file({"path": path})
        assert "return 42" in content
    finally:
        shutil.rmtree(tmpdir)


def test_read_file_not_found():
    result = handle_read_file({"path": "/nonexistent/path/file.txt"})
    assert "ERROR" in result or "tidak ditemukan" in result or "not found" in result.lower()


def test_write_file_new_directory_created():
    tmpdir = tempfile.mkdtemp(prefix="joki_test_files_")
    try:
        nested = os.path.join(tmpdir, "a", "b", "c", "test.txt")
        result = handle_write_file({"path": nested, "content": "nested"})
        assert "Written" in result
        assert os.path.exists(nested)
    finally:
        shutil.rmtree(tmpdir)


def test_edit_without_read_rejected():
    path, tmpdir = _project_temp_path()
    try:
        handle_write_file({"path": path, "content": "some content\n"})
        result = handle_edit_file({"path": path, "old_text": "some", "new_text": "other"})
        assert "WAJIB baca" in result
    finally:
        shutil.rmtree(tmpdir)


def test_write_file_protected_system_path_rejected():
    target = "/etc/test_should_fail.conf"
    try:
        if os.path.exists(target):
            os.remove(target)
        result = handle_write_file({"path": target, "content": "x"})
        assert "dilindungi" in result
        assert not os.path.exists(target)
    finally:
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass


def test_edit_file_protected_system_path_rejected():
    target = "/etc/hostname"
    result = handle_read_file({"path": target})
    assert "Error" not in result
    result = handle_edit_file({"path": target, "old_text": "x", "new_text": "y"})
    assert "dilindungi" in result


def test_write_edit_file_project_path_allowed():
    tmpdir = tempfile.mkdtemp(prefix="joki_proj_")
    try:
        path = os.path.join(tmpdir, "app.py")
        result = handle_write_file({"path": path, "content": "print('hi')\n"})
        assert "Written" in result
        handle_read_file({"path": path})
        result = handle_edit_file({"path": path, "old_text": "print('hi')", "new_text": "print('bye')"})
        assert "Edited" in result
        content = handle_read_file({"path": path})
        assert "print('bye')" in content
    finally:
        shutil.rmtree(tmpdir)


def test_config_edit_non_identity_path_allowed():
    tmpdir = tempfile.mkdtemp(prefix="joki_cfg_")
    try:
        path = os.path.join(tmpdir, "nginx_sites_available", "default")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("server {\n  listen 80;\n}\n")
        result = handle_config_edit({"path": path, "directive": "listen", "set_value": "8080"})
        assert "Edited" in result
        with open(path) as f:
            content = f.read()
        assert "8080" in content
    finally:
        shutil.rmtree(tmpdir)


def test_config_edit_identity_path_rejected(monkeypatch):
    monkeypatch.setattr("joki.tools.files.os.path.exists", lambda p: True)
    result = handle_config_edit({"path": "/etc/shadow", "directive": "x"})
    assert "identitas" in result


def test_read_file_system_path_allowed():
    result = handle_read_file({"path": "/etc/hostname"})
    assert "Error" not in result


def test_backup_cleanup_removes_old_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    old = os.path.join(BACKUP_DIR, "old_placeholder.bak")
    manifest_key = os.path.basename(old)
    with open(old, "w") as f:
        f.write("old")
    old_time = time.time() - 8 * 86400
    os.utime(old, (old_time, old_time))
    _BACKUP_MANIFEST[manifest_key] = "/some/old/path"
    try:
        tmpdir = tempfile.mkdtemp(prefix="joki_ret_")
        path = os.path.join(tmpdir, "file.txt")
        with open(path, "w") as f:
            f.write("content\n")
        handle_read_file({"path": path})
        handle_edit_file({"path": path, "old_text": "content", "new_text": "updated"})
        assert not os.path.exists(old)
        assert manifest_key not in _BACKUP_MANIFEST
        shutil.rmtree(tmpdir)
    finally:
        _BACKUP_MANIFEST.pop(manifest_key, None)
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        _save_manifest()


def test_file_cache_and_change_log_on_write_read():
    import os
    import shutil
    import tempfile

    from joki.state import _file_cache, _file_change_log
    from joki.tools.files import (
        _file_signature,
        handle_read_file,
        handle_write_file,
    )

    _file_cache.clear()
    _file_change_log.clear()
    tmp = tempfile.mkdtemp(prefix="joki_test_fc_")
    try:
        path = os.path.join(tmp, "a.py")
        handle_write_file({"path": path, "content": "x = 1\n"})
        assert path in _file_change_log
        _file_change_log.clear()

        handle_read_file({"path": path})
        assert path in _file_cache
        assert _file_cache[path]["hash"] == _file_signature(path)

        handle_write_file({"path": path, "content": "x = 2\n"})
        assert path not in _file_cache
        assert path in _file_change_log
    finally:
        _file_cache.clear()
        _file_change_log.clear()
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_file_changed_invalidates_read_in_context():
    import os
    import shutil
    import tempfile

    from joki.state import _read_in_context
    from joki.tools.files import _mark_file_changed

    tmp = tempfile.mkdtemp(prefix="joki_test_fc_")
    try:
        path = os.path.join(tmp, "b.py")
        _read_in_context[path] = {"hash": "abc", "idx": 3, "head": "x"}
        _mark_file_changed(path)
        assert path not in _read_in_context
    finally:
        _read_in_context.clear()
        shutil.rmtree(tmp, ignore_errors=True)
