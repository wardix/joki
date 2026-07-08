import os, sys, subprocess, getpass, re
from joki.state import *
from joki.display import _numbered

__all__ = [
    "_is_admin", "_prompt_sudo", "_run_elevated",
    "DANGEROUS_PATTERNS", "_confirm_dangerous",
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
        _console.print()
        if os.name == 'nt':
            _console.print("[yellow]Autentikasi administrator Windows diperlukan:[/yellow]")
            _SUDO_PASSWORD = getpass.getpass("  Password Administrator: ")
            r = subprocess.run(
                f'runas /user:Administrator "cmd /c echo authenticated" 2>&1',
                shell=True, input=_SUDO_PASSWORD + "\n",
                capture_output=True, text=True, timeout=10
            )
            err_upper = (r.stdout + r.stderr).upper()
            if "LOGON FAILURE" in err_upper or "1326" in err_upper or "PASSWORD OR USERNAME" in err_upper:
                _console.print("[red]  Password salah![/red]")
                _SUDO_PASSWORD = None
                return _prompt_sudo()
            _console.print("[green]  Autentikasi berhasil.[/green]")
        else:
            _console.print("[yellow]Autentikasi administrator (sudo) diperlukan:[/yellow]")
            _SUDO_PASSWORD = getpass.getpass("  Password: ")
            r = subprocess.run(
                ["sudo", "-S", "-v"],
                input=_SUDO_PASSWORD + "\n",
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                _console.print("[red]  Password salah![/red]")
                _SUDO_PASSWORD = None
                return _prompt_sudo()
            _console.print("[green]  Autentikasi berhasil.[/green]")
        return _SUDO_PASSWORD
    except (EOFError, KeyboardInterrupt):
        _console.print("\n[yellow]  Autentikasi dibatalkan.[/yellow]")
        _SUDO_PASSWORD = None
        return None
    except Exception:
        _SUDO_PASSWORD = None
        return None


def _run_elevated(cmd, password):
    """Run command with admin/root privileges using cached password."""
    if os.name == 'nt':
        if password == "__ROOT__":
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        else:
            return subprocess.run(
                f'runas /user:Administrator "cmd /c {cmd}"',
                shell=True, input=password + "\n",
                capture_output=True, text=True, timeout=60
            )
    else:
        return subprocess.run(
            f"sudo -S {cmd}",
            shell=True, input=password + "\n",
            capture_output=True, text=True, timeout=60
        )

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\bdd\b.*of=",
    r"\bmkfs\b",
]


def _confirm_dangerous(cmd):
    if any(re.search(p, cmd, re.I) for p in DANGEROUS_PATTERNS):
        _console.print(f"[yellow]⚠ Operasi berbahaya terdeteksi:[/yellow] {cmd}")
        return input("Lanjutkan? (y/N): ").lower() == 'y'
    return True


