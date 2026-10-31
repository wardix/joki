import os
import re
import select
import subprocess
import sys
import threading
import time
from typing import Any

from rich.markup import escape

import joki.state as _joki_state
from joki.display import _IS_TTY, _Spinner
from joki.state import *
from joki.utils import *

_HAS_PTY = False
_pty_mod: Any = None  # type: ignore[assignment]
try:
    import pty as _pty_mod
    _HAS_PTY = True
except ImportError:
    pass

_HAS_PYTE = False
pyte: Any = None  # type: ignore[assignment]
try:
    import pyte
    _HAS_PYTE = True
except ImportError:
    pass

_DEFAULT_IDLE_TIMEOUT_MS = 60_000
_STUCK_GRACE_S = 5


def _get_shell():
    global _PERSISTENT_SHELL
    with _SHELL_LOCK:
        if _PERSISTENT_SHELL is not None:
            poll = _PERSISTENT_SHELL.poll()
            if poll is None:
                return _PERSISTENT_SHELL
            _PERSISTENT_SHELL = None

        shells = [["bash", "--norc", "--noprofile"], ["sh"]]

        for shell_cmd in shells:
            try:
                _PERSISTENT_SHELL = subprocess.Popen(
                    shell_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, bufsize=0)
                return _PERSISTENT_SHELL
            except FileNotFoundError:
                continue
    return None


def _close_shell():
    global _PERSISTENT_SHELL
    with _SHELL_LOCK:
        if _PERSISTENT_SHELL is not None:
            try:
                _PERSISTENT_SHELL.terminate()
                _PERSISTENT_SHELL.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    _PERSISTENT_SHELL.kill()
                except Exception as e:  # noqa: BLE001
                    _console.print(f"[dim]Warning: Gagal kill shell: {escape(str(e))}[/dim]")
            _PERSISTENT_SHELL = None
    _close_pty_session()


def _close_pty_session():
    global _PTY_SESSION
    with _PTY_LOCK:
        if _PTY_SESSION is not None:
            try:
                os.close(_PTY_SESSION["master_fd"])
            except Exception as e:  # noqa: BLE001
                _console.print(f"[dim]Warning: Gagal close PTY fd: {escape(str(e))}[/dim]")
            try:
                _PTY_SESSION["proc"].terminate()
            except Exception as e:  # noqa: BLE001
                _console.print(f"[dim]Warning: Gagal terminate PTY: {escape(str(e))}[/dim]")
            _PTY_SESSION = None


def _get_pty_session():
    global _PTY_SESSION
    with _PTY_LOCK:
        if _PTY_SESSION is not None:
            p = _PTY_SESSION["proc"].poll()
            if p is None:
                return _PTY_SESSION
            _close_pty_session()

        if not _HAS_PTY:
            return None

        master_fd, slave_fd = _pty_mod.openpty()  # type: ignore[union-attr]
        shell_cmd = ["bash", "--norc", "--noprofile"]
        proc = subprocess.Popen(
            shell_cmd,
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
        init_cmd = b"stty -echo 2>/dev/null\nPS1=\nPROMPT_COMMAND=\nHISTFILE=/dev/null\n"
        os.write(master_fd, init_cmd)
        time.sleep(0.3)
        try:
            while select.select([master_fd], [], [], 0.05)[0]:
                if not os.read(master_fd, 65536):
                    break
        except (BlockingIOError, OSError):
            pass
        # Probe kesiapan shell: tunggu sampai bash benar-benar siap menerima
        # perintah (output pertama baru muncul ~0.3s setelah spawn).
        ready_marker = f"__JOKI_READY_{time.time_ns()}__"
        os.write(master_fd, f"printf '{ready_marker}\\n'\n".encode())
        ready_deadline = time.time() + 3
        ready_buf = b""
        while time.time() < ready_deadline:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                ready_buf += chunk
                if ready_marker.encode() in ready_buf:
                    break
            else:
                os.write(master_fd, f"printf '{ready_marker}\\n'\n".encode())
        return _PTY_SESSION


def _stuck_message(idle_s, partial="", elapsed_s=None, timeout_s=None):
    """Pesan saat command dianggap stuck (diam tanpa output terlalu lama)."""
    elapsed = f", sudah jalan {elapsed_s:.0f}s" if elapsed_s else ""
    cap = f" (batas total {timeout_s:.0f}s)" if timeout_s else ""
    msg = (
        f"[STUCK] Command tidak menghasilkan output selama {idle_s:.0f}s{elapsed} — "
        "kemungkinan menunggu input interaktif yang Joki tidak bisa berikan.\n"
    )
    if partial and partial.strip():
        msg += f"Output parsial:\n{partial.strip()[:2000]}\n"
    msg += (
        "Saran: jalankan ulang dengan input non-interaktif "
        "(misal: echo '' | cmd, atau flag --yes/-y), atau naikkan idle_timeout (ms) "
        "jika perintah memang lama diam secara wajar."
    )
    if timeout_s:
        msg += f"\nCatatan: total timeout {timeout_s:.0f}s{cap}."
    return msg


_LIVE_FLUSH_INTERVAL = 0.3


def _clean_stream_text(text):
    """Strip ANSI escape & normalisasi CRLF jadi newline (bare CR dibiarkan
    supaya progress bar berbasis \\r bisa di-collapse per baris)."""
    clean = re.sub(r'\x1b\[[?0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07', '', text)
    return clean.replace('\r\n', '\n')


def _stream_lines(text, marker):
    """Ubah partial output jadi daftar baris bersih.
    Baris yang memuat `marker` (echo penutup perintah) dibuang."""
    lines = []
    for ln in _clean_stream_text(text).split('\n'):
        if marker and marker in ln:
            ln = ln.split(marker, 1)[0].strip()
            if ln in ("", "echo", "printf"):
                continue
        if '\r' in ln:
            ln = ln.rsplit('\r', 1)[-1]
        if not ln.strip():
            continue
        lines.append(ln)
    return lines


def _flush_live_stream(pending, last_flush, marker):
    """Kirim pending partial output ke queue TUI bila sudah waktunya flush
    (throttle biar nggak ngebanjiri render). Mengembalikan (pending_baru, last_flush_baru)."""
    if not (_joki_state._TUI_ACTIVE and _joki_state._TUI_QUEUE is not None):
        return "", time.time()
    if (time.time() - last_flush) < _LIVE_FLUSH_INTERVAL:
        return pending, last_flush
    lines = _stream_lines(pending, marker)
    text = "\n".join(lines).strip("\n")
    if text.strip():
        _joki_state._bridge_send(_joki_state._RENDER_KIND, ((text,), None, False, "\n", "\n"))
    return "", time.time()


def _run_popen_with_idle(cmd, timeout, idle_timeout=0):
    """Jalankan command tanpa PTY, dengan deteksi idle/stuck (fallback)."""
    try:
        proc = subprocess.Popen(
            cmd, shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as e:
        return f"[ERROR] {e}"

    out_parts = []
    last_output = time.time()
    start = time.time()
    live_pending = ""
    last_flush = time.time()
    try:
        while True:
            if _joki_cancel.is_set():
                proc.kill()
                return "[CANCELLED]"
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                proc.kill()
                return f"[ERROR] Command timeout ({timeout}s)."
            
            stdout = proc.stdout
            if stdout is None:
                proc.kill()
                return "[ERROR] stdout tidak tersedia."
            
            r, _, _ = select.select([stdout], [], [], min(remaining, 0.1))
            if r:
                try:
                    chunk = os.read(stdout.fileno(), 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    decoded = chunk.decode(errors="replace")
                    out_parts.append(decoded)
                    live_pending, last_flush = _flush_live_stream(live_pending + decoded, last_flush, None)
                    last_output = time.time()
                elif proc.poll() is not None:
                    break
            elif proc.poll() is not None:
                break
            else:
                live_pending, last_flush = _flush_live_stream(live_pending, last_flush, None)
                if idle_timeout and (time.time() - last_output) > idle_timeout:
                    proc.kill()
                    return _stuck_message(
                        idle_timeout, "".join(out_parts),
                        elapsed_s=time.time() - start, timeout_s=timeout,
                    )
    except OSError as e:
        proc.kill()
        return f"[ERROR] {e}"
    return "".join(out_parts).strip() or "(no output)"


def _shell_execute(cmd, timeout=60, idle_timeout=0):
    session = _get_pty_session()
    if session is None:
        return _run_popen_with_idle(cmd, timeout, idle_timeout)

    master_fd = session["master_fd"]
    screen = session["screen"]
    stream = session["stream"]

    end_marker = f"__SHELL_END_{os.getpid()}_{time.time_ns()}__"

    with _SHELL_LOCK:
        if screen:
            screen.reset()

        try:
            os.write(master_fd, f"{cmd}\necho {end_marker}\n".encode())
        except Exception as e:  # noqa: BLE001
            _close_shell()
            return f"[ERROR] Gagal menulis ke PTY: {e}"

        raw_buf = ""
        start = time.time()
        last_output = time.time()
        stuck_tried = False
        live_pending = ""
        last_flush = time.time()

        def _current_display():
            if stream and screen:
                return "\n".join(line.rstrip() for line in screen.display).strip()
            return raw_buf.replace('\r\n', '\n').replace('\r', '').strip()

        while True:
            if _joki_cancel.is_set():
                _close_shell()
                return "[CANCELLED]"
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                _close_shell()
                return f"[ERROR] Command timeout ({timeout}s)."

            if idle_timeout and not stuck_tried and (time.time() - last_output) > idle_timeout:
                stuck_tried = True
                try:
                    os.write(master_fd, b"\x03\n")
                    os.write(master_fd, f"echo {end_marker}\n".encode())
                except OSError:
                    _close_shell()
                    return _stuck_message(
                        idle_timeout, _current_display(),
                        elapsed_s=time.time() - start, timeout_s=timeout,
                    )
                last_output = time.time()

            r, _, _ = select.select([master_fd], [], [], min(remaining, 0.05))
            if not r:
                if stuck_tried and (time.time() - last_output) > _STUCK_GRACE_S:
                    _close_shell()
                    return _stuck_message(
                        idle_timeout, _current_display(),
                        elapsed_s=time.time() - start, timeout_s=timeout,
                    )
                live_pending, last_flush = _flush_live_stream(live_pending, last_flush, end_marker)
                continue

            try:
                chunk = os.read(master_fd, 65536)
                if not chunk:
                    _close_shell()
                    return "[ERROR] PTY process died."

                if _joki_cancel.is_set():
                    _close_shell()
                    return "[CANCELLED]"

                last_output = time.time()
                decoded = chunk.decode(errors='replace')
                live_pending, last_flush = _flush_live_stream(live_pending + decoded, last_flush, end_marker)

                if stream and screen:
                    stream.feed(decoded)
                    display = "\n".join(line.rstrip() for line in screen.display).strip()
                    if end_marker in display:
                        idx = display.index(end_marker)
                        partial = display[:idx].rstrip("\n")
                        if stuck_tried:
                            return _stuck_message(
                                idle_timeout, partial,
                                elapsed_s=time.time() - start, timeout_s=timeout,
                            )
                        return partial
                else:
                    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07', '', decoded)
                    raw_buf += clean
                    if end_marker in raw_buf:
                        idx = raw_buf.index(end_marker)
                        partial = raw_buf[:idx].replace('\r\n', '\n').replace('\r', '').rstrip("\n")
                        if stuck_tried:
                            return _stuck_message(
                                idle_timeout, partial,
                                elapsed_s=time.time() - start, timeout_s=timeout,
                            )
                        return partial

            except (Exception, KeyboardInterrupt):  # noqa: BLE001
                _close_shell()
                return "[ERROR] Gagal membaca output PTY."

    return ""


def _run_with_pty(cmd, timeout=60, idle_timeout=0):
    if not _HAS_PTY:
        return _run_popen_with_idle(cmd, timeout, idle_timeout)

    master_fd, slave_fd = _pty_mod.openpty()  # type: ignore[union-attr]
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
        last_output = time.time()
        live_pending = ""
        last_flush = time.time()
        while True:
            if _joki_cancel.is_set():
                proc.terminate()
                proc.wait(timeout=5)
                return "[CANCELLED]"
            if time.time() - start > timeout:
                proc.terminate()
                proc.wait(timeout=5)
                return f"[ERROR] Command timeout ({timeout}s)"
            if idle_timeout and (time.time() - last_output) > idle_timeout:
                proc.terminate()
                proc.wait(timeout=5)
                return _stuck_message(
                    idle_timeout, "".join(output),
                    elapsed_s=time.time() - start, timeout_s=timeout,
                )
            r, _, _ = select.select([master_fd], [], [], 0.02)
            if r:
                try:
                    chunk = os.read(master_fd, 65536)
                    if not chunk:
                        break
                    last_output = time.time()
                    decoded = chunk.decode(errors='replace')
                    output.append(decoded)
                    if pyte_stream:
                        pyte_stream.feed(decoded)
                    if _IS_TTY and not _joki_state._TUI_ACTIVE:
                        sys.stdout.write(decoded)
                        sys.stdout.flush()
                    elif _joki_state._TUI_ACTIVE:
                        live_pending, last_flush = _flush_live_stream(live_pending + decoded, last_flush, None)
                except OSError:
                    break
            else:
                live_pending, last_flush = _flush_live_stream(live_pending, last_flush, None)
                if proc.poll() is not None:
                    break
        proc.wait()

        raw = "".join(output).strip()
        if _HAS_PYTE:
            clean_lines = pyte_screen.display  # type: ignore[union-attr]
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


_SHELL_BOUNDARY_ROOT = None


def _get_shell_boundary():
    """Root workspace untuk guard cwd.

    Lazy: dihitung saat perintah pertama lewat shell (setelah startup/resume
    chdir), bukan saat import — supaya `joki --resume` dari direktori lain
    tetap memakai cwd sesi yang benar.
    """
    global _SHELL_BOUNDARY_ROOT
    if _SHELL_BOUNDARY_ROOT is None:
        try:
            _SHELL_BOUNDARY_ROOT = os.path.realpath(os.getcwd())
        except Exception:  # noqa: BLE001
            _SHELL_BOUNDARY_ROOT = os.path.realpath(os.sep)
    return _SHELL_BOUNDARY_ROOT


def _sync_cwd_allowed(newpwd):
    """Terapkan cwd baru hanya bila DI DALAM workspace.

    Dulu `cd /tmp && ...` dari model memindahkan cwd proses joki secara
    permanen — indeks proyek, auto-test, dan path relatif ikut salah arah.
    Return True bila cwd diterapkan.
    """
    try:
        root = _get_shell_boundary()
        rp = os.path.realpath(newpwd)
    except Exception:  # noqa: BLE001
        return False
    inside = rp == root or root == os.sep or rp.startswith(root + os.sep)
    if not inside:
        return False
    try:
        os.chdir(newpwd)
        return True
    except OSError:
        return False


def _sync_cwd_from_shell():
    if not _HAS_PTY:
        return
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
                                if _sync_cwd_allowed(line):
                                    return
                                # Shell cd ke luar workspace — jangan seret
                                # proses joki ikut pindah; kembalikan shell.
                                root = _get_shell_boundary()
                                _console.print(
                                    f"[dim]cwd dijaga: '{line}' di luar workspace — "
                                    f"cwd sesi tetap {root}[/dim]"
                                )
                                try:
                                    os.write(master_fd, f"cd {root}\n".encode())
                                except Exception:  # noqa: BLE001, S110
                                    pass
                                return line
    except Exception:  # noqa: BLE001
        _console.print("[dim]Warning: Gagal sync CWD dari PTY[/dim]")


def _is_elevated_cmd(cmd):
    m = re.match(r'^\s*(sudo)\s+', cmd)
    if m:
        return m.group(1), cmd[m.end():]
    return None, cmd


_FILE_EDIT_PATTERNS = re.compile(
    r'(^|[|;])\s*'
    r'(sed\s+-i|echo\s+[^|;&]*\s>>?|awk\s+[^|;&]*\s>>?|printf\s+[^|;&]*\s>>?|cat\s+[^|;&]*\s>>?|'
    r'tee\s+\S+|cp\s+\S+\s+\S+|mv\s+\S+\s+\S+|truncate\s+[^|;&]*|'
    r'python3?\s+-c\s+[^|;&]*open\s*\(|perl\s+-i)',
    re.IGNORECASE
)


def _extract_write_targets(cmd):
    """Ekstrak path target write dari command (best-effort)."""
    targets = set()
    for m in re.finditer(r'[>»]\s*(\S+)', cmd):
        targets.add(m.group(1).strip("'\""))
    m = re.search(r'tee\s+(\S+)', cmd, re.IGNORECASE)
    if m:
        targets.add(m.group(1).strip("'\""))
    m = re.search(
        r'sed\s+(?:-[a-zA-Z]*i[a-zA-Z]*\s+)*\S+\s+([^\s|;&]+)\s*$',
        cmd, re.IGNORECASE)
    if m:
        targets.add(m.group(1).strip("'\""))
    return targets


_DESTRUCTIVE_PATTERNS = [
    (re.compile(r'\bDROP\s+(TABLE|DATABASE|SCHEMA)\b', re.IGNORECASE),
     "DROP TABLE/DATABASE — data dihapus permanen"),
    (re.compile(r'\bTRUNCATE\s+(TABLE|DATABASE|SCHEMA)\b', re.IGNORECASE),
     "TRUNCATE TABLE/DATABASE — data dihapus permanen"),
    (re.compile(r'\bdd\b[^\n;|&]*\bof=/dev/(sd|hd|nvme|mmcblk|sr|vd)'),
     "dd menulis langsung ke device/disk"),
    (re.compile(r'\bmkfs(?:\.\w+)?\b'),
     "mkfs — memformat partisi/disk"),
    (re.compile(r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}'),
     "fork bomb — bisa crash sistem"),
    (re.compile(r'\bchmod\s+(?:-[a-zA-Z]*[Rr][a-zA-Z]*\s+)*777\s+(/|/\*|/home|/root)'),
     "chmod -R 777 di root path"),
    (re.compile(r'\b(reboot|poweroff|halt)\b', re.IGNORECASE),
     "shutdown/reboot sistem"),
    (re.compile(r'\bshutdown\b\s+(?:-\S+\s+)*(now|\+\d+|[-+]?\d+)', re.IGNORECASE),
     "shutdown terjadwal sistem"),
]


def _has_destructive_rm(cmd):
    """Deteksi rm -rf pada path absolut/root/home (ireversibel)."""
    m = re.search(r'\brm\b', cmd)
    if not m:
        return False
    seg = re.split(r'[;|&]', cmd[m.end():])[0]
    tokens = seg.split()
    if not tokens:
        return False
    has_r = has_f = False
    i = 0
    while i < len(tokens) and tokens[i].startswith('-'):
        flag = tokens[i]
        if flag == '--recursive':
            has_r = True
        elif flag == '--force':
            has_f = True
        else:
            if 'r' in flag.lower():
                has_r = True
            if 'f' in flag.lower():
                has_f = True
        i += 1
    if not (has_r and has_f):
        return False
    for t in tokens[i:]:
        t = t.strip("'\"")
        if t.startswith(('/', '~')):
            return True
        if t in ('.', '..'):
            return True
    return False


def _requires_confirmation(cmd):
    if _has_destructive_rm(cmd):
        return True, "rm -rf pada path absolut/root/home — penghapusan permanen"
    for pattern, reason in _DESTRUCTIVE_PATTERNS:
        if pattern.search(cmd):
            return True, reason
    return False, ""


def handle_run_command(args):
    cmd = args.get("cmd", "").strip()
    if not cmd:
        return "Error: Parameter 'cmd' wajib diisi. Contoh: run_command(cmd=\"ls -la\")"
    
    if _FILE_EDIT_PATTERNS.search(cmd):
        for target in _extract_write_targets(cmd):
            if _is_protected_identity_path(target):
                return (
                    "[DILARANG] Command menulis ke file identitas sistem "
                    f"({target}). File seperti shadow/passwd/sudoers/SSH key "
                    "tidak boleh diubah lewat run_command."
                )
        return (
            "[BLOKIR] run_command tidak boleh dipakai untuk ngedit file. "
            "Gunakan edit_file untuk mengubah isi file — "
            "lebih aman, backup otomatis, dan gak perlu ribet quoting shell. "
            "Contoh: edit_file(path=..., old_text=..., new_text=...)"
        )

    # Gate destruktif: persetujuan manusia via cli (approve_once / sesi)
    # mengalahkan flag AI. Flag `confirmed=true` dari AI saja TIDAK cukup
    # bila dipanggil lewat agent_loop — di sana cli sudah meminta Y/N dulu.
    # Fallback langsung (tanpa cli, mis. test) tetap butuh confirmed=true.
    _human_ok = False
    try:
        if _joki_state.is_session_allowed("destructive_cmd") or _joki_state.consume_once_approval("run_command", args):
            _human_ok = True
    except Exception:  # noqa: BLE001
        _human_ok = False
    # Bersihkan sisa forgery AI bila ada (anti-bypass via args).
    try:
        args.pop(_joki_state._HUMAN_APPROVAL_KEY, None)
    except Exception:  # noqa: BLE001, S110
        pass
    if not _human_ok and args.get("confirmed") is not True:
        destructive, reason = _requires_confirmation(cmd)
        if destructive:
            return (
                "[KONFIRMASI] Perintah ini sangat destruktif dan ireversibel — "
                f"{reason}.\n"
                "Joki TIDAK menjalankannya tanpa konfirmasi eksplisit manusia (Y/N di terminal "
                "atau dialog Ya/Tidak di TUI). Persetujuan manusia cukup sekali — "
                "eksekusi langsung jalan tanpa perlu panggil ulang."
            )

    timeout_ms = args.get("timeout", 600000)
    if not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
        timeout_ms = 600000
    timeout_s = min(int(timeout_ms) // 1000, 600)

    idle_ms = args.get("idle_timeout", _DEFAULT_IDLE_TIMEOUT_MS)
    if not isinstance(idle_ms, (int, float)) or idle_ms < 0:
        idle_ms = _DEFAULT_IDLE_TIMEOUT_MS
    idle_timeout_s = min(int(idle_ms) // 1000, timeout_s)

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
        output = _run_with_pty(cmd, timeout=int(timeout_s), idle_timeout=int(idle_timeout_s))
    else:
        output = _shell_execute(cmd, timeout=int(timeout_s), idle_timeout=int(idle_timeout_s))
        _escaped = _sync_cwd_from_shell()
        if _escaped:
            output = (output or "") + (
                f"\n[cwd dijaga] '{_escaped}' di luar workspace; cwd sesi tetap "
                f"{_get_shell_boundary()}. Path relatif tetap dihitung dari workspace."
            )

    return output or "(no output)"


def handle_service_control(args):
    svc = args.get("service", "")
    act = args.get("action", "")
    if not svc:
        return "Error: Parameter 'service' wajib diisi. Contoh: service_control(service=\"nginx\", action=\"status\")"
    if not act:
        return "Error: Parameter 'action' wajib diisi. Pilihan: start, stop, restart, status. Contoh: service_control(service=\"nginx\", action=\"status\")"
    is_macos = sys.platform == 'darwin'

    if act in ("restart", "start", "reload"):
        _config_test_commands = {
            "apache2": "apachectl configtest",
            "httpd": "apachectl configtest",
            "nginx": "nginx -t",
            "sshd": "sshd -t",
            "postfix": "postfix check",
            "bind9": "named-checkconf",
            "named": "named-checkconf",
        }
        if svc in _config_test_commands:
            test_cmd = _config_test_commands[svc]
            try:
                tr = subprocess.run(test_cmd, shell=True, capture_output=True, text=True, timeout=15, check=False)
                if tr.returncode != 0:
                    return (f"[PRE-FLIGHT VALIDATION FAILED] Konfigurasi {svc} bermasalah:\n"
                            f"{tr.stdout}\n{tr.stderr}\n"
                            f"Perbaiki konfigurasi terlebih dahulu sebelum melakukan {act}.")
            except Exception:  # noqa: BLE001, S110
                pass

    with _Spinner(f"{act} {svc}"):
        if act == "status":
            if is_macos:
                r = subprocess.run(
                    f"launchctl list | grep -i {svc} || launchctl print system/{svc} 2>/dev/null || echo 'Service {svc} tidak ditemukan'",
                    shell=True, capture_output=True, text=True, timeout=30, check=False)
            else:
                r = subprocess.run(f"systemctl status {svc} --no-pager -l", shell=True,
                                   capture_output=True, text=True, timeout=30, check=False)
        else:
            sudo_password = _prompt_sudo()
            if is_macos:
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
                return _run_elevated(actual_cmd, sudo_password, timeout=30)
            elif sudo_password is None:
                return "[CANCELLED] Autentikasi administrator dibatalkan oleh pengguna."
            else:
                r = subprocess.run(actual_cmd, shell=True, capture_output=True, text=True, timeout=30, check=False)
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
            timeout=5, check=False)
        if r.stdout.strip():
            return f"INSTALLED: {r.stdout.strip()}"
    return f"NOT INSTALLED: {app} tidak ditemukan di system"


def handle_test_and_fix(args):
    cmd = args.get("cmd", "")
    if not cmd:
        return "Error: Parameter 'cmd' wajib diisi."
    try:
        with _Spinner("Mengetes"):
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60, check=False)
        output = r.stdout + r.stderr
        if r.returncode != 0:
            return f"FAILED (exit code {r.returncode})\n{output}"
        return f"SUCCESS\n{output}"
    except subprocess.TimeoutExpired:
        return "FAILED (timeout)"


def handle_sandbox_run(args):
    code = args.get("code", "")
    if not code:
        return "Error: Parameter 'code' wajib diisi."
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
            [interpreter, script_path] if interpreter in ("python3", "python", "node") else ["bash", script_path],
            capture_output=True, text=True, timeout=timeout, cwd=sandbox_dir, check=False
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
    except Exception as e:  # noqa: BLE001
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return f"[SANDBOX] Error: {e}"


def handle_run_tests(args):
    framework = args.get("framework", "auto")
    path = args.get("path", ".")
    timeout = min(args.get("timeout", 120000), 300000)
    timeout_s = timeout // 1000

    if not str(path or "").strip():
        return "Error: Parameter 'path' wajib diisi (contoh: '.', 'tests/', atau 'tests/test_x.py')."

    cwd = args.get("cwd", "")
    if cwd:
        path = os.path.join(cwd, path) if not os.path.isabs(path) else path
    else:
        cwd = os.getcwd()

    if not os.path.exists(path):
        return f"Error: Path tidak ditemukan: {path}"

    cmds = []
    if framework == "auto":
        proj_dir = path if os.path.isdir(path) else os.path.dirname(path)
        has_pytest = (os.path.exists(os.path.join(proj_dir, "pytest.ini")) or
                      os.path.exists(os.path.join(proj_dir, "pyproject.toml")) or
                      os.path.exists(os.path.join(proj_dir, "setup.cfg")))
        has_jest = (os.path.exists(os.path.join(proj_dir, "jest.config.js")) or
                    os.path.exists(os.path.join(proj_dir, "jest.config.ts")) or
                    os.path.exists(os.path.join(proj_dir, "jest.config.json")))
        has_phpunit = (os.path.exists(os.path.join(proj_dir, "phpunit.xml")) or
                       os.path.exists(os.path.join(proj_dir, "phpunit.xml.dist")))
        has_cargo = os.path.exists(os.path.join(proj_dir, "Cargo.toml"))
        has_go_mod = os.path.exists(os.path.join(proj_dir, "go.mod"))

        if has_pytest and _find_python_files(proj_dir):
            cmds.append(("pytest", f"cd {shq(proj_dir)} && python3 -m pytest -v"))
        if has_jest:
            cmds.append(("jest", f"cd {shq(proj_dir)} && npx jest --no-coverage"))
        if has_phpunit:
            cmds.append(("phpunit", f"cd {shq(proj_dir)} && phpunit --no-coverage"))
        if has_go_mod:
            cmds.append(("go test", f"cd {shq(proj_dir)} && go test ./..."))
        if has_cargo:
            cmds.append(("cargo test", f"cd {shq(proj_dir)} && cargo test"))
        if not cmds:
            return "Error: Framework testing tidak terdeteksi. Gunakan framework='pytest', 'jest', 'phpunit', 'go_test', atau 'cargo_test'."
    else:
        proj_dir = path if os.path.isdir(path) else os.path.dirname(path)
        fw_map = {
            "pytest": ("pytest", f"cd {shq(proj_dir)} && python3 -m pytest -v"),
            "jest": ("jest", f"cd {shq(proj_dir)} && npx jest --no-coverage"),
            "phpunit": ("phpunit", f"cd {shq(proj_dir)} && phpunit --no-coverage"),
            "go_test": ("go test", f"cd {shq(proj_dir)} && go test ./..."),
            "cargo_test": ("cargo test", f"cd {shq(proj_dir)} && cargo test"),
        }
        if framework in fw_map:
            cmds.append(fw_map[framework])
        else:
            return f"Error: Framework '{framework}' tidak dikenal. Pilihan: auto, pytest, jest, phpunit, go_test, cargo_test."

    results = []
    for name, cmd in cmds:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout_s, check=False)
            output = (r.stdout or "") + (r.stderr or "")
            # Exit 0 tanpa test nyata bukan bukti (go/cargo/jest kadang
            # melaporkan "no test files"/"running 0 tests" dengan exit 0).
            no_tests = re.search(
                r"\b(?:no tests? (?:ran|collected|found)|collected 0 items|"
                r"no test files|running 0 tests|0 passing)\b",
                output, re.IGNORECASE)
            if r.returncode == 0 and no_tests:
                status = "GAGAL (tidak ada test dijalankan)"
            elif r.returncode == 0:
                status = "BERHASIL"
            else:
                status = f"GAGAL (exit {r.returncode})"
            results.append(f"[{name}] {status}\n{output.strip()[:5000]}")
        except subprocess.TimeoutExpired:
            results.append(f"[{name}] TIMEOUT (>{timeout_s}s)")
        except Exception as e:  # noqa: BLE001
            results.append(f"[{name}] Error: {e}")

    return "\n---\n".join(results)


def _find_python_files(directory):
    for root, dirs, fnames in os.walk(directory):
        dn = os.path.basename(root)
        if dn.startswith(".") or dn in ("node_modules", "__pycache__", "venv", ".git"):
            dirs[:] = []
            continue
        for f in fnames:
            if f.endswith(".py"):
                return True
    return False


def shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


def handle_predict_command(args):
    cmd = args.get("cmd", "")
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
