import os
import shutil
import tempfile
import time

from joki.project_index import _build_index_text, _scan_project, _scan_symbols, get_project_index


def _make_project(tmp):
    os.makedirs(os.path.join(tmp, "src"))
    with open(os.path.join(tmp, "main.py"), "w") as f:
        f.write("import os\n\ndef main():\n    pass\n\nclass App:\n    pass\n")
    with open(os.path.join(tmp, "src", "util.py"), "w") as f:
        f.write("def helper():\n    return 1\n")
    with open(os.path.join(tmp, "notes.txt"), "w") as f:
        f.write("plain text, bukan kode\n")


def test_scan_project_discovers_files_and_symbols():
    tmp = tempfile.mkdtemp(prefix="joki_test_idx_")
    try:
        _make_project(tmp)
        idx = _scan_project(tmp)
        assert "main.py" in idx["files"]
        assert "src/util.py" in idx["files"]
        assert "notes.txt" in idx["files"]
        syms = idx["files"]["main.py"].get("symbols", [])
        names = [s[0] for s in syms]
        assert "main" in names
        assert "App" in names
        assert "os" not in names  # import bukan simbol def/class
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_project_excludes_venv_and_git():
    tmp = tempfile.mkdtemp(prefix="joki_test_idx_")
    try:
        os.makedirs(os.path.join(tmp, "venv", "lib"))
        os.makedirs(os.path.join(tmp, ".git"))
        with open(os.path.join(tmp, "app.py"), "w") as f:
            f.write("def x():\n    pass\n")
        with open(os.path.join(tmp, "venv", "lib", "junk.py"), "w") as f:
            f.write("def junk():\n    pass\n")
        idx = _scan_project(tmp)
        assert "app.py" in idx["files"]
        assert "venv/lib/junk.py" not in idx["files"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_index_refreshes_external_edit():
    """Edit/hapus di luar Joki harus ketahuan saat load (validasi mtime/size),
    bukan menyajikan cache basi."""
    tmp = tempfile.mkdtemp(prefix="joki_test_idx_sync_")
    try:
        _make_project(tmp)
        idx1 = get_project_index(tmp)
        names1 = [s[0] for s in idx1["files"]["src/util.py"].get("symbols", [])]
        assert "helper" in names1
        # Edit eksternal: tambah fungsi + paksa mtime beda.
        util = os.path.join(tmp, "src", "util.py")
        with open(util, "a") as f:
            f.write("\ndef baru_luar():\n    return 2\n")
        st = os.stat(util)
        os.utime(util, (st.st_atime + 5, st.st_mtime + 5))
        idx2 = get_project_index(tmp)
        names2 = [s[0] for s in idx2["files"]["src/util.py"].get("symbols", [])]
        assert "baru_luar" in names2
        # Hapus eksternal: entri ikut hilang.
        os.remove(os.path.join(tmp, "notes.txt"))
        idx3 = get_project_index(tmp)
        assert "notes.txt" not in idx3["files"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_index_text_reports_hidden_files():
    tmp = tempfile.mkdtemp(prefix="joki_test_idx_txt_")
    try:
        for i in range(8):
            with open(os.path.join(tmp, f"m{i}.py"), "w") as f:
                f.write("def f():\n    pass\n")
        idx = _scan_project(tmp)
        text = _build_index_text(idx, max_files=3, max_symbol_files=2)
        assert "disembunyikan" in text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_index_text_compact():
    tmp = tempfile.mkdtemp(prefix="joki_test_idx_")
    try:
        _make_project(tmp)
        idx = _scan_project(tmp)
        text = _build_index_text(idx)
        assert "[INDEX PROYEK]" in text
        assert "[INDEX BERAKHIR]" in text
        assert "main.py" in text
        assert "util.py" in text
        assert "main" in text  # simbol ikut muncul
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_index_text_empty_index():
    assert _build_index_text({"root": "/tmp", "files": {}}) == ""


def test_scan_symbols_detects_async_def():
    tmp = tempfile.mkdtemp(prefix="joki_test_async_")
    try:
        path = os.path.join(tmp, "async_mod.py")
        with open(path, "w") as f:
            f.write(
                "import asyncio\n\n"
                "async def fetch_data(url):\n"
                "    return await asyncio.sleep(0)\n\n"
                "class Service:\n"
                "    async def run(self, x, y):\n"
                "        return x + y\n"
            )
        syms = _scan_symbols(path)
        names = [s[0] for s in syms]
        assert "fetch_data" in names
        assert "run" in names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_symbols_detects_async_def_multiline_signature():
    tmp = tempfile.mkdtemp(prefix="joki_test_async_")
    try:
        path = os.path.join(tmp, "async_mod2.py")
        with open(path, "w") as f:
            f.write(
                "async def process_items(\n"
                "    self,\n"
                "    items: list[str],\n"
                "    chunk_size: int = 10,\n"
                ") -> None:\n"
                "    pass\n"
            )
        syms = _scan_symbols(path)
        names = [s[0] for s in syms]
        assert "process_items" in names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_symbols_ast_nested_and_decorated():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    try:
        path = os.path.join(tmp, "nested.py")
        with open(path, "w") as f:
            f.write(
                "@app.get(\"/x\")\n"
                "@cache\n"
                "async def handler(\n"
                "    request,\n"
                "    limit: int = 10,\n"
                "):\n"
                "    def inner_helper():\n"
                "        return 1\n"
                "    class _Inner:\n"
                "        pass\n"
                "    return inner_helper()\n"
                "\n"
                "class Outer:\n"
                "    def method(self):\n"
                "        async def nested_async(self):\n"
                "            pass\n"
            )
        syms = _scan_symbols(path)
        names = [s[0] for s in syms]
        for expected in ("handler", "inner_helper", "_Inner", "Outer", "method", "nested_async"):
            assert expected in names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_symbols_ast_ignores_defs_in_strings():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    try:
        path = os.path.join(tmp, "strings.py")
        with open(path, "w") as f:
            f.write(
                "DOC = \"\"\"\n"
                "def fake_one():\n"
                "    pass\n"
                "\"\"\"\n\n"
                "def real_one():\n"
                "    return 1\n"
            )
        syms = _scan_symbols(path)
        names = [s[0] for s in syms]
        assert "real_one" in names
        assert "fake_one" not in names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_symbols_py_syntax_error_falls_back_to_regex():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    try:
        path = os.path.join(tmp, "broken.py")
        with open(path, "w") as f:
            f.write(
                "def ok_one():\n"
                "    return 1\n"
                "def broken_one(:\n"
                "    pass\n"
                "def ok_two():\n"
                "    return 2\n"
            )
        syms = _scan_symbols(path)
        assert isinstance(syms, list)
        names = [s[0] for s in syms]
        assert "ok_one" in names
        assert "ok_two" in names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
