import os, sys, subprocess, re, time, select, pty, threading
from joki.state import *
from joki.utils import *
from joki.display import _IS_TTY


_HAS_PYTE = False
try:
    import pyte
    _HAS_PYTE = True
except ImportError:
    pass


def _get_shell():
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
            stderr=subprocess.PIPE, text=True, bufsize=0)
    except FileNotFoundError:
        try:
            _PERSISTENT_SHELL = subprocess.Popen(
                ["sh"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=0)
        except Exception:
            return None
    return _PERSISTENT_SHELL


def _close_shell():
    global _PERSISTENT_SHELL
    if _PERSISTENT_SHELL is not None:
        try:
            _PERSISTENT_SHELL.terminate()
            _PERSISTENT_SHELL.wait(timeout=3)
        except Exception:
            try:
                _PERSISTENT_SHELL.kill()
            except Exception as e:
                _console.print(f"[dim]Warning: Gagal kill shell: {e}[/dim]")
        _PERSISTENT_SHELL = None
    _close_pty_session()


def _close_pty_session():
    global _PTY_SESSION
    with _PTY_LOCK:
        if _PTY_SESSION is not None:
            try:
                os.close(_PTY_SESSION["master_fd"])
            except Exception as e:
                _console.print(f"[dim]Warning: Gagal close PTY fd: {e}[/dim]")
            try:
                _PTY_SESSION["proc"].terminate()
            except Exception as e:
                _console.print(f"[dim]Warning: Gagal terminate PTY: {e}[/dim]")
            _PTY_SESSION = None


def _get_pty_session():
    global _PTY_SESSION
    with _PTY_LOCK:
        if _PTY_SESSION is not None:
            p = _PTY_SESSION["proc"].poll()
            if p is None:
                return _PTY_SESSION
            _close_pty_session()

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True)
        os.close(slave_fd)
        if _HAS_PYTE:
            screen = pyte.Screen(80, 2000)
            stream = pyte.Stream(screen)
        else:
            screen = stream = None
        _PTY_SESSION = {
            "master_fd": master_fd, "proc": proc,
            "screen": screen, "stream": stream,
            "buf": b"", "lock": threading.Lock()}

        # Initialize terminal for clean command output
        os.write(master_fd, b"stty -echo 2>/dev/null\nPS1=\nPROMPT_COMMAND=\nHISTFILE=/dev/null\n")
        time.sleep(0.3)
        try:
            while select.select([master_fd], [], [], 0.05)[0]:
                if not os.read(master_fd, 65536):
                    break
        except (BlockingIOError, OSError):
            pass
        return _PTY_SESSION


def _shell_execute(cmd, timeout=60):
    session = _get_pty_session()
    if session is None:
        return "[ERROR] Tidak bisa memulai PTY session."

    master_fd = session["master_fd"]
    screen = session["screen"]
    stream = session["stream"]

    end_marker = f"__SHELL_END_{os.getpid()}_{time.time_ns()}__"

    with _SHELL_LOCK:
        if screen:
            screen.reset()

        try:
            os.write(master_fd, f"{cmd}\necho {end_marker}\n".encode())
        except Exception as e:
            _close_shell()
            return f"[ERROR] Gagal menulis ke PTY: {e}"

        raw_buf = ""
        start = time.time()

        while True:
            if _joki_cancel.is_set():
                return "[CANCELLED]"
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                _close_shell()
                return f"[ERROR] Command timeout ({timeout}s)."

            r, _, _ = select.select([master_fd], [], [], min(remaining, 0.1))
            if not r:
                continue

            try:
                chunk = os.read(master_fd, 65536)
                if not chunk:
                    _close_shell()
                    return "[ERROR] PTY process died."

                decoded = chunk.decode(errors='replace')

                if stream and screen:
                    stream.feed(decoded)
                    display = "\n".join(line.rstrip() for line in screen.display).strip()
                    if end_marker in display:
                        idx = display.index(end_marker)
                        return display[:idx].rstrip("\n")
                else:
                    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07', '', decoded)
                    raw_buf += clean
                    if end_marker in raw_buf:
                        idx = raw_buf.index(end_marker)
                        return raw_buf[:idx].replace('\r\n', '\n').replace('\r', '').rstrip("\n")

            except (Exception, KeyboardInterrupt):
                _close_shell()
                return "[ERROR] Gagal membaca output PTY."

    return ""


def _run_with_pty(cmd, timeout=60):
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["bash", "-c", cmd],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True)
        os.close(slave_fd)

        pyte_screen = pyte_stream = None
        if _HAS_PYTE:
            pyte_screen = pyte.Screen(80, 2000)
            pyte_stream = pyte.Stream(pyte_screen)

        output = []
        start = time.time()
        while True:
            if _joki_cancel.is_set():
                proc.terminate()
                return "[CANCELLED]"
            if time.time() - start > timeout:
                proc.terminate()
                return f"[ERROR] Command timeout ({timeout}s)"
            r, _, _ = select.select([master_fd], [], [], 0.05)
            if r:
                try:
                    chunk = os.read(master_fd, 65536)
                    if not chunk:
                        break
                    decoded = chunk.decode(errors='replace')
                    output.append(decoded)
                    if pyte_stream:
                        pyte_stream.feed(decoded)
                    if _IS_TTY:
                        sys.stdout.write(decoded)
                        sys.stdout.flush()
                except OSError:
                    break
            else:
                if proc.poll() is not None:
                    break
        proc.wait()

        raw = "".join(output).strip()
        if _HAS_PYTE:
            clean_lines = pyte_screen.display
            clean = "\n".join(line.rstrip() for line in clean_lines).strip()
            return clean
        return raw
    finally:
        try:
            os.close(master_fd)
        except OSError:
            _console.print("[dim]Warning: Gagal close PTY master fd[/dim]")


# ============================================================
# MULTI-MODEL SUPPORT
# ============================================================


def _sync_cwd_from_shell():
    """Sync Python's CWD with the persistent PTY shell's CWD."""
    session = _get_pty_session()
    if session is None:
        return
    master_fd = session["master_fd"]

    try:
        with _SHELL_LOCK:
            marker = f"__PWD_{os.getpid()}_{time.time_ns()}__"
            os.write(master_fd, f"pwd\necho {marker}\n".encode())
            buf = ""
            start = time.time()
            while time.time() - start < 3:
                r, _, _ = select.select([master_fd], [], [], 0.05)
                if r:
                    chunk = os.read(master_fd, 65536)
                    if not chunk:
                        break
                    buf += chunk.decode(errors='replace').replace('\r\n', '\n').replace('\r', '')
                    if marker in buf:
                        lines = buf.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line.startswith('/') and os.path.isdir(line):
                                os.chdir(line)
                                return
    except Exception:
        _console.print("[dim]Warning: Gagal sync CWD dari PTY[/dim]")


def _is_elevated_cmd(cmd):
    m = re.match(r'^\s*(sudo|runas)\s+', cmd)
    if m:
        return m.group(1), cmd[m.end():]
    return None, cmd


def handle_run_command(args):
    cmd = args.get("cmd", "").strip()
    if not cmd:
        return "Error: Parameter 'cmd' wajib diisi. Contoh: run_command(cmd=\"ls -la\")"

    timeout_ms = args.get("timeout", 120000)
    if not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
        timeout_ms = 120000
    timeout_s = min(int(timeout_ms) / 1000, 600)

    cwd = args.get("cwd", "")
    is_interactive = args.get("isInteractive", False)

    if cwd:
        cwd = os.path.expanduser(cwd)
        if not os.path.isdir(cwd):
            return f"Error: Direktori tidak ditemukan: {cwd}"
        cmd = f"cd {cwd} && {cmd}"

    if _is_elevated_cmd(cmd)[0]:
        password = _prompt_sudo()
        if password is None:
            return "[CANCELLED] Autentikasi administrator dibatalkan."

    if is_interactive:
        output = _run_with_pty(cmd, timeout=timeout_s)
    else:
        output = _shell_execute(cmd, timeout=timeout_s)
        _sync_cwd_from_shell()

    return output or "(no output)"


def handle_service_control(args):
    svc = args.get("service", "")
    act = args.get("action", "")
    if not svc:
        return "Error: Parameter 'service' wajib diisi. Contoh: service_control(service=\"nginx\", action=\"status\")"
    if not act:
        return "Error: Parameter 'action' wajib diisi. Pilihan: start, stop, restart, status. Contoh: service_control(service=\"nginx\", action=\"status\")"
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
                return _run_elevated(actual_cmd, sudo_password, timeout=30)
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
    app = args.get("app", "")
    if not app:
        return "Error: Parameter 'app' wajib diisi. Contoh: package_check(app=\"nginx\")"
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
