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


def test_is_test_file_patterns():
    import joki.cli as c
    assert c._is_test_file("tests/test_actions.py") is True
    assert c._is_test_file("src/actions_test.py") is True
    assert c._is_test_file("src/actions.test.ts") is True
    assert c._is_test_file("src/__tests__/actions.js") is True
    assert c._is_test_file("app/FooTest.php") is True
    assert c._is_test_file("src/actions.py") is False
    assert c._is_test_file("src/latest.py") is False


def test_test_file_source_hints_from_import(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "actions.py").write_text("x = 1\n", encoding="utf-8")
    hints = c._test_file_source_hints(
        "tests/test_actions.py", "from src.actions import run\n"
    )
    assert hints == ["src/actions.py"]


def test_auto_test_failure_on_test_file_targets_source(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "actions.py").write_text("x = 1\n", encoding="utf-8")
    msg = c._auto_test_failure_prompt(
        "tests/test_actions.py",
        "from src.actions import run\n",
        "pytest tests/test_actions.py",
        "FAILED test_actions.py",
    )
    assert "FILE TEST" in msg
    assert "JANGAN mengubah test" in msg
    assert "src/actions.py" in msg
    assert "PERBAIKI file ini" not in msg


def test_auto_test_failure_on_source_keeps_original_message():
    import joki.cli as c
    msg = c._auto_test_failure_prompt(
        "src/actions.py", "", "pytest tests/test_actions.py", "FAILED"
    )
    assert "PERBAIKI file ini" in msg


def test_sanitize_previous_task_messages_removes_stale_internal():
    import joki.cli as c
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "system", "content": "[INDEX PROYEK] peta"},
        {"role": "system", "content": "[CHECKPOINT TUGAS] tujuan lama"},
        {"role": "system", "content": "[KONTEKS TUGAS] progres lama"},
        {"role": "user", "content": "buat fitur A"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "1", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }]},
        {"role": "tool", "content": "isi file", "tool_call_id": "1"},
        {"role": "user", "content": "[PERUBAHAN FILE] x"},
        {"role": "user", "content": "[VERIFIKASI WAJIB] y"},
        {"role": "assistant", "content": "selesai"},
        {"role": "user", "content": "ganti gesture"},
    ]
    removed = c._sanitize_previous_task_messages(msgs)
    contents = [str(m.get("content", "")) for m in msgs]
    assert removed == 4
    assert "system prompt" in contents
    assert "[INDEX PROYEK] peta" in contents
    assert "buat fitur A" in contents
    assert "isi file" in contents
    assert "selesai" in contents
    assert not any(x.startswith(("[CHECKPOINT TUGAS]", "[KONTEKS TUGAS]",
                                 "[PERUBAHAN FILE]", "[VERIFIKASI")) for x in contents)
    assert contents[-2] == c._TASK_BOUNDARY
    assert contents[-1] == "ganti gesture"


def test_sanitize_previous_task_skips_continuation():
    import joki.cli as c
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "buat fitur A"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "[VERIFIKASI WAJIB] lama"},
        {"role": "user", "content": "lanjutkan"},
    ]
    before = list(msgs)
    assert c._sanitize_previous_task_messages(msgs) == 0
    assert msgs == before


def test_sanitize_initial_task_does_not_add_boundary():
    import joki.cli as c
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "buat fitur A"},
    ]
    assert c._sanitize_previous_task_messages(msgs) == 0
    assert len(msgs) == 2


def test_latest_real_user_request_skips_nudges_and_continuation():
    import joki.cli as c
    msgs = [
        {"role": "user", "content": "buat fitur A"},
        {"role": "user", "content": "[AKSI WAJIB] nudge"},
        {"role": "user", "content": "lanjutkan"},
    ]
    assert c._latest_real_user_request(msgs, skip_continuation=True) == "buat fitur A"


def test_agent_loop_stops_when_question_unanswered(tmp_path, monkeypatch):
    import joki.cli as c
    from joki.tools.question import QUESTION_UNANSWERED

    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "q1",
                "function": {
                    "name": "question",
                    "arguments": json.dumps({"query": "File mana?"}),
                },
            }],
        }

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(
        c, "execute",
        lambda name, args: QUESTION_UNANSWERED + " Jawaban user tidak tersedia",
    )
    c._joki_cancel.clear()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "tolong perbaiki file yang error"},
    ]
    c.agent_loop(msgs)
    assert calls["n"] == 1
    assert msgs[-1]["role"] == "tool"
    assert QUESTION_UNANSWERED in msgs[-1]["content"]


def test_task_detection_ignores_coba():
    import joki.cli as c
    assert c._looks_like_task("coba lihat ini") is False
    assert c._looks_like_task("perbaiki file ini") is True


def test_venv_python_pytest_is_real_evidence():
    import joki.cli as c
    assert c._is_suite_runner(".venv/bin/python -m pytest -q") is True
    assert c._is_meaningful_verify(
        "run_command",
        {"cmd": ".venv/bin/python -m pytest -q"},
        ["src/actions.py"],
    ) is True
    assert c._is_meaningful_verify(
        "run_command",
        {"cmd": ".venv/bin/python app.py"},
        ["app.py"],
    ) is True


def test_import_probe_only_covers_matching_file(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "actions.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")
    cmd = '.venv/bin/python -c "from src.actions import run"'
    assert set(c._python_import_targets(cmd)) >= {"src.actions", "src.actions.run"}
    assert c._is_meaningful_verify(
        "run_command", {"cmd": cmd}, ["src/actions.py"]
    ) is True
    assert c._is_meaningful_verify(
        "run_command", {"cmd": cmd}, ["src/other.py"]
    ) is False


def test_merge_resumed_context_only_for_continuation():
    import joki.cli as c
    target = {
        "original_request": "tugas baru",
        "files_created": [],
        "facts": [],
        "decisions": [],
        "progress_notes": [],
        "current_step": "",
        "last_context_inject": 9,
    }
    resumed = {
        "original_request": "tugas lama",
        "files_created": ["lama.py"],
        "facts": ["fakta lama"],
        "decisions": ["keputusan lama"],
        "progress_notes": ["progress lama"],
        "current_step": "langkah lama",
    }
    assert c._merge_resumed_task_context(target, resumed, False) is False
    assert target["files_created"] == []
    assert c._merge_resumed_task_context(target, resumed, True) is True
    assert target["original_request"] == "tugas baru"
    assert target["files_created"] == ["lama.py"]
    assert target["facts"] == ["fakta lama"]
    assert target["last_context_inject"] == 0


def test_detect_test_framework_uses_project_venv(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    cmd, runner = c._detect_test_framework(
        "tests/test_actions.py", "def test_x():\n    pass\n", ".py"
    )
    assert runner == "pytest"
    assert "venv/bin/python -m pytest" in cmd
    assert "tests/test_actions.py" in cmd


def test_detect_python_test_without_config(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    cmd, runner = c._detect_test_framework(
        "tests/test_actions.py", "def test_x():\n    pass\n", ".py"
    )
    assert runner == "pytest"
    assert "python3 -m pytest" in cmd


def test_venv_py_compile_is_not_evidence():
    import joki.cli as c
    assert c._is_meaningful_verify(
        "run_command",
        {"cmd": ".venv/bin/python -m py_compile app.py"},
        ["app.py"],
    ) is False


def test_verifier_progress_signature_tracks_real_progress(tmp_path, monkeypatch):
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    sig1 = c._verifier_progress_signature([str(f)], [])
    assert c._verifier_progress_signature([str(f)], []) == sig1
    f.write_text("x = 2\n", encoding="utf-8")
    sig2 = c._verifier_progress_signature([str(f)], [])
    assert sig2 != sig1
    ev = [{"cmd": "python3 a.py", "files": [], "output": "ok"}]
    assert c._verifier_progress_signature([str(f)], ev) != sig2


def test_apply_model_switch_notice_once():
    import joki.cli as c
    from joki.state import _consume_model_switch, _set_model_switch

    _consume_model_switch()
    _set_model_switch("Model A", "model-a", "Model B", "model-b", "fallback")
    msgs = [{"role": "user", "content": "kerjakan"}]
    info = c._apply_model_switch_notice(msgs)
    assert info["to_name"] == "Model B"
    assert msgs[-1]["role"] == "system"
    assert "[MODEL SWITCH]" in msgs[-1]["content"]
    assert "baca ulang" in msgs[-1]["content"].lower()
    assert c._apply_model_switch_notice(msgs) is None


def test_verifier_no_progress_hard_stop_does_not_ask_manual(
    tmp_path, monkeypatch, capsys
):
    import joki.cli as c
    import joki.verifier as vmod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    from joki.tools import files as fmod
    fmod.handle_read_file({"path": "a.py"})

    calls = {"n": 0}
    verify_calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "1",
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({
                            "path": "a.py",
                            "old_text": "x = 1",
                            "new_text": "x = 2",
                        }),
                    },
                }],
            }
        return {"role": "assistant", "content": "Sudah selesai."}

    def _fail(**kwargs):
        verify_calls["n"] += 1
        return {
            "verdict": "FAIL",
            "reason": "tidak ada bukti eksekusi",
            "stage": "deterministic",
        }

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(vmod, "verify_completion", _fail)
    c._joki_cancel.clear()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "perbaiki a.py"},
    ]
    c.agent_loop(msgs)
    out = capsys.readouterr().out.lower()
    assert calls["n"] >= 3
    assert verify_calls["n"] == 2
    assert "periksa manual" not in out
    assert "tanpa kemajuan" in out or "tidak ada file/bukti baru" in out


def test_sanitize_removes_stale_model_switch_notice():
    import joki.cli as c
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "tugas lama"},
        {"role": "assistant", "content": "ok"},
        {"role": "system", "content": "[MODEL SWITCH] model berubah"},
        {"role": "user", "content": "tugas baru"},
    ]
    removed = c._sanitize_previous_task_messages(msgs)
    contents = [str(m.get("content", "")) for m in msgs]
    assert removed == 1
    assert not any(x.startswith("[MODEL SWITCH]") for x in contents)
    assert contents[-1] == "tugas baru"


def test_verifier_progress_prevents_hard_stop(tmp_path, monkeypatch, capsys):
    import joki.cli as c
    import joki.verifier as vmod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    from joki.tools import files as fmod
    fmod.handle_read_file({"path": "a.py"})

    calls = {"n": 0}
    verdicts = ["FAIL", "FAIL", "PASS"]

    def _llm(msgs):
        calls["n"] += 1
        n = calls["n"]
        if n in (1, 5):
            old, new = ("x = 1", "x = 2") if n == 1 else ("x = 2", "x = 3")
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": str(n),
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({
                            "path": "a.py",
                            "old_text": old,
                            "new_text": new,
                        }),
                    },
                }],
            }
        return {"role": "assistant", "content": "Sudah selesai."}

    def _verify(**kwargs):
        verdict = verdicts.pop(0)
        return {
            "verdict": verdict,
            "reason": "butuh bukti" if verdict == "FAIL" else "bukti cukup",
            "stage": "deterministic",
        }

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(vmod, "verify_completion", _verify)
    c._joki_cancel.clear()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "ubah isi a.py"},
    ]
    c.agent_loop(msgs)
    out = capsys.readouterr().out.lower()
    assert calls["n"] == 7
    assert verdicts == []
    assert "tanpa kemajuan" not in out
    assert "ada kemajuan file/bukti baru" in out
