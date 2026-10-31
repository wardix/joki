"""Test verifier terpisah (read-only, fail-open)."""
import json
import os


def test_deterministic_no_edits_pass():
    from joki import verifier as v
    ok, _ = v.deterministic_check([], [])
    assert ok is True


def test_deterministic_no_evidence_fail():
    from joki import verifier as v
    ok, _ = v.deterministic_check(["a/app.py"], [])
    assert ok is False


def test_deterministic_rejects_echo_spoof():
    from joki import verifier as v
    # Spoof klasik: mengandung kata pytest tapi bin utama echo.
    ok, _ = v.deterministic_check(["a/app.py"], ["echo pytest ok"])
    assert ok is False
    ok2, _ = v.deterministic_check(["a/app.py"], ["echo testing app.py"])
    assert ok2 is False


def test_deterministic_rejects_mixed_echo_spoof():
    """Regresi: covered=True di luar blok if membuat anti-spoof mati bila
    basename hanya disebut di dalam echo SEMENTARA ada perintah non-echo
    lain (lepas dari cek 'semua bukti non-evidence')."""
    from joki import verifier as v
    # "echo testing app.py" menyebut basename tapi bin utamanya echo;
    # "git status" bukan runner dan tak menyebut basename -> harus FAIL.
    # (Kode lama: covered=True di echo, cek kedua lolos karena ada git.)
    ok, reason = v.deterministic_check(["a/app.py"], ["echo testing app.py", "git status"])
    assert ok is False, reason
    # Runner sah untuk file yang sama tetap lolos.
    assert v.deterministic_check(["a/app.py"], ["echo hi", "python3 a/app.py"])[0] is True
    # Suite runner global tetap mencakup semua file.
    assert v.deterministic_check(["a/app.py"], ["pytest tests/ -q"])[0] is True


def test_deterministic_accepts_runner_and_basename():
    from joki import verifier as v
    assert v.deterministic_check(["a/app.py"], ["pytest tests/ -q"])[0] is True
    assert v.deterministic_check(["a/app.py"], ["python3 a/app.py"])[0] is True
    assert v.deterministic_check(["a/app.py"], ["ls -la"])[0] is False


def test_parse_verdict_json_and_fence():
    from joki import verifier as v
    assert v._parse_verdict('{"verdict":"FAIL","reason":"x"}')["verdict"] == "FAIL"
    assert v._parse_verdict('```json\n{"verdict":"PASS","reason":"ok"}\n```')["verdict"] == "PASS"
    assert v._parse_verdict("bukan json") is None


def test_kill_switch_env(monkeypatch):
    from joki import verifier as v
    monkeypatch.setenv("JOKI_DISABLE_VERIFIER", "1")
    r = v.verify_completion("a", "b", ["x"], [])
    assert r["verdict"] == "SKIP"


def test_agent_loop_verifier_blocks_spoof_once(tmp_path, monkeypatch):
    """Klaim selesai tanpa bukti harus ditahan verifier 1x, lalu lolos setelah bukti."""
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    with open("a.py", "w") as fh:
        fh.write("x = 1\n")
    # Anggap a.py sudah dibaca agar edit lolos gate read.
    from joki.tools import files as fmod
    fmod.handle_read_file({"path": "a.py"})

    calls = {"n": 0, "verifier": 0}
    orig_verify = c.__dict__.get("verify_completion", None)

    def _fake_verify(**kwargs):
        calls["verifier"] += 1
        # Simulasi deterministic FAIL: tidak ada verified_texts.
        return {"verdict": "FAIL", "reason": "tidak ada bukti", "stage": "deterministic"}

    # Patch di modul verifier (yang diimport lazy oleh cli).
    import joki.verifier as vmod
    monkeypatch.setattr(vmod, "verify_completion", _fake_verify)

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {"name": "edit_file", "arguments": json.dumps(
                        {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})}}]}
        if calls["n"] == 2:
            # Klaim selesai tanpa run bukti apa pun.
            return {"role": "assistant", "content": "Sudah diperbaiki."}
        # Setelah ditolak verifier, lakukan verifikasi nyata lalu klaim lagi.
        if calls["n"] == 3:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "2", "function": {"name": "run_command", "arguments": json.dumps(
                        {"cmd": "python3 a.py"})}}]}
        return {"role": "assistant", "content": "Sudah diperbaiki dan terverifikasi python3 a.py."}

    monkeypatch.setattr(c, "call_llm", _llm)
    # Matikan judge LLM asli agar test deterministik: verifier fake di atas
    # FAIL sekali, lalu kita buat PASS di panggilan kedua dengan menukar fake.
    orig_fake = _fake_verify

    def _fake_verify2(**kwargs):
        calls["verifier"] += 1
        if calls["verifier"] <= 1:
            return {"verdict": "FAIL", "reason": "tidak ada bukti", "stage": "deterministic"}
        return {"verdict": "PASS", "reason": "ada bukti", "stage": "deterministic"}

    monkeypatch.setattr(vmod, "verify_completion", _fake_verify2)
    c._joki_cancel.clear()
    # Nonaktifkan gate lama agar fokus ke verifier (nolkan nudge dengan bukti palsu? biarkan jalan).
    # Paksa JOKI_DISABLE_VERIFIER kosong.
    monkeypatch.delenv("JOKI_DISABLE_VERIFIER", raising=False)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "perbaiki a.py"}]
    c.agent_loop(msgs)
    assert calls["verifier"] >= 1
    assert any("[VERIFIKATOR]" in str(m.get("content", "")) for m in msgs)
