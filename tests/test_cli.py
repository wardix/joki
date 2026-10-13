"""Test ringan untuk joki.cli — fokus pada _classify_task & helper non-LLM."""
import json


def test_classify_task_coding():
    import joki.cli as c
    assert c._classify_task("tolong buatkan script python") == c.MODE_CODE


def test_classify_task_sysadmin():
    import joki.cli as c
    assert c._classify_task("konfigurasi nginx dan restart service") == c.MODE_SYSADMIN


def test_classify_task_security():
    import joki.cli as c
    assert c._classify_task("scan port 192.168.1.1") == c.MODE_SECURITY


def test_classify_task_general():
    import joki.cli as c
    assert c._classify_task("halo apa kabar") == c.MODE_GENERAL


def test_context_command_shows_usage(capsys):
    import joki.cli as c
    msgs = [{"role": "user", "content": "halo"}]
    cont, out_msgs = c._handle_command("/context", msgs)
    assert cont is True
    assert out_msgs is msgs
    shown = capsys.readouterr().out
    assert "Konteks:" in shown
    assert "token" in shown
    assert "%" in shown


def test_context_alias_konteks(capsys):
    import joki.cli as c
    cont, _ = c._handle_command("/konteks", [])
    assert cont is True
    assert "Konteks:" in capsys.readouterr().out


def test_context_in_slash_meta():
    import joki.cli as c
    assert "/context" in c._SLASH_META


def _complete(text):
    from prompt_toolkit.document import Document

    from joki.cli import _JokiCompleter

    doc = Document(text)
    return [c.text for c in _JokiCompleter().get_completions(doc, None)]


def test_skills_complete_subcommands():
    assert _complete("/skills ") == ["list", "on", "off", "reload"]
    assert _complete("/skills o") == ["on", "off"]


def test_skills_complete_ids_after_on_off():
    assert _complete("/skills on ") == ["debug-error", "laravel-proyek", "node-proyek", "refactor-aman", "verifikasi-eksekusi"]
    assert _complete("/skills off r") == ["refactor-aman"]
    assert _complete("/skills list ") == []
    assert _complete("/skills bogus ") == []


def test_skills_list_shows_source_mode(capsys):
    import joki.cli as c
    c._handle_command("/skills list", [])
    out = capsys.readouterr().out
    assert "[repo/" in out, out
    assert "Skills:" in out


def test_skills_bracket_input_safe(capsys):
    import joki.cli as c
    cont, _ = c._handle_command("/skills off [x]", [])
    assert cont is True
    out = capsys.readouterr().out
    assert "tidak dikenal" in out


def test_ui_changed_files_detects_views():
    import joki.cli as c
    assert c._ui_changed_files(["a/table.blade.php", "b/app.css", "c/Cart.vue"]) == [
        "a/table.blade.php", "b/app.css", "c/Cart.vue"]
    assert c._ui_changed_files(["ctl.php", "a.py", "x.js"]) == []


def test_render_proof_rejects_syntax_only():
    import joki.cli as c
    assert c._is_render_proof("screenshot", {}, "ok gambar") is True
    assert c._is_render_proof("run_command", {"cmd": "curl localhost | grep table"}, "ok") is True
    assert c._is_render_proof("run_command", {"cmd": "php -l table.blade.php"}, "ok") is False
    assert c._is_render_proof("run_command", {"cmd": "curl localhost"}, "FAILED (exit 7)") is False


def test_looks_like_task():
    import joki.cli as c
    assert c._looks_like_task("buatkan script python") is True
    assert c._looks_like_task("tolong perbaiki login") is True
    assert c._looks_like_task("cek port 8080") is True
    assert c._looks_like_task("halo") is False
    assert c._looks_like_task("apa itu closure?") is False
    assert c._looks_like_task("tolong jelaskan kode ini") is False
    assert c._looks_like_task("") is False


def test_zero_tool_text_exit_nudged_once(tmp_path, monkeypatch):
    """Regresi perintah-2x: teks tanpa tool untuk task langsung ditahan 1x."""
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "Baik, saya cek dulu."}
        if calls["n"] == 2:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})}}]}
        return {"role": "assistant", "content": "Sudah dicek."}

    monkeypatch.setattr(c, "call_llm", _llm)
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "cek isi direktori ini"}]
    c.agent_loop(msgs)
    assert calls["n"] == 3
    assert any("AKSI WAJIB" in str(m.get("content", "")) for m in msgs)


def test_zero_tool_guard_skips_smalltalk(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        return {"role": "assistant", "content": "Halo juga!"}

    monkeypatch.setattr(c, "call_llm", _llm)
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "halo"}]
    c.agent_loop(msgs)
    assert calls["n"] == 1


def test_dry_counter_resets_on_progress(tmp_path, monkeypatch):
    """Regresi: ronde berisi edit harus menolkan dry count agar kerja sehat
    (tool-call tanpa teks, umum di model thinking) tak dituduh stuck.

    Pola 5x list, 1x edit, 5x list: tanpa reset, streak dry mencapai 11 dan
    memicu inject [DRY RUN] (threshold 8); dengan reset, streak terpanjang
    cuma 5. Path selang-seling agar stuck-detector tidak ikut campur.
    """
    import joki.cli as c
    from joki.tools import files as fmod
    monkeypatch.chdir(tmp_path)
    with open("a.py", "w") as fh:
        fh.write("x = 1\n")
    fmod.handle_read_file({"path": "a.py"})
    calls = {"n": 0}

    def _tool(n, path):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": str(n), "function": {"name": "list_dir", "arguments": json.dumps({"path": path})}}]}

    def _llm(msgs):
        calls["n"] += 1
        n = calls["n"]
        if n <= 5:
            return _tool(n, "." if n % 2 else "./")
        if n == 6:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "e", "function": {"name": "edit_file", "arguments": json.dumps(
                        {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})}}]}
        if n <= 11:
            return _tool(n, "." if n % 2 else "./")
        return {"role": "assistant", "content": "Selesai."}

    monkeypatch.setattr(c, "call_llm", _llm)
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "update a.py lalu cek direktori"}]
    c.agent_loop(msgs)
    with open("a.py") as fh:
        assert "x = 2" in fh.read()
    assert not any("[DRY RUN]" in str(m.get("content", "")) for m in msgs)


def test_head_tail_keeps_head_and_tail():
    import joki.cli as c
    long = "A" * 5000 + "B" * 5000
    out = c._head_tail(long, 100, 100)
    assert out.startswith("A" * 100)
    assert out.endswith("B" * 100)
    assert "DIPOTONG" in out
    assert c._head_tail("pendek", 100, 100) == "pendek"


def test_truncate_tool_result_head_tail():
    import joki.cli as c
    long = "H" * 15000 + "T" * 15000
    out = c._truncate_tool_result(long)
    assert "T" * 100 in out  # ekor dipertahankan
    assert "DIPOTONG" in out
    assert c._truncate_tool_result("kecil") == "kecil"


def test_human_confirm_git_destructive():
    import joki.cli as c
    assert c._needs_human_confirm("git_push", {"force": True})[0] is True
    assert c._needs_human_confirm("git_push", {"force": False})[0] is False
    assert c._needs_human_confirm("git_branch", {"action": "delete_force", "name": "x"})[0] is True
    assert c._needs_human_confirm("git_branch", {"action": "list"})[0] is False


def test_skills_on_id_enables_skill(monkeypatch, capsys):
    """Alur '/skills on <id>' (hasil dropdown TUI 2 level) harus menyalakan skill,
    bukan ditolak 'Sub-perintah tak dikenal'."""
    import joki.cli as c
    called = {}

    def _fake_set(tid, on):
        called["args"] = (tid, on)
        return True

    monkeypatch.setattr("joki.skills.set_skill_enabled", _fake_set)
    cont, _ = c._handle_command("/skills on laravel-proyek", [])
    assert cont is True
    assert called.get("args") == ("laravel-proyek", True)
    assert "dinyalakan" in capsys.readouterr().out
