import threading
import time

import pytest

from joki import cli

MONITOR_NAME = "joki-cancel-monitor"


def _alive_monitors():
    return [t for t in threading.enumerate() if t.name == MONITOR_NAME]


def _assert_monitor_gone():
    for _ in range(20):
        if not _alive_monitors():
            return
        time.sleep(0.05)
    pytest.fail(f"Thread {MONITOR_NAME} masih hidup setelah agent_loop selesai")


def test_agent_loop_exception_stops_monitor(tmp_path, monkeypatch):
    def boom(messages):
        raise RuntimeError("simulated network error")

    monkeypatch.setattr(cli, "call_llm", boom)
    cli._joki_cancel.clear()
    monkeypatch.chdir(tmp_path)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "tes"},
    ]
    with pytest.raises(RuntimeError):
        cli.agent_loop(msgs)
    _assert_monitor_gone()


def test_agent_loop_normal_exit_stops_monitor(tmp_path, monkeypatch):
    def respond(messages):
        return {"role": "assistant", "content": "Tugas selesai."}

    monkeypatch.setattr(cli, "call_llm", respond)
    cli._joki_cancel.clear()
    monkeypatch.chdir(tmp_path)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "tes"},
    ]
    cli.agent_loop(msgs)
    _assert_monitor_gone()


def test_run_auto_test_detaches_stdin(monkeypatch):
    """Regresi: subprocess auto-test (dry test) tidak boleh mewarisi tty user,
    supaya tidak berebut baca stdin dengan cancel-monitor."""
    import subprocess

    captured = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        captured["stdin"] = kw.get("stdin")
        return _Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._run_auto_test("echo hi")
    assert captured.get("stdin") is subprocess.DEVNULL


def test_monitor_stops_after_subprocess_stdin_race():
    """Regresi: cancel-monitor tidak boleh jadi zombie (tersangkut di os.read)
    ketika subprocess dry-test ikut membaca tty — jika zombie, keystroke prompt
    `joki>` bakal ditelan dan prompt tak bisa diketik."""
    import os
    import pty
    import select
    import sys

    child = r"""
import os, sys, time, subprocess, threading
sys.path.insert(0, %(repo)r)
from joki.state import _start_cancel_monitor, _stop_cancel_monitor

def alive():
    return [t for t in threading.enumerate() if t.name == "joki-cancel-monitor"]

_start_cancel_monitor()
time.sleep(0.3)
# subprocess yang mewarisi stdin (fd 0 = tty) dan mencoba membaca — persis
# seperti subprocess dry-test sebelum fix stdin=DEVNULL.
p = subprocess.Popen(["bash", "-c", "read -r x || true"],
                     stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print("READY", flush=True)
time.sleep(1.5)
try:
    p.kill()
except Exception:
    pass
t0 = time.time()
_stop_cancel_monitor()
gone = not alive()
print("GONE=%s elapsed=%.2f" % (gone, time.time() - t0), flush=True)
print("END", flush=True)
"""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    code = child.replace("%(repo)r", repr(repo))
    pid, master_fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execv(sys.executable, [sys.executable, "-c", code])
        os._exit(1)
    buf = b""
    injected = False
    end = time.time() + 20
    try:
        while time.time() < end:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
            if b"READY" in buf and not injected:
                time.sleep(0.2)
                os.write(master_fd, b"X")
                injected = True
            if b"END" in buf:
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    text = buf.decode(errors="replace")
    assert "GONE=True" in text, f"Monitor jadi zombie (menelan keystroke prompt):\n{text}"


def test_monitor_keeps_stdout_blocking():
    """Regresi: monitor yang membaca stdin non-blocking TIDAK boleh membuat
    stdout ikut non-blocking. Di terminal asli, fd 0 dan fd 1 adalah dup dari
    tty yang sama (satu open file description), jadi set O_NONBLOCK di fd 0 akan
    bikin rich display gagal menulis — BlockingIOError: EAGAIN saat buffer tty
    penuh."""
    import os
    import pty
    import select
    import sys

    child = r"""
import os, sys, time, threading
sys.path.insert(0, %(repo)r)
from joki.state import _start_cancel_monitor, _stop_cancel_monitor

print("START", flush=True)
time.sleep(0.2)
_start_cancel_monitor()
# Selagi monitor aktif, stdout (fd 1) HARUS tetap blocking.
blocking = os.get_blocking(1)
# Tulis payload besar selagi parent membacanya — tanpa fix ini, payload
# cukup besar untuk mengisi buffer tty akan memunculkan BlockingIOError.
payload = b"A" * (512 * 1024)
err = "none"
try:
    written = 0
    while written < len(payload):
        written += os.write(1, payload[written:written + 65536])
except BlockingIOError as exc:
    err = "BlockingIOError"
except Exception as exc:
    err = type(exc).__name__
print("STDOUT_BLOCKING=%s WRITE_ERR=%s" % (blocking, err), flush=True)
_stop_cancel_monitor()
print("END", flush=True)
"""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    code = child.replace("%(repo)r", repr(repo))
    pid, master_fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execv(sys.executable, [sys.executable, "-c", code])
        os._exit(1)
    buf = b""
    end = time.time() + 20
    try:
        while time.time() < end:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
            if b"END" in buf:
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    text = buf.decode(errors="replace")
    assert "STDOUT_BLOCKING=True" in text, f"stdout ikut non-blocking:\n{text}"
    assert "WRITE_ERR=none" in text, f"write stdout gagal:\n{text}"


# ============================================================
# _JokiCompleter (autocomplete slash-command)
# ============================================================

def _complete(text):
    from prompt_toolkit.document import Document

    from joki.cli import _JokiCompleter

    doc = Document(text)
    return [(c.text, str(c.display).split("  —  ")[0]) for c in _JokiCompleter().get_completions(doc, None)]  # type: ignore[arg-type]


def test_completer_shows_all_commands_on_slash():
    res = _complete("/")
    texts = [t for t, _ in res]
    assert "/model " in texts
    assert "/keluar " in texts


def test_completer_filters_by_prefix():
    res = _complete("/mo")
    assert [t for t, _ in res] == ["/model "]


def test_completer_model_suboptions():
    from joki.config import _MODELS

    res = _complete("/model ")
    keys = [t for t, _ in res]
    assert keys and all(not t.startswith("/") for t in keys)
    assert sorted(keys) == sorted(_MODELS.keys())


def test_completer_model_filtered_by_word():
    from joki.config import _MODELS

    first = min(_MODELS.keys())
    res = _complete(f"/model {first[:4]}")
    keys = [t for t, _ in res]
    assert first in keys
    assert all(k.startswith(first[:4]) for k in keys)


def test_completer_non_slash_falls_back_to_path():
    res = _complete("jo")
    # path completer menyediakan kandidat (folder joki/ ada di cwd)
    assert res
    assert any(t == "ki" for t, _ in res)


def test_completer_unknown_command_empty():
    assert _complete("/foobar") == []
