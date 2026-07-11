import os, sys, subprocess, getpass, re
from joki.state import *
from joki.display import _numbered, _pause_spinner, _resume_spinner

__all__ = [
    "_is_admin", "_prompt_sudo", "_run_elevated",
]

def _is_admin():
    """Check if current process has admin/root privileges."""
    if os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return True


def _prompt_sudo():
    """Prompt user for admin password and cache it for the session.
    Returns the password string, or '__ROOT__' if already admin, or None on cancel.
    """
    global _SUDO_PASSWORD
    if _SUDO_PASSWORD is not None:
        return _SUDO_PASSWORD

    if _is_admin():
        _SUDO_PASSWORD = "__ROOT__"
        return _SUDO_PASSWORD

    try:
        _pause_spinner()
        if os.name == 'nt':
            _console.print("Autentikasi administrator Windows diperlukan:")
            _SUDO_PASSWORD = getpass.getpass("  Password Administrator: ")
            r = subprocess.run(
                f'runas /user:Administrator "cmd /c echo authenticated" 2>&1',
                shell=True, input=_SUDO_PASSWORD + "\n",
                capture_output=True, text=True, timeout=10
            )
            err_upper = (r.stdout + r.stderr).upper()
            if "LOGON FAILURE" in err_upper or "1326" in err_upper or "PASSWORD OR USERNAME" in err_upper:
                _console.print("  Password salah!")
                _SUDO_PASSWORD = None
                _resume_spinner()
                return _prompt_sudo()
            _console.print("  Autentikasi berhasil.")
        else:
            _console.print("Autentikasi administrator (sudo) diperlukan:")
            while True:
                _SUDO_PASSWORD = getpass.getpass("  Password: ")
                r = subprocess.run(
                    ["sudo", "-S", "-v"],
                    input=_SUDO_PASSWORD + "\n",
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode == 0:
                    break
                _console.print("  Password salah!")
                _SUDO_PASSWORD = None
            _console.print("  Autentikasi berhasil.")
        _resume_spinner()
        return _SUDO_PASSWORD
    except (EOFError, KeyboardInterrupt):
        _console.print("\n[yellow]  Autentikasi dibatalkan.[/yellow]")
        _SUDO_PASSWORD = None
        return None
    except Exception:
        _SUDO_PASSWORD = None
        return None


def _run_elevated(cmd, password, timeout=60):
    """Run command with admin/root privileges using cached password.
    Returns (stdout+stderr) string, or error message on failure."""
    try:
        if password == "__ROOT__":
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        elif os.name == 'nt':
            r = subprocess.run(
                f'runas /user:Administrator "cmd /c {cmd}"',
                shell=True, input=password + "\n",
                capture_output=True, text=True, timeout=timeout
            )
        else:
            r = subprocess.run(
                f"sudo -S {cmd}",
                shell=True, input=password + "\n",
                capture_output=True, text=True, timeout=timeout
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
    except Exception as e:
        return f"[ELEVATED] Error: {e}"

