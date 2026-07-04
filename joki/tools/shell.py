import os
import sys
import json
import subprocess
import sqlite3
import re
import time
import random
import base64
import socket
import urllib
import csv
import platform
import ssl
from pathlib import Path
from difflib import unified_diff
from datetime import datetime
import httpx
from duckduckgo_search import DDGS
from joki.state import *
from joki.utils import *
from joki.display import _numbered, _Spinner


def _get_shell():
    """Start or return the persistent shell process (bash)."""
    global _PERSISTENT_SHELL
    if _PERSISTENT_SHELL is not None:
        poll = _PERSISTENT_SHELL.poll()
        if poll is None:
            return _PERSISTENT_SHELL
        _PERSISTENT_SHELL = None
    try:
        _PERSISTENT_SHELL = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0
        )
    except FileNotFoundError:
        try:
            _PERSISTENT_SHELL = subprocess.Popen(
                ["sh"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
        except Exception:
            return None
    return _PERSISTENT_SHELL


def _close_shell():
    """Kill the persistent shell process if running."""
    global _PERSISTENT_SHELL
    if _PERSISTENT_SHELL is not None:
        try:
            _PERSISTENT_SHELL.terminate()
            _PERSISTENT_SHELL.wait(timeout=3)
        except Exception:
            try:
                _PERSISTENT_SHELL.kill()
            except Exception:
                pass
        _PERSISTENT_SHELL = None


def _shell_execute(cmd, timeout=60):
    """Execute command in the persistent shell.
    Returns (stdout+stderr) string.
    """
    shell = _get_shell()
    if shell is None:
        return "[ERROR] Tidak bisa memulai persistent shell."

    end_marker = f"__SHELL_END_{os.getpid()}_{time.time_ns()}__"

    full_cmd = f" ( {cmd} ) 2>&1; echo '{end_marker}'"

    with _SHELL_LOCK:
        try:
            shell.stdin.write(full_cmd + "\n")
            shell.stdin.flush()
        except Exception as e:
            _close_shell()
            return f"[ERROR] Gagal menulis ke shell: {e}"

        output = []
        start = time.time()
        while True:
            if _joki_cancel.is_set():
                return "[CANCELLED]"
            elapsed = time.time() - start
            if elapsed > timeout:
                _close_shell()
                return f"[ERROR] Command timeout ({timeout}s). Shell di-restart."
            try:
                line = shell.stdout.readline()
                if not line:
                    _close_shell()
                    return "[ERROR] Shell process mati."
                if line.strip() == end_marker:
                    break
                output.append(line)
            except (Exception, KeyboardInterrupt):
                _close_shell()
                return "[ERROR] Gagal membaca output shell."

    return "".join(output).rstrip("\n")

# ============================================================
# MULTI-MODEL SUPPORT
# ============================================================


def handle_run_command(args):
    cmd = args["cmd"].strip()
    if not _confirm_dangerous(cmd):
        return "Dibatalkan oleh user."
    sudo_password = None
    actual_cmd = cmd
    use_sudo = False

    if cmd.startswith("sudo ") or (
            os.name == 'nt' and cmd.startswith("runas ")):
        use_sudo = True
        sudo_password = _prompt_sudo()
        if sudo_password:
            prefix = "sudo " if cmd.startswith("sudo ") else "runas "
            actual_cmd = cmd[len(prefix):].lstrip()

    if use_sudo and sudo_password:
        with _Spinner("Menjalankan perintah"):
            result = _run_elevated(actual_cmd, sudo_password)
        output = result.stdout + result.stderr
        return output or "(no output)"
    else:
        with _Spinner("Menjalankan perintah"):
            output = _shell_execute(cmd)
            return output or "(no output)"


def handle_service_control(args):
    svc = args["service"]
    act = args["action"]
    is_macos = sys.platform == 'darwin'
    if act == "status":
        with _Spinner(f"{act} {svc}"):
            if os.name == 'nt':
                r = subprocess.run(
                    f"sc query {svc}", shell=True,
                    capture_output=True, text=True, timeout=30
                )
            elif is_macos:
                r = subprocess.run(
                    f"launchctl list | grep -i {svc} || launchctl print system/{svc} 2>/dev/null || echo 'Service {svc} tidak ditemukan'",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30)
            else:
                r = subprocess.run(
                    f"systemctl status {svc} --no-pager -l", shell=True,
                    capture_output=True, text=True, timeout=30
                )
    else:
        sudo_password = _prompt_sudo()
        if os.name == 'nt':
            actual_cmd = f"net {act} {svc}"
        elif is_macos:
            if act == "enable":
                actual_cmd = f"launchctl load -w /System/Library/LaunchDaemons/{svc}.plist 2>/dev/null || launchctl enable system/{svc}"
            elif act == "disable":
                actual_cmd = f"launchctl unload -w /System/Library/LaunchDaemons/{svc}.plist 2>/dev/null || launchctl disable system/{svc}"
            elif act == "restart":
                actual_cmd = f"launchctl kickstart -k system/{svc} 2>/dev/null || (launchctl stop {svc} 2>/dev/null; sleep 1; launchctl start {svc} 2>/dev/null)"
            else:
                actual_cmd = f"launchctl {act} {svc}"
        else:
            actual_cmd = f"systemctl {act} {svc}"
        if sudo_password:
            with _Spinner(f"{act} {svc}"):
                r = _run_elevated(actual_cmd, sudo_password)
        else:
            cmd = f"sudo {actual_cmd}" if sudo_password != "__ROOT__" else actual_cmd
            with _Spinner(f"{act} {svc}"):
                r = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30)
    return (r.stdout or r.stderr).strip() or f"OK: {act} {svc}"


def handle_package_check(args):
    app = args["app"]
    # Check via which, dpkg, rpm, etc.
    checks = [
        f"which {app} 2>/dev/null",
        f"command -v {app} 2>/dev/null",
        f"dpkg -l {app} 2>/dev/null | grep '^ii'",
        f"rpm -q {app} 2>/dev/null"
    ]
    for c in checks:
        r = subprocess.run(
            c,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5)
        if r.stdout.strip():
            return f"INSTALLED: {r.stdout.strip()}"
    return f"NOT INSTALLED: {app} tidak ditemukan di system"


def handle_test_and_fix(args):
    try:
        with _Spinner("Mengetes"):
            r = subprocess.run(
                args["cmd"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=60)
        output = r.stdout + r.stderr
        if r.returncode != 0:
            return f"FAILED (exit code {r.returncode})\n{output}"
        return f"SUCCESS\n{output}"
    except subprocess.TimeoutExpired:
        return "FAILED (timeout)"


def handle_sandbox_run(args):
    code = args["code"]
    interpreter = args.get("interpreter", "auto")
    timeout = min(args.get("timeout", 15), 60)
    import tempfile
    import uuid
    sandbox_dir = os.path.join(
        tempfile.gettempdir(),
        f"joki_sandbox_{uuid.uuid4().hex[:8]}")
    os.makedirs(sandbox_dir, exist_ok=True)

    files_raw = args.get("files", "")
    if files_raw:
        for entry in files_raw.split("|"):
            if "=" in entry:
                fpath, fcontent = entry.split("=", 1)
                fdest = os.path.join(sandbox_dir, fpath.strip())
                os.makedirs(os.path.dirname(fdest), exist_ok=True)
                with open(fdest, "w") as f:
                    f.write(fcontent)

    script_path = os.path.join(sandbox_dir, "script")
    ext_map = {
        "python3": ".py",
        "node": ".js",
        "bash": ".sh",
        "sh": ".sh",
        "auto": ""}

    if interpreter == "auto":
        if code.startswith("#!"):
            interp_cmd = code.splitlines()[0].lstrip("#!").strip()
            interpreter = "bash" if "bash" in interp_cmd or "sh" in interp_cmd else "python3" if "python" in interp_cmd else "node" if "node" in interp_cmd else "bash"
        elif any(kw in code for kw in ["import ", "def ", "class ", "print("]):
            interpreter = "python3"
        elif any(kw in code for kw in ["require(", "module.exports", "console.log"]):
            interpreter = "node"
        else:
            interpreter = "bash"

    ext = ext_map.get(interpreter, "")
    script_path = os.path.join(sandbox_dir, f"script{ext}")
    with open(script_path, "w") as f:
        f.write(code)
    os.chmod(script_path, 0o755)

    try:
        r = subprocess.run(
            [interpreter, script_path] if interpreter in ("python3", "node") else ["bash", script_path],
            capture_output=True, text=True, timeout=timeout, cwd=sandbox_dir
        )
        output = r.stdout + r.stderr
        if not output.strip():
            output = "(no output)"
        status = "SUCCESS" if r.returncode == 0 else f"FAILED (exit {r.returncode})"
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return f"[SANDBOX] {status}\n{output.strip()}"
    except subprocess.TimeoutExpired:
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return f"[SANDBOX] TIMEOUT (>{timeout}s)"
    except Exception as e:
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return f"[SANDBOX] Error: {e}"


def handle_predict_command(args):
    cmd = args["cmd"]
    risks = []
    dangerous_patterns = [
        (r"\brm\s+-rf\b", "Menghapus file/direktori secara paksa (rm -rf) — data bisa hilang permanen"),
        (r"\bmv\s+", "Memindahkan file — bisa timpa file tujuan"),
        (r"\bdd\b", "Low-level disk operation — bisa merusak partisi jika salah"),
        (r"\bmkfs|mkfs\.|fdisk|parted", "Operasi partisi/format — bisa menghapus seluruh data"),
        (r"\bchmod\s+777", "Memberi izin akses penuh ke semua user — risiko keamanan"),
        (r"\bchown\b", "Mengubah kepemilikan file — bisa menyebabkan akses error"),
        (r":(){ :\|:& };:", "Fork bomb — bisa crash sistem"),
        (r">\s*/dev/", "Menulis langsung ke device — bisa merusak sistem"),
        (r"wget|curl.*\|.*sh", "Download dan pipe ke shell — risiko malware"),
        (r"sudo", "Menjalankan dengan hak akses root"),
        (r"apt install|apt-get install|pip install|npm install", "Menginstall package baru"),
        (r"systemctl (stop|disable|mask)", "Menghentikan/menonaktifkan service sistem"),
        (r"DROP TABLE|DELETE FROM|TRUNCATE", "Operasi database destruktif"),
        (r">\s+\S+\.(json|txt|py|js|yaml|conf|ini)", "Menimpa isi file (write)"),
    ]
    for pattern, desc in dangerous_patterns:
        if re.search(pattern, cmd, re.IGNORECASE):
            risks.append(f"  ⚠ {desc}")
    if not risks:
        risks.append("  ✓ Tidak terdeteksi pola berbahaya")
    return f"Analisa perintah: `{cmd[:200]}`\n" + "\n".join(risks)
