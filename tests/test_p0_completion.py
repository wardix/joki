"""Regresi P0: jangan klaim selesai dari tool yang gagal / bukti palsu.

Mencakup:
- edit yang rollback karena syntax error tidak boleh dihitung sukses;
- `run_tests` error / framework tak dikenal bukan bukti;
- `curl` HTTP 404 bukan bukti render/eksekusi;
- `python3 --version`, `pytest --collect-only`, `npx vue-tsc`, `npm install`
  bukan verifikasi;
- verifier tidak boleh memakai model worker yang sama (self-judge);
- bukti terstruktur membawa output + hash file ke judge.
"""
import json
import os
import shutil
import subprocess

import pytest

from joki import verifier as v
from joki.cli import (
    _is_meaningful_verify,
    _is_render_proof,
    _is_repro_command,
    _is_tool_fail,
)
from joki.tools.files import handle_edit_file, handle_read_file
from joki.tools.office import handle_write_office
from joki.tools.shell import handle_run_tests


def test_tool_fail_detects_rollback_and_plain_errors():
    assert _is_tool_fail(
        "Error: hasil edit merusak sintaks — perubahan DIBATALKAN (rollback).")
    assert _is_tool_fail("Error: file tidak ditemukan")
    assert _is_tool_fail("fatal: not a git repository")
    assert _is_tool_fail("[REPRODUKSI WAJIB] jalankan dulu")
    assert _is_tool_fail("[NO TESTS] tidak ada test dijalankan")
    assert _is_tool_fail("SyntaxError: invalid syntax")
    assert _is_tool_fail("HTTP/1.1 404 Not Found")
    assert _is_tool_fail("404")
    assert not _is_tool_fail("450 file diproses")
    assert not _is_tool_fail("ok sukses")
    # Ringkasan test sukses tidak boleh dianggap gagal hanya karena kata
    # "0 failed" (cargo/pytest summaries).
    assert not _is_tool_fail("test result: ok. 1 passed; 0 failed")
    assert _is_tool_fail("10 failed, 0 passed") is True


def test_edit_rollback_is_failure_and_file_unchanged(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("x = 1\n")
    handle_read_file({"path": str(path)})
    result = handle_edit_file({
        "path": str(path),
        "old_text": "x = 1",
        "new_text": "def broken(:",
    })
    assert "DIBATALKAN" in result
    assert _is_tool_fail(result) is True
    assert path.read_text() == "x = 1\n"


def test_run_tests_errors_are_failures(tmp_path):
    missing = str(tmp_path / "tidak-ada")
    assert _is_tool_fail(handle_run_tests({"path": missing})) is True
    assert _is_tool_fail(handle_run_tests({
        "path": str(tmp_path), "framework": "framework-ngawur"})) is True


def test_run_tests_no_tests_is_failure(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "bukan_test.py").write_text("x = 1\n")
    result = handle_run_tests({"path": str(tmp_path), "framework": "pytest"})
    assert _is_tool_fail(result) is True, result


def test_curl_404_not_render_or_execution_proof(tmp_path):
    path = tmp_path / "Packages.vue"
    path.write_text("<template><div/></template>\n")
    assert _is_tool_fail("404") is True
    assert _is_render_proof(
        "run_command", {"cmd": "curl -s localhost:5173/"}, "404") is False
    assert _is_render_proof(
        "run_command", {"cmd": "curl -s localhost:5173/"},
        "HTTP/1.1 404 Not Found") is False
    # Record terstruktur dengan output 404 juga ditolak verifier.
    rec = {
        "ok": True,
        "kind": "http",
        "cmd": "curl -s localhost:5173/",
        "output": "404",
        "files": [str(path)],
        "file_sigs": {str(path): v._file_signature(str(path))},
    }
    ok, _ = v.deterministic_check([str(path)], [], verified_evidence=[rec])
    assert ok is False


def test_version_and_nonrunner_commands_not_verification(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("print('ok')\n")
    changed = [str(path)]
    for cmd in (
        "python3 --version",
        "pytest --collect-only",
        "npx vue-tsc --noEmit",
        "npm install",
    ):
        assert _is_meaningful_verify("run_command", {"cmd": cmd}, changed) is False, cmd
        assert v.deterministic_check(changed, [cmd])[0] is False, cmd
    assert _is_meaningful_verify(
        "run_command", {"cmd": f"python3 {path}"}, changed) is True
    assert _is_meaningful_verify(
        "run_command", {"cmd": "pytest -q"}, changed) is True
    assert _is_meaningful_verify("test_and_fix", {"cmd": ""}, changed) is False


def test_repro_command_rejects_read_only_and_failed_shapes():
    assert _is_repro_command("pwd") is False
    assert _is_repro_command("ls -la") is False
    assert _is_repro_command("npm install") is False
    assert _is_repro_command("pytest -q") is True
    assert _is_repro_command("python3 app.py") is True
    assert _is_repro_command("curl -s localhost:5173/") is True


def test_same_model_on_other_endpoint_is_still_self_judge():
    worker = {
        "base_url": "http://worker.local/v1", "model": "same-model",
        "provider": "openai", "api_keys": ["k"],
    }
    judge = {
        "base_url": "http://judge.local/v1", "model": "same-model",
        "provider": "openai", "api_keys": ["k"],
    }
    mc, key = v._pick_independent_judge({"worker": worker, "judge": judge}, worker, "judge")
    assert mc is None
    assert key == ""


def test_self_judge_is_skipped_not_pass(tmp_path, monkeypatch):
    import joki.config as cfg

    path = tmp_path / "app.py"
    path.write_text("print(1)\n")
    rec = {
        "ok": True,
        "kind": "file_exec",
        "cmd": f"python3 {path}",
        "output": "1\n",
        "files": [str(path)],
        "file_sigs": {str(path): v._file_signature(str(path))},
    }
    worker = {
        "name": "Worker", "base_url": "http://worker.local/v1",
        "model": "worker-model", "provider": "openai", "api_keys": ["k"],
    }
    monkeypatch.setattr(cfg, "_MODELS", {"worker": worker})
    monkeypatch.setattr(cfg, "_current_model_config", dict(worker))
    monkeypatch.setenv("JOKI_JUDGE_MODEL", "worker")
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    monkeypatch.delenv("JOKI_SKIP_JUDGE", raising=False)
    calls = []
    monkeypatch.setattr(v, "_call_judge", lambda *a, **k: calls.append(1) or '{"verdict":"PASS"}')

    res = v.verify_completion(
        "perbaiki app.py", "sudah selesai", [str(path)],
        [f"python3 {path}"], verified_evidence=[rec])
    assert res["verdict"] == "SKIP"
    assert res["stage"] == "self-judge"
    assert calls == []


def test_independent_judge_gets_output_and_mutation_proof(tmp_path, monkeypatch):
    path = tmp_path / "app.py"
    path.write_text("print(1)\n")
    rec = {
        "tool": "run_command", "ok": True, "kind": "file_exec",
        "cmd": f"python3 {path}", "output": "1\n",
        "files": [str(path)],
        "file_sigs": {str(path): v._file_signature(str(path))},
    }
    msgs = v._build_judge_messages(
        "perbaiki app.py", "selesai", [str(path)],
        [f"python3 {path}"], verified_evidence=[rec])
    user = msgs[-1]["content"]
    system = msgs[0]["content"]
    assert "output: 1" in user
    assert f"python3 {path}" in user
    assert "DATA TIDAK TERPERCAYA" in system
    assert "404" in system
    assert "--version" in system


def test_write_office_marks_file_change(tmp_path):
    from joki.state import _file_change_log
    path = tmp_path / "data.csv"
    _file_change_log.clear()
    try:
        result = handle_write_office({
            "path": str(path), "content": "nama,nilai\nbudi,90\n"})
        assert "CSV created:" in result
        assert os.path.abspath(str(path)) in _file_change_log
    finally:
        _file_change_log.clear()


def test_no_mutation_claim_for_change_request_is_labeled(tmp_path, monkeypatch):
    import joki.cli as c

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    outputs = []
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        return {"role": "assistant",
                "content": "Sudah selesai, fitur X sudah ditambahkan."}

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(c, "stream_print",
                        lambda text, **kw: outputs.append(str(text)))
    c._joki_cancel.clear()
    c.agent_loop([{"role": "system", "content": "sys"},
                  {"role": "user",
                   "content": "tambahkan fitur X di aplikasi ini"}])
    assert calls["n"] >= 2
    assert any("[BELUM TERVERIFIKASI]" in o for o in outputs), outputs


def test_agent_loop_passes_output_and_file_hashes_to_verifier(tmp_path, monkeypatch):
    import joki.cli as c
    import joki.verifier as vmod

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "a.py"
    path.write_text("x = 1\nprint(x)\n")
    captured = {}
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {
                        "name": "read_file", "arguments": json.dumps(
                            {"path": str(path)})}}]}
        if calls["n"] == 2:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "2", "function": {
                        "name": "edit_file", "arguments": json.dumps(
                            {"path": str(path), "old_text": "x = 1",
                             "new_text": "x = 2"})}}]}
        if calls["n"] == 3:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "3", "function": {
                        "name": "run_command", "arguments": json.dumps(
                            {"cmd": f"python3 {path}"})}}]}
        return {"role": "assistant", "content": "Selesai."}

    def _fake_verify(**kwargs):
        captured.update(kwargs)
        return {"verdict": "PASS", "reason": "ok", "stage": "test"}

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(vmod, "verify_completion", _fake_verify)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    c._joki_cancel.clear()
    c.agent_loop([{"role": "system", "content": "sys"},
                  {"role": "user", "content": f"perbaiki {path}"}])

    evidence = captured.get("verified_evidence") or []
    assert evidence, captured
    assert any(e.get("cmd", "").startswith(f"python3 {path}") for e in evidence)
    rec = next(e for e in evidence if e.get("cmd", "").startswith(f"python3 {path}"))
    assert "output" in rec and rec.get("output") is not None
    assert rec.get("file_sigs", {}).get(str(path)) == vmod._file_signature(str(path))
    assert str(path) in {os.path.abspath(str(p)) for p in captured.get("changed_files") or []}


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=True).stdout


def _make_pull_repo(tmp_path):
    """Buat clone yang ketinggalan satu commit dari remote."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test")
    (seed / "catatan.txt").write_text("awal\n")
    _git(seed, "add", "catatan.txt")
    _git(seed, "commit", "-m", "awal")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", "-b", "main", str(remote), str(clone))

    (seed / "catatan.txt").write_text("versi baru dari remote\n")
    _git(seed, "add", "catatan.txt")
    _git(seed, "commit", "-m", "perubahan remote")
    _git(seed, "push", "origin", "main")
    return clone


def test_git_pull_fast_forward_marks_incoming_files(tmp_path, monkeypatch):
    """Pull fast-forward mengubah worktree tapi `git status` tetap bersih.

    File hasil pull harus masuk changed_files dan memicu kewajiban verifikasi,
    bukan lolos sebagai "tidak ada perubahan".
    """
    if not shutil.which("git"):
        pytest.skip("git tidak tersedia")
    clone = _make_pull_repo(tmp_path)

    monkeypatch.chdir(clone)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    captured = {}
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {
                        "name": "git_pull",
                        "arguments": json.dumps(
                            {"cwd": str(clone), "remote": "origin",
                             "branch": "main"})}}]}
        return {"role": "assistant", "content": "Selesai."}

    def _fake_verify(**kwargs):
        captured.update(kwargs)
        return {"verdict": "PASS", "reason": "ok", "stage": "test"}

    import joki.cli as c
    import joki.verifier as vmod

    outputs = []
    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(c, "stream_print",
                        lambda text, **kw: outputs.append(str(text)))
    monkeypatch.setattr(vmod, "verify_completion", _fake_verify)
    c._joki_cancel.clear()
    c.agent_loop([{"role": "system", "content": "sys"},
                  {"role": "user", "content": "ambil perubahan terbaru dari remote"}])

    changed = {os.path.abspath(str(p)) for p in captured.get("changed_files") or []}
    assert str(clone / "catatan.txt") in changed, captured
    assert calls["n"] >= 2
    # Setelah hakim independen PASS, klaim tidak boleh tetap dilabeli
    # "belum terverifikasi" hanya karena _needs_verify dihitung sebelum PASS.
    assert not any("[BELUM TERVERIFIKASI]" in o for o in outputs), outputs


def test_run_command_git_pull_marks_incoming_files(tmp_path, monkeypatch):
    """Bypass tool git khusus lewat run_command tetap harus terlacak."""
    if not shutil.which("git"):
        pytest.skip("git tidak tersedia")
    clone = _make_pull_repo(tmp_path)

    monkeypatch.chdir(clone)
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    captured = {}
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {
                        "name": "run_command",
                        "arguments": json.dumps(
                            {"cmd": "git pull origin main",
                             "cwd": str(clone)})}}]}
        return {"role": "assistant", "content": "Selesai."}

    def _fake_verify(**kwargs):
        captured.update(kwargs)
        return {"verdict": "PASS", "reason": "ok", "stage": "test"}

    import joki.cli as c
    import joki.verifier as vmod

    monkeypatch.setattr(c, "call_llm", _llm)
    monkeypatch.setattr(vmod, "verify_completion", _fake_verify)
    c._joki_cancel.clear()
    c.agent_loop([{"role": "system", "content": "sys"},
                  {"role": "user", "content": "ambil perubahan terbaru dari remote"}])

    changed = {os.path.abspath(str(p)) for p in captured.get("changed_files") or []}
    assert str(clone / "catatan.txt") in changed, captured
    assert calls["n"] >= 2
