"""Regresi dari uji ulang poolside (2026-10-17) — 3 celah harness:

1. glob dari root '/' ditolak & ada batas waktu (dulu hang menjelajah FS).
2. `cd` di shell tidak memindahkan cwd proses joki ke luar workspace.
3. Direktori kerja absolut disebut di system prompt + [INDEX PROYEK].
"""
import os

import joki.cli as cli
from joki.project_index import _build_index_text, _scan_project
from joki.tools import files as files_mod
from joki.tools import shell as shell_mod
from joki.tools.files import handle_glob


# --- 1. guard glob -------------------------------------------------------

def test_glob_root_rejected():
    out = handle_glob({"pattern": "**/*.py", "path": "/"})
    assert out.startswith("Error")
    assert "root" in out.lower()


def test_glob_abs_pattern_root_rejected():
    out = handle_glob({"pattern": "/**/a.py"})
    assert out.startswith("Error")


def test_glob_inside_subdir_still_works(tmp_path):
    (tmp_path / "b.py").write_text("x=1\n")
    out = handle_glob({"pattern": "**/*.py", "path": str(tmp_path)})
    assert "b.py" in out


def test_glob_timeout_guard(monkeypatch, tmp_path):
    import time as _time

    def _slow_glob(*a, **k):
        _time.sleep(5)
        return []

    monkeypatch.setattr(files_mod._glob_mod, "glob", _slow_glob)
    monkeypatch.setattr(files_mod, "_GLOB_TIMEOUT_S", 0.3)
    out = handle_glob({"pattern": "**/*.py", "path": str(tmp_path)})
    assert "timeout" in out.lower()


# --- 2. guard cwd --------------------------------------------------------

def test_sync_cwd_allowed_only_inside_workspace(monkeypatch, tmp_path):
    root = tmp_path / "proyek"
    sub = root / "sub"
    sub.mkdir(parents=True)
    monkeypatch.setattr(shell_mod, "_SHELL_BOUNDARY_ROOT", os.path.realpath(root))
    old = os.getcwd()
    try:
        assert shell_mod._sync_cwd_allowed(str(sub)) is True
        assert os.path.realpath(os.getcwd()) == os.path.realpath(sub)
        assert shell_mod._sync_cwd_allowed("/") is False
        assert shell_mod._sync_cwd_allowed("/tmp") is False
        assert os.path.realpath(os.getcwd()) == os.path.realpath(sub)
        assert shell_mod._sync_cwd_allowed(str(root)) is True
        assert os.path.realpath(os.getcwd()) == os.path.realpath(root)
    finally:
        os.chdir(old)


def test_shell_boundary_lazy_captures_start_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(shell_mod, "_SHELL_BOUNDARY_ROOT", None)
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert shell_mod._get_shell_boundary() == os.path.realpath(tmp_path)
    finally:
        os.chdir(old)


# --- 3. direktori kerja di prompt & index --------------------------------

def test_index_text_includes_workdir(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    pass\n")
    idx = _scan_project(str(tmp_path))
    text = _build_index_text(idx)
    assert "Direktori kerja:" in text
    assert str(tmp_path) in text


def test_system_prompt_mentions_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sp = cli._build_system_prompt()
    assert "DIREKTORI KERJA" in sp
    assert str(tmp_path) in sp
