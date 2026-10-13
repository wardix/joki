import getpass
import os
import subprocess

from joki import state
from joki.display import _color_error, _color_warn, _pause_spinner, _resume_spinner
from joki.state import *

__all__ = [
    "_is_admin", "_prompt_sudo", "_run_elevated",
]

def _is_admin():
    """Check if current process has admin/root privileges."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return True


def _prompt_sudo():
    """Prompt user for sudo password and cache it for the session.
    Returns the password string, or '__ROOT__' if already admin, or None on cancel.
    """
    global _SUDO_PASSWORD
    if _SUDO_PASSWORD is not None:
        return _SUDO_PASSWORD

    if _is_admin():
        _SUDO_PASSWORD = "__ROOT__"
        return _SUDO_PASSWORD

    if state._TUI_ACTIVE:
        return _prompt_sudo_tui()

    try:
        _pause_spinner()
        _MONITOR_PAUSED.set()
        try:
            _console.print("Autentikasi administrator (sudo) diperlukan:")
            while True:
                _SUDO_PASSWORD = getpass.getpass("  Password: ")
                r = subprocess.run(
                    ["sudo", "-S", "-v"],
                    input=_SUDO_PASSWORD + "\n",
                    capture_output=True, text=True, timeout=10, check=False
                )
                if r.returncode == 0:
                    break
                _console.print("  Password salah!")
                _SUDO_PASSWORD = None
            _console.print("  Autentikasi berhasil.")
        finally:
            _MONITOR_PAUSED.clear()
        _resume_spinner()
        return _SUDO_PASSWORD
    except (EOFError, KeyboardInterrupt):
        _console.print(f"\n[{_color_warn()}]  Autentikasi dibatalkan.[/{_color_warn()}]")
        _SUDO_PASSWORD = None
        return None
    except Exception:  # noqa: BLE001
        _SUDO_PASSWORD = None
        return None


def _prompt_sudo_tui():
    """Prompt sudo lewat dialog TUI (worker thread) — validasi tetap pakai
    `sudo -S -v` sehingga credential ter-cache di timestamp sudo."""
    global _SUDO_PASSWORD
    message = "Masukkan password administrator (sudo):"
    while True:
        password = _request_tui_password(message)
        if password is None:
            _console.print(f"[{_color_warn()}]  Autentikasi dibatalkan.[/{_color_warn()}]")
            return None
        if not password:
            message = f"[{_color_error()}]Password tidak boleh kosong.[/{_color_error()}] Password administrator (sudo):"
            continue
        r = subprocess.run(
            ["sudo", "-S", "-v"],
            input=password + "\n",
            capture_output=True, text=True, timeout=10, check=False
        )
        if r.returncode == 0:
            _SUDO_PASSWORD = password
            return password
        message = f"[{_color_error()}]Password salah, coba lagi.[/{_color_error()}] Password administrator (sudo):"


def _run_elevated(cmd, password, timeout=60):
    """Run command with root privileges using cached password.
    Returns (stdout+stderr) string, or error message on failure."""
    try:
        if password == "__ROOT__":
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=False)
        else:
            r = subprocess.run(
                f"sudo -S {cmd}",
                shell=True, input=password + "\n",
                capture_output=True, text=True, timeout=timeout, check=False
            )
        output = (r.stdout or "") + (r.stderr or "")
        if not output.strip():
            output = "(no output)"
        if r.returncode == 0:
            return f"[ELEVATED] SUCCESS\n{output.strip()}"
        else:
            return f"[ELEVATED] FAILED (exit {r.returncode})\n{output.strip()}"
    except subprocess.TimeoutExpired:
        return f"[ELEVATED] TIMEOUT (>{timeout}s)"
    except Exception as e:  # noqa: BLE001
        return f"[ELEVATED] Error: {e}"

