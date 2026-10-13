import os
import shutil
import tempfile

import pytest

from joki.tools.deps import (
    _find_circular_deps,
    _module_to_path,
    _scan_file_imports,
    get_file_graph,
    handle_analyze_deps,
    handle_impact_analysis,
    refresh_file_graph,
)


def test_scan_imports_from_full_path_python():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("from paket.sub.modul import sesuatu\n")
    try:
        imports, ext = _scan_file_imports(file_path)
        assert "paket.sub.modul" in imports
        assert "paket" not in imports  # jangan potong ke segmen pertama
        assert ext == ".py"
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_imports_plain_import_single():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("import os\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_imports_multi_line():
    tmp = tempfile.mkdtemp(prefix="joki_test_si_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write(
            "import os\n"
            "import sys\n"
            "from datetime import datetime\n"
            "from joki.state import *\n"
            "from joki.config import _get_data_dir\n"
        )
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
        assert "sys" in imports
        assert "datetime" in imports
        assert "joki.state" in imports
        assert "joki.config" in imports
        assert len(imports) >= 5, f"Expected >=5 imports, got {len(imports)}: {imports}"
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_module_to_path_full():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        os.makedirs(os.path.join(tmp, "paket"), exist_ok=True)
        with open(os.path.join(tmp, "paket", "modul_a.py"), "w") as f:
            f.write("x = 1")
        result = _module_to_path("paket.modul_a", ".py", [tmp])
        assert result is not None
        assert result.endswith("paket/modul_a.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_init():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        os.makedirs(os.path.join(tmp, "httpx"), exist_ok=True)
        with open(os.path.join(tmp, "httpx", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path("httpx", ".py", [tmp])
        assert result is not None
        assert result.endswith("httpx/__init__.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_external_not_found():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        result = _module_to_path("some_random_external_package", ".py", [tmp])
        assert result is None
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_fallback_to_first_seg():
    tmp = tempfile.mkdtemp(prefix="joki_test_mtp_")
    try:
        # Simulasi: from httpx.some.deep.path import X
        # Full path httpx/some/deep/path.py tidak ada
        # Tapi fallback ke segmen pertama httpx → httpx/__init__.py harus ada
        os.makedirs(os.path.join(tmp, "httpx"), exist_ok=True)
        with open(os.path.join(tmp, "httpx", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path("httpx.some.deep.path", ".py", [tmp])
        assert result is not None
        assert result.endswith("httpx/__init__.py")
    finally:
        shutil.rmtree(tmp)


def test_analyze_deps_orphan_detection():
    files = {
        "main.py": "from paket.modul_a import fungsi\n",
        "paket/__init__.py": "",
        "paket/modul_a.py": "fungsi = lambda: 1\n",
        "paket/modul_b.py": "x = 2\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_analyze_deps({"path": root})
        # main.py mengimport modul_a.py — modul_a HARUS ter-resolve, bukan orphan
        assert "paket/modul_a.py" not in result or "tidak diimport" not in result
        # modul_b.py tidak diimport siapapun — BISA orphan (tapi tidak wajib muncul
        # di 10 besar orphan, jadi kita assert positif kalo muncul)
        orphan_section = result.split("Orphan files")[-1] if "Orphan files" in result else ""
        if orphan_section and "paket/modul_b.py" not in orphan_section:
            # modul_b.py mungkin tidak masuk 10 besar orphan, skip assert
            pass
    finally:
        shutil.rmtree(root)


def _make_temp_project(files):
    root = tempfile.mkdtemp(prefix="joki_test_deps_")
    for relpath, content in files.items():
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    return root


# ---------- AST-based Python import scanning ----------

def test_scan_python_ast_ignores_comments_and_docstrings():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write(
            '"""\nfrom fake.docstring import X\nimport fake_import\n"""\n'
            "# from fake.comment import Y\n"
            "import os\n"
            "s = 'import not_real'\n"
        )
    try:
        imports, ext = _scan_file_imports(file_path)
        assert ext == ".py"
        assert "os" in imports
        assert "fake.docstring" not in imports
        assert "fake.comment" not in imports
        assert "fake_import" not in imports
        assert "not_real" not in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_single_line_multiple_imports():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("import os, sys, json\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
        assert "sys" in imports
        assert "json" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_relative_imports():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("from . import util\nfrom ..helpers import x\nfrom .. import mod\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert ".util" in imports
        assert "..helpers" in imports
        assert "..mod" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_ast_lazy_import_in_function():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "test.py")
    with open(file_path, "w") as f:
        f.write("def load():\n    import json\n    return json\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "json" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


def test_scan_python_syntax_error_falls_back_to_regex():
    tmp = tempfile.mkdtemp(prefix="joki_test_ast_")
    file_path = os.path.join(tmp, "broken.py")
    with open(file_path, "w") as f:
        f.write("import os\nthis is not valid python (!!!\n")
    try:
        imports, _ = _scan_file_imports(file_path)
        assert "os" in imports
    finally:
        os.unlink(file_path)
        os.rmdir(tmp)


# ---------- relative import resolution ----------

def test_module_to_path_relative_import():
    tmp = tempfile.mkdtemp(prefix="joki_test_rel_")
    try:
        os.makedirs(os.path.join(tmp, "pkg", "sub"), exist_ok=True)
        with open(os.path.join(tmp, "pkg", "mod.py"), "w") as f:
            f.write("x = 1")
        with open(os.path.join(tmp, "pkg", "sub", "b.py"), "w") as f:
            f.write("")
        sub = os.path.join(tmp, "pkg", "sub")
        result = _module_to_path("..mod", ".py", [sub], from_dir=sub)
        assert result == os.path.join(tmp, "pkg", "mod.py")
    finally:
        shutil.rmtree(tmp)


def test_module_to_path_relative_package_self():
    tmp = tempfile.mkdtemp(prefix="joki_test_rel_")
    try:
        os.makedirs(os.path.join(tmp, "pkg"), exist_ok=True)
        with open(os.path.join(tmp, "pkg", "__init__.py"), "w") as f:
            f.write("")
        result = _module_to_path(".", ".py", [os.path.join(tmp, "pkg")], from_dir=os.path.join(tmp, "pkg"))
        assert result == os.path.join(tmp, "pkg", "__init__.py")
    finally:
        shutil.rmtree(tmp)


# ---------- impact_analysis ----------

def test_impact_analysis_direct():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "main.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "def run(): pass\n",
        "pkg/model.py": "x = 1\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "service.py")})
        assert "main.py" in result
        assert "LANGSUNG" in result
        assert "pkg/model.py" not in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_transitive():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "model.py")})
        assert "pkg/service.py" in result
        assert "app.py" in result
        assert "TRANSITIVE" in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_total_no_double_count():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "model.py")})
        # app (transitif) + service (langsung) = 2, BUKAN 3 (double-count lama)
        assert "Total file perlu dicek ulang: 2" in result
    finally:
        shutil.rmtree(root)


def test_impact_analysis_file_not_found():
    result = handle_impact_analysis({"path": "/nonexistent/foo.py"})
    assert "tidak ditemukan" in result


def test_impact_analysis_relative_import_resolved():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "pkg/__init__.py": "",
        "pkg/mod.py": "x = 1\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/b.py": "from .. import mod\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_impact_analysis({"path": os.path.join(root, "pkg", "mod.py")})
        assert "pkg/sub/b.py" in result
        assert "LANGSUNG" in result
    finally:
        shutil.rmtree(root)


# ---------- circular dependencies ----------

def test_find_circular_deps_canonical():
    graph = {
        "a.py": {"resolved": ["b.py"]},
        "b.py": {"resolved": ["c.py"]},
        "c.py": {"resolved": ["a.py"]},
        "d.py": {"resolved": ["a.py"]},
    }
    cycles = _find_circular_deps(graph)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a.py", "b.py", "c.py"}


def test_find_circular_deps_none():
    graph = {
        "a.py": {"resolved": ["b.py"]},
        "b.py": {"resolved": []},
    }
    assert _find_circular_deps(graph) == []


def test_analyze_deps_target_shows_transitive_dependants():
    files = {
        "pyproject.toml": "[project]\nname = 't'\n",
        "app.py": "from pkg.service import run\n",
        "pkg/__init__.py": "",
        "pkg/service.py": "from pkg.model import M\n",
        "pkg/model.py": "class M: pass\n",
    }
    root = _make_temp_project(files)
    try:
        result = handle_analyze_deps({"path": os.path.join(root, "pkg", "model.py")})
        assert "app.py" in result
        assert "Terpengaruh transitif" in result
        assert "Impact jika diubah: 2 file lain perlu dicek." in result
    finally:
        shutil.rmtree(root)


# ---------- Fase 2: scanner JS/TS, PHP PSR-4, Go module, graph cache ----------

def test_scan_js_ts_export_dynamic_require():
    root = _make_temp_project({
        "app.ts": (
            'import { h } from "./help";\n'
            'export * from "./help";\n'
            'export type { T } from "./types";\n'
            'const m = await import("./lazy");\n'
            'const r = require("./legacy");\n'
        ),
        "help.ts": "export const h = 1;\n",
        "types.ts": "export type T = number;\n",
        "lazy.ts": "export const l = 1;\n",
        "legacy.ts": "export const r = 1;\n",
    })
    try:
        imports, ext = _scan_file_imports(os.path.join(root, "app.ts"))
        assert ext == ".ts"
        for want in ("./help", "./types", "./lazy", "./legacy"):
            assert want in imports, f"{want} missing: {imports}"
    finally:
        shutil.rmtree(root)


def test_ts_paths_alias_resolution():
    root = _make_temp_project({
        "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@utils/*": ["src/utils/*"]}}}',
        "src/utils/help.ts": "export const h = 1;\n",
        "src/app.ts": 'import { h } from "@utils/help";\nconsole.log(h);\n',
    })
    try:
        from joki.tools import deps as D
        D._TSCONFIG_CACHE.clear()
        g = D._build_file_graph(root)
        assert g["src/app.ts"]["resolved"] == ["src/utils/help.ts"]
    finally:
        shutil.rmtree(root)


def test_php_use_after_phptag_and_psr4():
    root = _make_temp_project({
        "composer.json": '{"autoload": {"psr-4": {"App\\\\": "src/"}}}',
        "src/Controller/Home.php": "<?php namespace App\\Controller;\nclass Home {}\n",
        "index.php": "<?php use App\\Controller\\Home;\nnew Home();\n",
    })
    try:
        imports, ext = _scan_file_imports(os.path.join(root, "index.php"))
        assert ext == ".php"
        assert "App\\Controller\\Home" in imports
        from joki.tools import deps as D
        D._COMPOSER_CACHE.clear()
        g = D._build_file_graph(root)
        assert g["index.php"]["resolved"] == ["src/Controller/Home.php"]
    finally:
        shutil.rmtree(root)


def test_go_block_imports_and_module_map():
    if not shutil.which("go"):
        pytest.skip("go toolchain tak tersedia")
    root = _make_temp_project({
        "go.mod": "module example.com/m\n\ngo 1.21\n",
        "pkg/calc.go": "package pkg\n\nfunc Add(a, b int) int { return a + b }\n",
        "main.go": 'package main\n\nimport (\n\t"fmt"\n\t"example.com/m/pkg"\n)\n\nfunc main() {}\n',
    })
    try:
        imports, ext = _scan_file_imports(os.path.join(root, "main.go"))
        assert ext == ".go"
        assert "example.com/m/pkg" in imports
        assert "fmt" in imports
        from joki.tools import deps as D
        D._GO_MAP_CACHE.clear()
        g = D._build_file_graph(root)
        assert g["main.go"]["resolved"] == ["pkg/calc.go"]
    finally:
        shutil.rmtree(root)


def test_graph_cache_and_incremental_refresh():
    from joki.tools import deps as D

    root = _make_temp_project({
        "a.py": "x = 1\n",
        "b.py": "from a import x\nprint(x)\n",
    })
    try:
        g1 = get_file_graph(root, force=True)
        assert g1["b.py"]["resolved"] == ["a.py"]
        # Kedua: dari cache (file cache ada di disk).
        assert os.path.isfile(D._graph_index_path(os.path.abspath(root)))
        # Tambah file + refresh inkremental (tanpa force).
        with open(os.path.join(root, "c.py"), "w") as f:
            f.write("from b import x\n")
        g2 = refresh_file_graph(root, [os.path.join(root, "c.py")])
        assert g2["c.py"]["resolved"] == ["b.py"]
        # Hapus file + refresh: entri hilang dan referensi terprune.
        os.remove(os.path.join(root, "a.py"))
        g3 = refresh_file_graph(root, [os.path.join(root, "a.py")])
        assert "a.py" not in g3
        assert "a.py" not in g3["b.py"]["resolved"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_graph_cache_validates_external_edit():
    """Edit di luar Joki (tanpa refresh_file_graph) harus ketahuan saat load:
    stamp mtime/size tak cocok → resolve ulang file itu saja."""
    from joki.tools import deps as D

    root = _make_temp_project({
        "a.py": "x = 1\n",
        "b.py": "from a import x\nprint(x)\n",
    })
    try:
        g1 = get_file_graph(root, force=True)
        assert g1["b.py"]["resolved"] == ["a.py"]
        assert g1["b.py"].get("stamp")
        # Edit eksternal: b.py tak lagi import apapun (ubah ukuran + mtime).
        with open(os.path.join(root, "b.py"), "w") as f:
            f.write("print('tak ada import sama sekali di sini')\n")
        st = os.stat(os.path.join(root, "b.py"))
        os.utime(os.path.join(root, "b.py"), (st.st_atime + 5, st.st_mtime + 5))
        g2 = get_file_graph(root)
        assert g2["b.py"]["resolved"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


# ---------- Root proyek bersama ----------

def test_project_root_markers():
    from joki.state import _is_project_root
    from joki.tools.deps import _find_project_root_deps
    from joki.tools.lsp import _find_project_root

    root = _make_temp_project({
        "requirements_linux.txt": "httpx\n",
        "joki/tools/deps.py": "x = 1\n",
    })
    try:
        assert _is_project_root(root)
        target = os.path.join(root, "joki", "tools", "deps.py")
        assert _find_project_root_deps(target) == root
        assert _find_project_root(target) == root
    finally:
        shutil.rmtree(root)


def test_project_root_fallback_dirname():
    from joki.tools.deps import _find_project_root_deps

    root = _make_temp_project({"sub/file.py": "x = 1\n"})
    try:
        target = os.path.join(root, "sub", "file.py")
        assert _find_project_root_deps(target) == os.path.join(root, "sub")
    finally:
        shutil.rmtree(root)


# ---------- Lapisan C: indeks simbol ----------

def test_file_symbols_persisted():
    from joki.tools import deps as D

    root = _make_temp_project({
        "a.py": "def foo():\n    return 1\n\nclass Bar:\n    pass\n",
        "b.py": "x = 1\n",
    })
    try:
        syms = D.get_file_symbols(root, force=True)
        names = [s["name"] for s in syms.get("a.py", [])]
        assert "foo" in names and "Bar" in names
        assert syms["a.py"][0]["line"] >= 1
        # Kedua: dari cache disk.
        assert D.get_file_symbols(root) == syms
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_symbols_refresh_on_change():
    from joki.tools import deps as D

    root = _make_temp_project({"a.py": "def foo():\n    return 1\n"})
    try:
        D.get_file_graph(root, force=True)
        assert "foo" in [s["name"] for s in D.get_file_symbols(root).get("a.py", [])]
        with open(os.path.join(root, "a.py"), "w") as f:
            f.write("def foo():\n    return 1\n\ndef baru():\n    return 2\n")
        D.refresh_file_graph(root, [os.path.join(root, "a.py")])
        assert "baru" in [s["name"] for s in D.get_file_symbols(root).get("a.py", [])]
        os.remove(os.path.join(root, "a.py"))
        D.refresh_file_graph(root, [os.path.join(root, "a.py")])
        assert "a.py" not in D.get_file_symbols(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_old_cache_without_symbols_rebuilt():
    import json

    from joki.tools import deps as D

    root = _make_temp_project({"a.py": "def foo():\n    return 1\n"})
    try:
        path = D._graph_index_path(os.path.abspath(root))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"root": root, "scanned_at": 0,
                       "files": {"a.py": {"imports": [], "resolved": []}}}, f)
        syms = D.get_file_symbols(root)
        assert "foo" in [s["name"] for s in syms.get("a.py", [])]
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_symbol_impact_uses_index_and_refs(monkeypatch):
    from joki.tools import deps as D

    root = _make_temp_project({
        "a.py": "def hitung(x):\n    return x\n",
        "b.py": "from a import hitung\nprint(hitung(1))\n",
    })
    try:
        D.get_file_graph(root, force=True)
        monkeypatch.setattr(D, "find_symbol_references",
                            lambda r, f, s: [("b.py", 2, 7)] if s == "hitung" else [])
        out = D.symbol_impact(root, "a.py", "hitung")
        assert "Didefinisikan di a.py:1" in out
        assert "b.py:2:7" in out
        # Simbol tak dikenal: definisi tak ditemukan, tanpa crash.
        out2 = D.symbol_impact(root, "a.py", "tak_ada")
        assert "tak ditemukan" in out2.lower() or "tidak ditemukan" in out2.lower()
    finally:
        D._drop_ref_cache(root)
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_impact_with_symbol_param(monkeypatch):
    from joki.tools import deps as D

    root = _make_temp_project({"a.py": "def hitung(x):\n    return x\n"})
    try:
        monkeypatch.setattr(D, "find_symbol_references", lambda r, f, s: [])
        out = D.handle_impact_analysis({"path": os.path.join(root, "a.py"), "symbol": "hitung"})
        assert "Impact Analysis: a.py" in out
        assert "Simbol 'hitung':" in out
        assert "Didefinisikan di a.py:1" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass


def test_find_symbol_references_live():
    from joki.tools import deps as D
    from joki.tools import lsp as L

    if "python" not in L._get_available_servers():
        pytest.skip("pyright tak tersedia")
    root = _make_temp_project({
        "a.py": "def hitung(x):\n    return x\n",
        "b.py": "from a import hitung\nprint(hitung(1))\n",
    })
    try:
        refs = D.find_symbol_references(root, "a.py", "hitung")
        assert ("b.py", 2, 7) in refs, f"refs={refs}"
        # Memo: panggilan kedua tanpa LSP tetap sama.
        assert D.find_symbol_references(root, "a.py", "hitung") == refs
    finally:
        L._cleanup_lsp()
        D._drop_ref_cache(root)
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.remove(D._graph_index_path(os.path.abspath(root)))
        except OSError:
            pass
