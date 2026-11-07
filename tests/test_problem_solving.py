"""Regression problem-solving: echo ditolak, lint bukan bukti, fail akhir log ketahuan."""
import json


def test_meaningful_rejects_echo_and_ls():
    from joki.cli import _is_meaningful_verify
    assert _is_meaningful_verify("run_command", {"cmd": "echo pytest ok"}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "ls -la"}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "python3 a/app.py"}, ["a/app.py"]) is True


def test_lint_not_counted_as_verify():
    from joki.cli import _is_meaningful_verify
    assert _is_meaningful_verify("run_linter", {"path": "a.py"}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_tests", {"path": "."}, ["a/app.py"]) is True


def test_tool_fail_full_output():
    from joki.cli import _is_tool_fail
    assert _is_tool_fail("x" * 19000 + "Traceback (most recent call last): boom") is True
    assert _is_tool_fail("ok sukses") is False


def test_verifier_hard_blocks_second_fail(tmp_path, monkeypatch):
    """FAIL ke-2 harus hard-block (return), bukan lolos."""
    import joki.cli as c
    import joki.verifier as vmod
    monkeypatch.chdir(tmp_path)
    with open("a.py", "w") as fh:
        fh.write("x = 1\n")
    from joki.tools import files as fmod
    fmod.handle_read_file({"path": "a.py"})

    def _fake_fail(**kwargs):
        return {"verdict": "FAIL", "reason": "tidak ada bukti", "stage": "deterministic"}

    monkeypatch.setattr(vmod, "verify_completion", _fake_fail)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {"name": "edit_file", "arguments": json.dumps(
                        {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})}}]}
        return {"role": "assistant", "content": "Sudah selesai."}

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "perbaiki a.py"}]
    c.agent_loop(msgs)
    # Harus ada 2 penolakan lalu return hard-block, klaim tidak diterima sebagai sukses.
    assert sum("[VERIFIKATOR]" in str(m.get("content", "")) for m in msgs) >= 1


def test_repro_lock_same_family_passes_suite_covers():
    from joki.verifier import deterministic_check
    assert deterministic_check(["a/app.py"], ["python3 a/app.py"], ["python3 a/app.py"])[0] is True
    assert deterministic_check(["a/app.py"], ["pytest tests/"], ["python3 a/app.py"])[0] is True
    assert deterministic_check(["a/app.py"], ["ls -la"], ["python3 a/app.py"])[0] is False


def test_skip_judge_env(monkeypatch):
    import os
    from joki.verifier import verify_completion
    monkeypatch.setenv("JOKI_SKIP_JUDGE", "1")
    r = verify_completion("req", "klaim", ["a.py"], ["python3 a.py"], ["python3 a.py"])
    assert r["verdict"] == "PASS" and "skip" in r["stage"]
    monkeypatch.delenv("JOKI_SKIP_JUDGE", raising=False)


def test_delegate_files_imported_to_parent(tmp_path, monkeypatch):
    import json
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {"name": "delegate_subtask", "arguments": json.dumps(
                        {"goal": "buat x", "paths": []})}}]}
        return {"role": "assistant", "content": "Selesai delegasi."}

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(c, "execute", lambda n, a: "[SUB-TUGAS SELESAI]\nFile berubah: sub_a.py" if n == "delegate_subtask" else "ok")
    monkeypatch.setenv("JOKI_DISABLE_VERIFIER", "1")
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "buatkan x via delegasi"}]
    c.agent_loop(msgs)
    assert any("sub_a.py" in str(m.get("content", "")) for m in msgs)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
