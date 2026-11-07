"""Test hardening anti-klaim-palsu & anti-"menyuruh user" (patch lapangan).

Kasus nyata: model Poolside Laguna S 2.1 sering (1) klaim "sudah diperbaiki"
padahal cuma `php -l`/grep/auto-test palsu, dan (2) menyuruh user menjalankan
perintah shell alih-alih memakai run_command sendiri.
"""
import json


# ---------- Patch 1+2: bukti lint/viewer/HTTP di cli ----------

def test_meaningful_verify_rejects_lint_only():
    from joki.cli import _is_meaningful_verify
    ctl = "app/Http/Controllers/TaskController.php"
    assert _is_meaningful_verify("run_command", {"cmd": f"php -l {ctl}"}, [ctl]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "python3 -m py_compile app.py"}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "node --check x.js"}, ["x.js"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "npx tsc --noEmit"}, ["x.ts"]) is False
    # Kecuali error awal memang syntax/parse error → lint boleh jadi bukti.
    assert _is_meaningful_verify(
        "run_command", {"cmd": "php -l ctl.php"}, ["ctl.php"],
        "Parse error: syntax error, unexpected token di ctl.php") is True


def test_meaningful_verify_rejects_file_viewers():
    from joki.cli import _is_meaningful_verify
    assert _is_meaningful_verify("run_command", {"cmd": "grep -n tempnam TaskController.php"}, ["TaskController.php"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "awk '{print}' app.py"}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": 'bash -c "grep x app.py"'}, ["a/app.py"]) is False
    assert _is_meaningful_verify("run_command", {"cmd": "sed -n '1,5p' app.py"}, ["a/app.py"]) is False


def test_meaningful_verify_accepts_exec_and_local_http():
    from joki.cli import _is_meaningful_verify
    # Eksekusi nyata tetap dihitung.
    assert _is_meaningful_verify("run_command", {"cmd": "python3 a/app.py"}, ["a/app.py"]) is True
    # Repro HTTP lokal (kasus Laravel 500) dihitung bukti.
    assert _is_meaningful_verify(
        "run_command", {"cmd": "curl -s http://localhost/gtd/login"},
        ["app/Http/Controllers/TaskController.php"]) is True
    assert _is_meaningful_verify(
        "run_command", {"cmd": "wget -q -O - http://127.0.0.1:8080/x"}, ["a.py"]) is True
    # curl ke internet biasa bukan repro app user.
    assert _is_meaningful_verify("run_command", {"cmd": "curl -s https://example.com"}, ["a.py"]) is False


def test_tool_fail_detects_app_errors():
    from joki.cli import _is_tool_fail
    assert _is_tool_fail("ErrorException: tempnam(): file created in the system's temporary directory") is True
    assert _is_tool_fail("<html><title>500 Internal Server Error</title></html>") is True
    assert _is_tool_fail("500") is True
    assert _is_tool_fail("HTTP/1.1 502 Bad Gateway") is True
    assert _is_tool_fail("SQLSTATE[42S02]: Base table or view not found") is True
    assert _is_tool_fail("Fatal error: Uncaught TypeError: x") is True
    assert _is_tool_fail("semua aman, tidak ada error") is False
    assert _is_tool_fail("450 file diproses") is False


# ---------- Patch 1+4: verifier deterministik & judge ----------

def test_deterministic_rejects_lint_and_viewer():
    from joki import verifier as v
    ok, _ = v.deterministic_check(["ctl.php"], ["php -l ctl.php"])
    assert ok is False
    ok2, _ = v.deterministic_check(["TaskController.php"], ["grep -n tempnam TaskController.php"])
    assert ok2 is False
    # Syntax context mengizinkan lint sebagai bukti.
    ok3, _ = v.deterministic_check(["ctl.php"], ["php -l ctl.php"], None, "syntax error di ctl.php")
    assert ok3 is True
    # Repro curl terkunci tetap bukti sah.
    ok4, _ = v.deterministic_check(
        ["TaskController.php"], ["curl -s http://localhost/gtd/login"],
        ["curl -s http://localhost/gtd/login"])
    assert ok4 is True


def test_has_any_evidence_ignores_lint_and_viewers():
    from joki import verifier as v
    assert v._has_any_evidence(["php -l ctl.php"]) is False
    assert v._has_any_evidence(["grep x app.py"]) is False
    assert v._has_any_evidence(["php -l ctl.php"], allow_lint=True) is True
    assert v._has_any_evidence(["curl -s http://localhost/x"]) is True


def test_judge_prompt_rejects_syntax_only():
    from joki import verifier as v
    msgs = v._build_judge_messages("http://localhost/gtd/login error 500", "sudah diperbaiki",
                                   ["app/Http/Controllers/TaskController.php"],
                                   ["php -l app/Http/Controllers/TaskController.php"])
    sys = msgs[0]["content"]
    assert "MEMBERI" not in sys  # sanity teks
    assert "syntax" in sys.lower() and "curl" in sys.lower()


# ---------- Patch 3: auto-test PHP framework/blade di-skip ----------

def test_auto_test_skips_framework_php():
    from joki.cli import _detect_test_framework
    laravel = "<?php\nnamespace App\\Http\\Controllers;\nclass TaskController { public function index() {} }\n"
    assert _detect_test_framework("app/Http/Controllers/TaskController.php", laravel, ".php") is None
    blade = "<table id='x'>@foreach($rows as $r)<tr></tr>@endforeach</table>"
    assert _detect_test_framework("resources/views/tasks.blade.php", blade, ".php") is None
    # Script PHP biasa (top-level eksekusi) tetap dites.
    assert _detect_test_framework("tools/hello.php", "<?php echo 'hi';", ".php") == ("php", "php")


# ---------- Patch 5: detektor "menyuruh user jalankan perintah" ----------

def test_ask_user_run_regex():
    from joki.cli import _ASK_RUN_RE, _SHELLISH_RE
    txt = "Silakan jalankan perintah berikut:\n```bash\nphp artisan config:clear\n```"
    assert _ASK_RUN_RE.search(txt) and _SHELLISH_RE.search(txt)
    txt2 = "Coba jalankan: systemctl restart nginx"
    assert _ASK_RUN_RE.search(txt2) and _SHELLISH_RE.search(txt2)
    txt3 = "Please run the following: curl http://localhost/x"
    assert _ASK_RUN_RE.search(txt3) and _SHELLISH_RE.search(txt3)
    # Teks biasa tanpa perintah tidak kena.
    assert _ASK_RUN_RE.search("Sudah selesai, semua aman.") is None


def test_agent_loop_nudges_ask_user(tmp_path, monkeypatch):
    """Model menyuruh user jalankan perintah → dinudge agar pakai run_command."""
    import joki.cli as c
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def _llm(msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant",
                    "content": "Silakan jalankan perintah berikut:\n```bash\nphp artisan config:clear\n```"}
        if calls["n"] == 2:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {
                        "name": "run_command",
                        "arguments": json.dumps({"cmd": "echo bersih"})}}]}
        return {"role": "assistant", "content": "Sudah dijalankan sendiri."}

    monkeypatch.setattr(c, "call_llm", _llm)
    c._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "perbaiki error login gtd"}]
    c.agent_loop(msgs)
    assert any("AKSI SENDIRI" in str(m.get("content", "")) for m in msgs)
    assert calls["n"] >= 3
