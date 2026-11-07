"""Fixture refactor besar: kunci perilaku SOP + edit atomik + rename aman."""

import os
import shutil
import tempfile

import pytest

from joki.cli import _REFACTOR_SOP_TEXT, _is_refactor_request
from joki.constants import CORE_TOOLS, TOOLS, get_tools_for_mode
from joki.executor import TOOL_HANDLERS
from joki.llm import REFACTOR_WORKING_SET, SUMMARY_WORKING_SET
from joki.tools.files import (
    handle_edit_file,
    handle_read_file,
    handle_rename_symbol,
    handle_search_code,
    handle_write_file,
)


def _tmp():
    return tempfile.mkdtemp(prefix="joki_refactor_")


def test_is_refactor_request():
    assert _is_refactor_request("tolong refactor modul auth ini")
    assert _is_refactor_request("rename fungsi foo ke bar")
    assert _is_refactor_request("pindah modul ke package baru")
    assert "[SOP REFACTOR]" in _REFACTOR_SOP_TEXT
    assert "rename_symbol" in _REFACTOR_SOP_TEXT
    assert not _is_refactor_request("halo apa kabar")
    assert not _is_refactor_request("")


def test_rename_symbol_registered():
    names = {t["function"]["name"] for t in TOOLS}
    assert "rename_symbol" in names
    assert "rename_symbol" in CORE_TOOLS
    code_names = {t["function"]["name"] for t in get_tools_for_mode("code")}
    assert "rename_symbol" in code_names
    assert "rename_symbol" in TOOL_HANDLERS


def test_refactor_working_set_larger():
    assert REFACTOR_WORKING_SET > SUMMARY_WORKING_SET


def test_edit_file_syntax_rollback():
    tmp = _tmp()
    try:
        p = os.path.join(tmp, "m.py")
        handle_write_file({"path": p, "content": "def foo():\n    return 1\n"})
        handle_read_file({"path": p})
        bad = handle_edit_file(
            {"path": p, "old_text": "    return 1", "new_text": "    return :::: "}
        )
        assert "DIBATALKAN" in bad or "syntax" in bad.lower()
        with open(p) as f:
            assert f.read() == "def foo():\n    return 1\n"
        good = handle_edit_file(
            {"path": p, "old_text": "    return 1", "new_text": "    return 2"}
        )
        assert "Edited" in good
        assert "SINTAKS OK" in good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rename_symbol_validation():
    tmp = _tmp()
    try:
        p = os.path.join(tmp, "r.py")
        handle_write_file({"path": p, "content": "def foo():\n    return 1\n"})
        assert "wajib diisi" in handle_rename_symbol({"file_path": p}).lower()
        assert "tidak valid" in handle_rename_symbol(
            {"file_path": p, "old_name": "123", "new_name": "bar"}
        )
        assert "sama" in handle_rename_symbol(
            {"file_path": p, "old_name": "foo", "new_name": "foo"}
        ).lower()
        assert "tidak ditemukan" in handle_rename_symbol(
            {"file_path": "/nonexistent/x.py", "old_name": "a", "new_name": "b"}
        ).lower()
        assert "tidak ditemukan" in handle_rename_symbol(
            {"file_path": p, "old_name": "tak_ada_simbol_ini", "new_name": "x"}
        ).lower()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_symbol_prefers_definition():
    from joki.tools.lsp import _find_symbol_in_file

    tmp = _tmp()
    try:
        p = os.path.join(tmp, "m.py")
        with open(p, "w") as f:
            f.write("# panggil foo() di komentar\ndef foo():\n    return foo\n")
        line, char = _find_symbol_in_file(p, "foo")
        assert (line, char) == (1, 4)
        assert _find_symbol_in_file(p, "") == (0, 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_search_code_basic():
    tmp = _tmp()
    try:
        p = os.path.join(tmp, "s.py")
        handle_write_file({"path": p, "content": "def cari_saya():\n    pass\n"})
        found = handle_search_code({"pattern": "cari_saya", "path": tmp})
        assert "cari_saya" in found
        assert "not found" in handle_search_code({"pattern": "zzznomatch", "path": tmp})
        assert "wajib diisi" in handle_search_code({"pattern": "", "path": tmp}).lower()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_truncate_tool_result():
    from joki.cli import TOOL_RESULT_MAX_CHARS, _truncate_tool_result

    assert _truncate_tool_result("pendek") == "pendek"
    assert _truncate_tool_result("") == ""
    assert _truncate_tool_result(None) == ""
    long = "x" * (TOOL_RESULT_MAX_CHARS + 5000)
    out = _truncate_tool_result(long)
    assert len(out) < len(long)
    assert "DIPOTONG" in out
    assert out.startswith("x" * 12000)
    # Kontrak baru head+tail: ekor dipertahankan agar error di ujung terlihat.
    mixed = "H" * 15000 + "T" * 10000
    out2 = _truncate_tool_result(mixed)
    assert out2.startswith("H" * 100)
    assert "T" * 100 in out2


def test_strip_think_markers():
    from joki.llm import _strip_think_markers

    assert _strip_think_markers("halo") == "halo"
    out = _strip_think_markers("nalar dulu.</think>\n\nJawaban.")
    assert "</think>" not in out
    assert "nalar dulu." in out
    assert "Jawaban." in out


def _diag(sev, msg, line=0):
    return {"severity": sev, "message": msg, "range": {"start": {"line": line, "character": 0}}}


def test_classify_lsp_diags():
    from joki.tools.files import _classify_lsp_diags as c

    ts = [_diag(1, "Cannot find module '@x'."), _diag(1, "Cannot find name 'foo'.", line=1)]
    assert c(ts, "typescript") == ["baris 2: Cannot find name 'foo'."]
    cc = [_diag(1, "'missing.h' file not found"), _diag(2, "unused variable", line=1)]
    assert c(cc, "c_cpp") == []
    php = [_diag(1, "Undefined type 'A'."), _diag(1, "Undefined variable '$x'.", line=2)]
    assert c(php, "php") == ["baris 3: Undefined variable '$x'."]
    assert c([], "typescript") == []
    assert c(None, "typescript") == []


def test_diagnostics_wait_edge_cases():
    from joki.tools import lsp as L

    assert L.get_file_diagnostics_wait("/nonexistent/x.ts", timeout=1) is None
    assert L.get_file_diagnostics_wait("/tmp/joki_nope.zzz", timeout=1) is None


def _lsp_lang_available(lang):
    from joki.tools import lsp as L

    return lang in L._get_available_servers()


def test_lsp_verify_c_live():
    import shutil
    import tempfile

    if not _lsp_lang_available("c_cpp"):
        pytest.skip("clangd tak tersedia")
    from joki.tools import lsp as L
    from joki.tools.files import _lsp_verify_edited

    tmp = tempfile.mkdtemp(prefix="joki_lspc_")
    try:
        bad = os.path.join(tmp, "broken.c")
        with open(bad, "w") as f:
            f.write("int main() { return ; }\n")
        out = _lsp_verify_edited(bad, timeout=15)
        assert "[VERIFY lsp:c_cpp]" in out
        assert "error" in out.lower()
        ok = os.path.join(tmp, "ok.c")
        with open(ok, "w") as f:
            f.write('#include "missing_header_xyz.h"\nint main() { return 0; }\n')
        assert _lsp_verify_edited(ok, timeout=15) == "[VERIFY lsp:c_cpp] bersih (0 error)."
    finally:
        L._cleanup_lsp()
        shutil.rmtree(tmp, ignore_errors=True)


def test_lsp_verify_php_live():
    import shutil
    import tempfile

    if not _lsp_lang_available("php"):
        pytest.skip("intelephense tak tersedia")
    from joki.tools import lsp as L
    from joki.tools.files import _lsp_verify_edited

    tmp = tempfile.mkdtemp(prefix="joki_lspphp_")
    try:
        bad = os.path.join(tmp, "broken.php")
        with open(bad, "w") as f:
            f.write("<?php echo ;\n")
        out = _lsp_verify_edited(bad, timeout=15)
        assert "[VERIFY lsp:php]" in out
        assert "error" in out.lower()
    finally:
        L._cleanup_lsp()
        shutil.rmtree(tmp, ignore_errors=True)


def test_edit_c_reports_lsp_section():
    import shutil
    import tempfile

    if not _lsp_lang_available("c_cpp"):
        pytest.skip("clangd tak tersedia")
    from joki.tools import lsp as L

    tmp = tempfile.mkdtemp(prefix="joki_editc_")
    try:
        p = os.path.join(tmp, "calc.c")
        handle_write_file({"path": p, "content": "int tambah(int a, int b) {\n    return a + b;\n}\n"})
        handle_read_file({"path": p})
        out = handle_edit_file({"path": p, "old_text": "    return a + b;", "new_text": "    return a + b + 0;"})
        assert "Edited" in out
        assert "[VERIFY lsp:c_cpp]" in out
    finally:
        L._cleanup_lsp()
        shutil.rmtree(tmp, ignore_errors=True)


def test_delegate_validates_and_guards_nesting():
    import joki.cli as C

    assert "wajib diisi" in C.handle_delegate_subtask({}).lower()
    C._DELEGATE_DEPTH = 1
    try:
        assert "bersarang" in C.handle_delegate_subtask({"goal": "x"}).lower()
    finally:
        C._DELEGATE_DEPTH = 0
    assert C._DELEGATE_DEPTH == 0


def test_delegate_text_answer_returns_summary(monkeypatch):
    import joki.cli as C

    monkeypatch.setattr(
        C, "call_llm",
        lambda msgs: {"role": "assistant", "content": "Modul rapi. Test lolos."},
    )
    out = C.handle_delegate_subtask(
        {"goal": "Rapikan modul auth", "paths": ["/tmp"], "max_iterations": 5}
    )
    assert "[SUB-TUGAS SELESAI]" in out
    assert "Rapikan modul auth" in out
    assert "Iterasi sub-agent: 1/5" in out
    assert "Modul rapi. Test lolos." in out
    assert C._DELEGATE_DEPTH == 0


def test_delegate_runs_tool_then_summarizes(monkeypatch):
    import joki.cli as C

    seq = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "function": {"name": "todo_show", "arguments": "{}"}}]},
        {"role": "assistant", "content": "Selesai."},
    ]
    monkeypatch.setattr(C, "call_llm", lambda msgs: seq.pop(0))
    out = C.handle_delegate_subtask({"goal": "Cek todo", "max_iterations": 5})
    assert "Iterasi sub-agent: 2/5" in out
    assert "Selesai." in out


def test_delegate_blocks_nested_tool_call(monkeypatch):
    import joki.cli as C

    seq = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "function": {"name": "delegate_subtask", "arguments": '{"goal": "x"}'}}]},
        {"role": "assistant", "content": "Lanjut manual."},
    ]
    seen = []

    real_execute = C.execute

    def _spy(name, targs):
        seen.append(name)
        return real_execute(name, targs)

    monkeypatch.setattr(C, "call_llm", lambda msgs: seq.pop(0))
    monkeypatch.setattr(C, "execute", _spy)
    out = C.handle_delegate_subtask({"goal": "Induk", "max_iterations": 5})
    # Panggilan delegate bersarang di-short-circuit tanpa menyentuh execute
    # (tidak ada rekursi, tidak ada tool lain yang jalan).
    assert seen == []
    assert C._DELEGATE_DEPTH == 0
    assert "Lanjut manual." in out


def test_delegate_tool_registered():
    from joki.constants import CORE_TOOLS, TOOLS, get_tools_for_mode
    from joki.executor import TOOL_HANDLERS

    names = {t["function"]["name"] for t in TOOLS}
    assert "delegate_subtask" in names
    assert "delegate_subtask" in CORE_TOOLS
    assert "delegate_subtask" in {t["function"]["name"] for t in get_tools_for_mode("code")}
    assert "delegate_subtask" in TOOL_HANDLERS


def test_rename_cross_dir_reports_coverage():
    import shutil
    import tempfile

    from joki.tools import lsp as L

    if "python" not in L._get_available_servers():
        pytest.skip("pyright tak tersedia")
    tmp = tempfile.mkdtemp(prefix="joki_rncov_")
    try:
        src = os.path.join(tmp, "proj", "src")
        tst = os.path.join(tmp, "proj", "tests")
        os.makedirs(src)
        os.makedirs(tst)
        with open(os.path.join(tmp, "proj", "requirements.txt"), "w") as f:
            f.write("x\n")
        a = os.path.join(src, "a.py")
        with open(a, "w") as f:
            f.write("def hitung_total(items):\n    return sum(items)\n")
        b = os.path.join(tst, "test_a.py")
        with open(b, "w") as f:
            f.write("from a import hitung_total\n")
        handle_read_file({"path": a})
        out = handle_rename_symbol(
            {"file_path": a, "old_name": "hitung_total", "new_name": "jumlahkan"})
        assert "[CAKUPAN] 2/2" in out
        with open(a) as f:
            assert "def jumlahkan" in f.read()
        with open(b) as f:
            assert "jumlahkan" in f.read()
    finally:
        L._cleanup_lsp()
        shutil.rmtree(tmp, ignore_errors=True)


def test_validate_syntax_multi_language():
    from joki.tools.files import _validate_syntax as v

    assert v("a.py", "x = 1") is None
    assert v("a.json", "{}") is None
    assert v("a.yaml", "k: v") is None
    assert v("a.toml", "k = 1") is None
    assert v("a.json", "{bad") is not None
    assert v("a.yaml", "k: [unclosed") is not None
    assert v("a.toml", "k = ") is not None
    # Toolchain sistem (node/php/ruby/gofmt/bash): skip bila tak terinstall,
    # error jelas bila terinstall dan sintaks rusak.
    import shutil

    if shutil.which("node"):
        assert v("a.js", "const x = 1;") is None
        assert v("a.js", "const x = ;") is not None
    if shutil.which("php"):
        assert v("a.php", "<?php echo 1;") is None
        assert v("a.php", "<?php echo ;") is not None
    if shutil.which("ruby"):
        assert v("a.rb", "puts 1") is None
        assert v("a.rb", "def foo(") is not None
    if shutil.which("gofmt") or shutil.which("go"):
        assert v("a.go", "package main") is None
        assert v("a.go", "package main func {") is not None
    if shutil.which("bash"):
        assert v("a.sh", "echo hi") is None
        assert v("a.sh", "if [") is not None
    # Bahasa butuh konteks proyek: selalu skip (serahkan ke LSP/linter).
    assert v("a.rs", "fn main() {") is None or "rustc" in (v("a.rs", "fn main() {") or "")


def test_validate_syntax_jvm_rust_classifiers():
    import shutil

    from joki.tools.files import _JAVA_SKIP_RE as JR
    from joki.tools.files import _RUST_SKIP_RE as RR
    from joki.tools.files import _TS_SKIP_RE as TR
    from joki.tools.files import _classify_diagnostics
    from joki.tools.files import _validate_syntax as v

    # Classifier murni (tanpa toolchain): ringkasan diabaikan, resolusi di-skip.
    assert _classify_diagnostics("A.java:1: error: package x does not exist\n1 error", JR) is None
    assert "illegal start" in (_classify_diagnostics(
        "B.java:2: error: illegal start of expression\n1 error", JR) or "")
    assert _classify_diagnostics("error[E0432]: unresolved import\nerror: aborting due to previous error", RR) is None
    assert "expected expression" in (_classify_diagnostics(
        "error: expected expression, found `;`", RR) or "")

    # Live bila toolchain ada.
    if shutil.which("javac"):
        assert v("A.java", "public class A {\n}") is None
        assert v("B.java", "public class B {\n void m() { foo(; }\n}") is not None
        assert v("C.java", "import com.example.Missing;\npublic class C {\n}") is None
        mixed = v("D.java", "import com.example.Missing;\npublic class D {\n void m() { foo(; }\n}")
        assert mixed is not None and "javac" in mixed.lower()
    if shutil.which("rustc"):
        assert v("a.rs", "fn main() {}") is None
        assert v("b.rs", "fn main() {\n let x = ;\n}") is not None
        assert v("c.rs", "use serde::Serialize;\nfn main() {}") is None
    # TS: skip bila tsc tak ada; classifier tercakup di atas via TR.
    assert TR.search("error TS2307: Cannot find module") is not None


def test_find_companion_test():
    import shutil
    import tempfile

    from joki.tools.files import _find_companion_test

    tmp = tempfile.mkdtemp(prefix="joki_comp_")

    def _write(name, content):
        with open(os.path.join(tmp, name), "w") as f:
            f.write(content)

    try:
        src = os.path.join(tmp, "calc.py")
        _write("calc.py", "x = 1\n")
        assert _find_companion_test(src) is None
        tst = os.path.join(tmp, "test_calc.py")
        _write("test_calc.py", "def test_x():\n    pass\n")
        assert _find_companion_test(src) == os.path.abspath(tst)
        # Ambiguitas (dua kandidat) -> None.
        _write("calc_test.py", "x\n")
        assert _find_companion_test(src) is None
        # File test itu sendiri -> None.
        assert _find_companion_test(tst) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_edit_auto_verifies_companion_test():
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="joki_autov_")
    try:
        p = os.path.join(tmp, "calc.py")
        handle_write_file({"path": p, "content": "def tambah(a, b):\n    return a + b\n"})
        handle_write_file({
            "path": os.path.join(tmp, "test_calc.py"),
            "content": "from calc import tambah\n\ndef test_tambah():\n    assert tambah(1, 2) == 3\n",
        })
        handle_read_file({"path": p})
        import sys

        sys.path.insert(0, tmp)
        try:
            out = handle_edit_file({"path": p, "old_text": "    return a + b", "new_text": "    return a + b + 0"})
        finally:
            sys.path.remove(tmp)
        assert "Edited" in out
        assert "[VERIFY test]" in out
        assert "LOLOS" in out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
