import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from rich.console import Console

__version__ = "0.1.0"

MODE_CODE = "code"
MODE_SYSADMIN = "sysadmin"
MODE_SECURITY = "security"
MODE_GENERAL = "general"

__all__ = [
    "BACKUP_DIR",
    "MODE_CODE",
    "MODE_GENERAL",
    "MODE_SECURITY",
    "MODE_SYSADMIN",
    "_APPROVED_ONCE",
    "_CONFIRM_KIND",
    "_CURRENT_SESSION",
    "_HAS_TTY",
    "_HUMAN_APPROVAL_KEY",
    "_IS_TTY",
    "_MONITOR_PAUSED",
    "_PERSISTENT_SHELL",
    "_PROTECTED_IDENTITY_PATHS",
    "_PROTECTED_SYSTEM_PATHS",
    "_PTY_LOCK",
    "_PTY_SESSION",
    "_QUESTION_KIND",
    "_READ_FILES",
    "_RENDER_KIND",
    "_RULE_KIND",
    "_SESSION_APPROVALS",
    "_SHELL_LOCK",
    "_STATUS_KIND",
    "_SUDO_PASSWORD",
    "_TEXT_KIND",
    "_THEME_DARK",
    "_THINK_KIND",
    "_TUI_ACTIVE",
    "_TUI_DARK",
    "_TUI_QUEUE",
    "_WORKSPACE_ROOT",
    "ConfigError",
    "JokiError",
    "LLMError",
    "ToolError",
    "__version__",
    "_bridge_send",
    "_console",
    "_current_model_config",
    "_current_task_mode",
    "_exhausted_keys",
    "_file_cache",
    "_file_change_log",
    "_is_project_root",
    "_is_protected_identity_path",
    "_is_protected_path",
    "_joki_cancel",
    "_joki_card",
    "_read_in_context",
    "_recent_tools",
    "_request_tui_confirm",
    "_request_tui_password",
    "_request_tui_question",
    "_resolve_theme",
    "_resolve_tui_confirm",
    "_resolve_tui_password",
    "_resolve_tui_question",
    "_set_theme",
    "_start_cancel_monitor",
    "_stop_cancel_monitor",
    "_tui_start",
    "_tui_stop",
    "allow_session",
    "approve_once",
    "consume_once_approval",
    "is_session_allowed",
    "reset_session_approvals",
]
BACKUP_DIR = os.path.join(tempfile.gettempdir(), "joki_backups")
_WORKSPACE_ROOT = os.getcwd()

# Marker direktori root proyek — dipakai bersama oleh pencari root di
# deps.py (_find_project_root_deps) dan lsp.py (_find_project_root) agar
# konsisten. Mencakup file penanda umum + pola glob (requirements*.txt,
# *.sln, *.xcodeproj) supaya proyek tanpa .git/pyproject tetap terdeteksi.
_PROJECT_ROOT_MARKERS_EXACT = {
    ".git", ".hg", ".svn",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "deno.json", "deno.jsonc",
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "Pipfile.lock", "uv.lock", "poetry.lock", "pdm.lock", "pixi.toml",
    "go.mod", "go.work",
    "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "gradlew", "mvnw",
    "composer.json",
    "Gemfile", "Gemfile.lock",
    "mix.exs", "rebar.config",
    "dune-project",
    "Package.swift",
    "flake.nix",
    ".project", "Makefile", "CMakeLists.txt",
}
_PROJECT_ROOT_MARKERS_GLOB = ("requirements*.txt", "*.sln", "*.xcodeproj")


def _is_project_root(directory):
    """True bila direktori tampak seperti root proyek (ada marker)."""
    try:
        names = set(os.listdir(directory))
    except OSError:
        return False
    if names & _PROJECT_ROOT_MARKERS_EXACT:
        return True
    return any(fnmatch.fnmatch(n, pat) for n in names for pat in _PROJECT_ROOT_MARKERS_GLOB)

# Tema: definisikan awal untuk hindari circular import (config.py import state)
_THEME_DARK = None

_PROTECTED_SYSTEM_PATHS = [
    "/etc/",
    "/usr/local/etc/",
    "/usr/share/",
    "/boot/",
    "/lib/",
    "/lib64/",
    "/usr/lib/",
    "/sys/",
    "/proc/",
    "/bin/",
    "/sbin/",
    "/usr/bin/",
    "/usr/sbin/",
]

_PROTECTED_IDENTITY_PATHS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d/",
    "/etc/ssh/sshd_config",
    os.path.expanduser("~/.ssh/"),
]

def _normalize_path(path):
    return os.path.realpath(os.path.expanduser(os.path.abspath(str(path))))

def _is_protected_path(abs_path):
    abs_path = _normalize_path(abs_path)
    for protected in _PROTECTED_SYSTEM_PATHS:
        if protected.endswith(os.sep):
            if abs_path.startswith(protected):
                return True
        else:
            if abs_path == protected or abs_path.startswith(protected + os.sep):
                return True
    return False

def _is_protected_identity_path(abs_path):
    abs_path = _normalize_path(abs_path)
    for protected in _PROTECTED_IDENTITY_PATHS:
        if protected.endswith(os.sep):
            if abs_path.startswith(protected):
                return True
        else:
            if abs_path == protected or abs_path.startswith(protected + os.sep):
                return True
    return False

_IS_TTY = sys.stdout.isatty()

_HAS_TTY = _IS_TTY and importlib.util.find_spec("termios") is not None


# === TUI bridge (Textual) ===
# Ketika TUI aktif, semua output _console di-route ke queue sebagai renderable;
# JokiApp memakainya untuk menulis ke RichLog. Saat tidak TUI, _console berperilaku
# normal (menulis ke stdout) sehingga mode scrollback & tes tetap jalan.
_TUI_ACTIVE = False
_TUI_QUEUE = None
_TUI_DARK = True

_RENDER_KIND = "render"
_TEXT_KIND = "text"
_RULE_KIND = "rule"
_STATUS_KIND = "status"
_THINK_KIND = "think"
_PASSWORD_KIND = "password"
_QUESTION_KIND = "question"
_CONFIRM_KIND = "confirm"

# Kunci internal persetujuan manusia. TIDAK ada di skema tool (constants.py)
# sehingga AI tidak bisa mengisinya lewat JSON — cli mem-strip kunci ini dari
# args AI sebelum gate, lalu menyuntik persetujuan via memori internal
# (_APPROVED_ONCE), bukan via args. Konstanta ini diekspor agar tool bisa
# membersihkan sisa forgery bila ada.
_HUMAN_APPROVAL_KEY = "_joki_human_approved"

# Permintaan password yang belum dijawab dari worker thread (TUI dialog).
# id -> {"event": threading.Event, "result": str|None}
_PASSWORD_REQUESTS: dict[str, dict[str, Any]] = {}


class _BridgeConsole(Console):
    def print(self, *objects, **kwargs):
        if _TUI_ACTIVE and _TUI_QUEUE is not None:
            style = kwargs.pop("style", None)
            markup = kwargs.pop("markup", True)
            sep = kwargs.pop("sep", " ")
            end = kwargs.pop("end", "\n")
            kwargs = {}
            if len(objects) == 1 and isinstance(objects[0], (list, tuple)):
                objects = tuple(objects[0])
            try:
                _TUI_QUEUE.put((_RENDER_KIND, (tuple(objects), style, markup, sep, end)))
            except Exception:  # noqa: BLE001, S110
                pass
            return
        super().print(*objects, **kwargs)

    def rule(self, *args, **kwargs):
        if _TUI_ACTIVE and _TUI_QUEUE is not None:
            text = args[0] if args else kwargs.get("title", "")
            style = kwargs.get("style")
            if not text:
                text = "─" * 40
            try:
                _TUI_QUEUE.put((_RULE_KIND, (text, style)))
            except Exception:  # noqa: BLE001, S110
                pass
            return None
        return super().rule(*args, **kwargs)


def _bridge_send(kind, payload):
    if _TUI_ACTIVE and _TUI_QUEUE is not None:
        try:
            _TUI_QUEUE.put((kind, payload))
        except Exception:  # noqa: BLE001, S110
            pass


# Kunci untuk counter id request. Tanpa ini, `+= 1` (LOAD-ADD-STORE,
# bukan atomik) bisa memberi rid kembar ke dua worker thread bersamaan
# -> jawaban tertukar atau worker gantung. Terbukti tabrakan tanpa lock.
_ID_COUNTER_LOCK = threading.Lock()

_PASSWORD_ID_COUNTER = 0


def _next_request_id(prefix):
    """Id unik thread-safe untuk request dialog TUI (pw1, pw2, ... / q1, ... / c1, ...)."""
    global _PASSWORD_ID_COUNTER, _QUESTION_ID_COUNTER, _CONFIRM_ID_COUNTER
    with _ID_COUNTER_LOCK:
        if prefix == "pw":
            _PASSWORD_ID_COUNTER += 1
            return f"pw{_PASSWORD_ID_COUNTER}"
        if prefix == "c":
            _CONFIRM_ID_COUNTER += 1
            return f"c{_CONFIRM_ID_COUNTER}"
        _QUESTION_ID_COUNTER += 1
        return f"q{_QUESTION_ID_COUNTER}"


def _request_tui_password(message):
    """Minta password lewat dialog TUI dari worker thread (blokir sampai
    dijawab/dibatalkan). Mengembalikan string password atau None bila batal."""
    if not (_TUI_ACTIVE and _TUI_QUEUE is not None):
        return None
    rid = _next_request_id("pw")
    holder = {"event": threading.Event(), "result": None}
    _PASSWORD_REQUESTS[rid] = holder
    _bridge_send(_PASSWORD_KIND, (rid, message))
    while not holder["event"].wait(0.2):
        if _joki_cancel.is_set():
            holder["result"] = None
            holder["event"].set()
            break
    _PASSWORD_REQUESTS.pop(rid, None)
    return holder["result"]


def _resolve_tui_password(rid, value):
    """Jawab permintaan password dari dialog TUI (panggil dari thread UI)."""
    holder = _PASSWORD_REQUESTS.get(rid)
    if holder is not None:
        holder["result"] = value
        holder["event"].set()


# Permintaan jawaban question-tool yang belum dijawab dari worker thread.
# id -> {"event": threading.Event, "result": str|None}
_QUESTION_REQUESTS: dict[str, dict[str, Any]] = {}
_QUESTION_ID_COUNTER = 0


def _request_tui_question(query, options=None, default="", timeout=300):
    """Minta jawaban lewat dialog TUI dari worker thread (blokir sampai
    dijawab/dibatalkan/timeout). Mengembalikan string jawaban atau None bila
    batal/timeout/TUI tak aktif."""
    if not (_TUI_ACTIVE and _TUI_QUEUE is not None):
        return None
    rid = _next_request_id("q")
    holder = {"event": threading.Event(), "result": None}
    _QUESTION_REQUESTS[rid] = holder
    _bridge_send(_QUESTION_KIND, (rid, str(query or ""), list(options or []), str(default or "")))
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 300.0
    elapsed = 0.0
    step = 0.2
    while elapsed < timeout:
        if holder["event"].wait(min(step, timeout - elapsed)):
            break
        elapsed += step
        if _joki_cancel.is_set():
            holder["result"] = None
            holder["event"].set()
            break
    _QUESTION_REQUESTS.pop(rid, None)
    return holder["result"]


def _resolve_tui_question(rid, value):
    """Jawab permintaan question dari dialog TUI (panggil dari thread UI)."""
    holder = _QUESTION_REQUESTS.get(rid)
    if holder is not None:
        holder["result"] = value
        holder["event"].set()


# === Konfirmasi destruktif (Ya sekali / Ya sesi ini / Batal) ===
# Worker thread (agent_loop) memblokir di _request_tui_confirm sampai dialog
# TUI dijawab. Hasil: "once" | "session" | None (batal/timeout/cancel).
_CONFIRM_REQUESTS: dict[str, dict[str, Any]] = {}
_CONFIRM_ID_COUNTER = 0


def _request_tui_confirm(reason, detail="", category_label="", timeout=300):
    """Minta konfirmasi Ya/Tidak/Sesi lewat dialog TUI (blokir worker thread)."""
    if not (_TUI_ACTIVE and _TUI_QUEUE is not None):
        return None
    rid = _next_request_id("c")
    holder = {"event": threading.Event(), "result": None}
    _CONFIRM_REQUESTS[rid] = holder
    _bridge_send(
        _CONFIRM_KIND,
        (rid, str(reason or ""), str(detail or "")[:400], str(category_label or "")),
    )
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 300.0
    elapsed = 0.0
    step = 0.2
    while elapsed < timeout:
        if holder["event"].wait(min(step, timeout - elapsed)):
            break
        elapsed += step
        if _joki_cancel.is_set():
            holder["result"] = None
            holder["event"].set()
            break
    _CONFIRM_REQUESTS.pop(rid, None)
    res = holder["result"]
    return res if res in ("once", "session") else None


def _resolve_tui_confirm(rid, value):
    """Jawab permintaan konfirmasi dari dialog TUI (panggil dari thread UI)."""
    holder = _CONFIRM_REQUESTS.get(rid)
    if holder is not None:
        holder["result"] = value if value in ("once", "session") else None
        holder["event"].set()


# === Persetujuan sesi + sekali-pakai (anti-AI-forge) ===
# _SESSION_APPROVALS: kategori risiko yang diizinkan manusia untuk sesi ini.
# Pengganti granular JOKI_AUTO_CONFIRM=1 (global mematikan semua pengaman).
# Kategori: destructive_cmd | db_destructive | nonlocal_scan |
#            service_control | git_force
_SESSION_APPROVALS: dict[str, bool] = {
    "destructive_cmd": False,
    "db_destructive": False,
    "nonlocal_scan": False,
    "service_control": False,
    "git_force": False,
}

# Token sekali-pakai: fingerprint (name + args kanonis) yang disetujui manusia
# di cli._ask_human_confirm. Disimpan di memori Python — AI tidak bisa mengisi
# via JSON tool args (yang di-strip sebelum gate). Tool mengonsumsi via
# consume_once_approval() sehingga eksekusi langsung jalan tanpa panggil ulang.
_APPROVED_ONCE: set[str] = set()
_APPROVAL_LOCK = threading.Lock()


def _approval_fingerprint(name, args):
    """Fingerprint kanonis name+args untuk token sekali-pakai."""
    try:
        import hashlib as _hl
        import json as _js

        clean = {k: v for k, v in dict(args or {}).items() if k != _HUMAN_APPROVAL_KEY}
        blob = _js.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
        digest = _hl.sha256(f"{name}:{blob}".encode("utf-8")).hexdigest()[:32]
        return f"{name}:{digest}"
    except Exception:  # noqa: BLE001
        return f"{name}:{id(args)}"


def approve_once(name, args):
    """Catat persetujuan manusia sekali-pakai untuk (name, args) ini."""
    try:
        with _APPROVAL_LOCK:
            _APPROVED_ONCE.add(_approval_fingerprint(name, args))
    except Exception:  # noqa: BLE001, S110
        pass


def consume_once_approval(name, args):
    """Konsumsi token sekali-pakai bila ada. True = manusia sudah setuju."""
    try:
        fp = _approval_fingerprint(name, args)
        with _APPROVAL_LOCK:
            if fp in _APPROVED_ONCE:
                _APPROVED_ONCE.discard(fp)
                return True
            return False
    except Exception:  # noqa: BLE001
        return False


def is_session_allowed(category):
    """True bila kategori risiko sudah diizinkan manusia untuk sesi ini."""
    try:
        return bool(_SESSION_APPROVALS.get(str(category or ""), False))
    except Exception:  # noqa: BLE001
        return False


def allow_session(category):
    """Tandai kategori risiko diizinkan untuk sisa sesi."""
    try:
        key = str(category or "")
        if key in _SESSION_APPROVALS:
            _SESSION_APPROVALS[key] = True
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def reset_session_approvals():
    """Nol-kan semua izin sesi (dipanggil saat /baru)."""
    try:
        for k in list(_SESSION_APPROVALS.keys()):
            _SESSION_APPROVALS[k] = False
    except Exception:  # noqa: BLE001, S110
        pass


def _tui_start(queue, dark=True):
    global _TUI_ACTIVE, _TUI_QUEUE, _TUI_DARK
    _TUI_ACTIVE = True
    _TUI_QUEUE = queue
    _TUI_DARK = bool(dark)


def _tui_stop():
    global _TUI_ACTIVE, _TUI_QUEUE
    _TUI_ACTIVE = False
    _TUI_QUEUE = None


# === Tema (adaptif ke environment, gaya opencode "system") ===
# Nilai ter-resolve: True = gelap, False = terang. Di-resolve lazy sekali lewat
# _resolve_theme(); dipakai TUI (Textual: self.dark) & Syntax highlight (rich).
# Definisi awal di baris 78 untuk hindari circular import.

def _config_theme_setting():
    """Baca nilai `theme` dari config.json: 'system' (default) | 'light' | 'dark'."""
    try:
        from joki.config import _get_config_path  # deferred: hindari circular import
        with open(_get_config_path()) as _f:
            data = json.load(_f)
        value = str(data.get("theme", "system")).strip().lower()
        return value if value in ("light", "dark", "system") else "system"
    except Exception:  # noqa: BLE001
        return "system"


def _gsettings_color_scheme():
    """Deteksi skema warna sistem via GNOME gsettings. None jika tak tersedia."""
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=1, check=False,
        ).stdout.strip().lower()
    except Exception:  # noqa: BLE001
        out = ""
    if "prefer-dark" in out:
        return "dark"
    if "prefer-light" in out:
        return "light"
    # 'default' / kosong → ikuti gtk-theme. Tema GNOME bawaan: terang kecuali
    # bernama ...-dark (mis. 'Yaru-dark', 'Adwaita-dark').
    try:
        theme = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=1, check=False,
        ).stdout.strip().lower()
    except Exception:  # noqa: BLE001
        theme = ""
    if theme:
        if "dark" in theme:
            return "dark"
        return "light"
    return None


def _osc11_background(timeout=0.4):
    """Query warna background terminal via OSC 11. Return (r, g, b) 0-255 atau None."""
    try:
        import select
        import termios
        stdin_fd = sys.stdin.fileno()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return None
        old = termios.tcgetattr(stdin_fd)
        raw = termios.tcgetattr(stdin_fd)
        raw[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(stdin_fd, termios.TCSANOW, raw)
        try:
            sys.stdout.write("\x1b]11;?\x1b\\")
            sys.stdout.flush()
            if not select.select([sys.stdin], [], [], timeout)[0]:
                return None
            resp = os.read(stdin_fd, 256)
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, old)
        match = re.search(
            r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)",
            resp.decode("latin1", "replace"),
        )
        if not match:
            return None

        def _channel(hexpart):
            if len(hexpart) == 1:
                return int(hexpart, 16) * 17
            return int(hexpart[:2], 16)

        return (_channel(match.group(1)), _channel(match.group(2)), _channel(match.group(3)))
    except Exception:  # noqa: BLE001
        return None


def _detect_dark():
    """Tentukan mode gelap/terang berdasarkan config + environment."""
    setting = _config_theme_setting()
    if setting == "light":
        return False
    if setting == "dark":
        return True
    g = _gsettings_color_scheme()
    if g == "dark":
        return True
    if g == "light":
        return False
    bg = _osc11_background()
    if bg is not None:
        lum = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) / 255.0
        return lum < 0.5
    return True


def _resolve_theme():
    """Resolve sekali (lazy cache) dan kembalikan mode gelap/terang app."""
    global _THEME_DARK
    if _THEME_DARK is None:
        _THEME_DARK = _detect_dark()
    return _THEME_DARK


def _set_theme(setting):
    """Set mode gelap/terang langsung dan simpan ke config.json. Return dark: bool."""
    global _THEME_DARK
    if isinstance(setting, str):
        dark = setting.strip().lower() not in ("light", "terang")
    else:
        dark = bool(setting)
    _THEME_DARK = dark
    try:
        from joki.config import _get_config_path  # deferred: hindari circular import
        path = _get_config_path()
        with open(path) as f:
            data = json.load(f)
        data["theme"] = "dark" if dark else "light"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:  # noqa: BLE001, S110
        pass
    return dark


_console = _BridgeConsole()


class _SideCard:
    def __init__(self, body, title, border_style="blue", style=None):
        self.body = body
        self.title = title
        self.border_style = border_style
        self.style = style

    def __rich_console__(self, console, options):
        from rich.text import Text
        border = console.get_style(self.border_style)

        if self.title:
            try:
                # Judul selalu hitam (terang) / putih (gelap); border tetap warna aslinya.
                title_color = "#FFFFFF" if _resolve_theme() else "#000000"
                head = Text.from_markup(
                    f"[bold {title_color}]{self.title}[/bold {title_color}]"
                )
            except Exception:  # noqa: BLE001
                head = Text(str(self.title))
            line = Text()
            line.append("│ ", style=border)
            line.append_text(head)
            if self.style:
                line.stylize(self.style)
            yield line

        body = self.body
        if isinstance(body, str):
            try:
                body = Text.from_markup(body)
            except Exception:  # noqa: BLE001
                body = Text(body)
        for segments in console.render_lines(body, options, pad=False):
            line = Text()
            line.append("│ ", style=border)
            for segment in segments:
                line.append(segment.text, segment.style)
            if self.style:
                line.stylize(self.style)
            yield line


def _joki_card(body, title, border_style="blue"):
    return _SideCard(body, title, border_style, style=_joki_card_bg())


def _joki_card_bg():
    """Background putih saat tema terang agar semua card konsisten."""
    return None if _resolve_theme() else "on white"


_current_model_config: dict[str, Any] = {}
_CURRENT_SESSION = None  # type: ignore[assignment]
_joki_cancel = threading.Event()
_MONITOR_PAUSED = threading.Event()
_cancel_thread_running = threading.Event()
_cancel_monitor = None
_monitor_stdin_snapshot: dict[str, Any] = {"old": None, "fd": None}
# Ditandai oleh _stop_cancel_monitor setelah thread utama restore termios
# secara sinkron. Dipakai agar thread monitor yang baru sadar dari read tidak
# menimpa mode terminal milik prompt_toolkit.
_monitor_restore_done = threading.Event()
_exhausted_keys: set[str] = set()
_SUDO_PASSWORD = None
_PERSISTENT_SHELL = None
_SHELL_LOCK = threading.RLock()
_PTY_SESSION = None
_PTY_LOCK = threading.Lock()
_READ_FILES: set[str] = set()
_CURRENT_SPINNER = None
_current_task_mode = MODE_GENERAL
_LSP_CLIENTS: dict[str, Any] = {}
_LSP_LOCK = threading.Lock()

# === Manajemen konteks & memori kerja ===
# Tool yang pernah dipakai sesi ini (untuk mengurangi jumlah schema tool yang dikirim)
_recent_tools: set[str] = set()
# Cache isi file: abs_path -> {"hash", "mtime"}
_file_cache: dict[str, Any] = {}
# Tool result read_file yang masih tersedia di history pesan:
# abs_path -> {"hash", "idx", "head"}
_read_in_context: dict[str, Any] = {}
# File yang berubah sejak notifikasi terakhir (untuk mekanisme perubahan file)
_file_change_log: set[str] = set()

class JokiError(Exception): pass
class ToolError(JokiError): pass
class LLMError(JokiError): pass
class ConfigError(JokiError): pass


# === Monitor cancel bersama (Esc+Esc / Ctrl+C) ===
# Satu-satunya pembaca stdin saat agent_loop berjalan. Thread lain (spinner,
# call_llm) TIDAK boleh menyentuh stdin/termios — cukup cek `_joki_cancel`.

def _monitor_cancel_loop():
    _stdin_old = None
    _read_fd = None
    _stdin_fd = None
    try:
        import select
        import termios
        _stdin_fd = sys.stdin.fileno()
        _stdin_old = termios.tcgetattr(_stdin_fd)
        _monitor_stdin_snapshot["old"] = _stdin_old  # type: ignore[assignment]
        _monitor_stdin_snapshot["fd"] = _stdin_fd  # type: ignore[assignment]
        _new = termios.tcgetattr(_stdin_fd)
        # Only set input-related raw flags — keep OPOST (output processing) ON
        # so stdout \n → \r\n conversion still works
        _new[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
        _new[2] &= ~(termios.CSIZE | termios.PARENB)
        _new[2] |= termios.CS8
        _new[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        _new[6][termios.VMIN] = 1
        _new[6][termios.VTIME] = 0
        termios.tcsetattr(_stdin_fd, termios.TCSANOW, _new)
        # Baca dari handle terminal yang DIBUKA BARU (bukan dup/fd 0) yang dibuat
        # non-blocking. Ini mencegah race select→read di mana byte sudah dikonsumsi
        # pembaca lain (mis. subprocess dry-test yang mewarisi tty) sehingga
        # os.read blok selamanya dan thread jadi zombie yang menelan keystroke
        # prompt `joki>` setelah agent_loop selesai. Wajib dibuka baru, bukan dup:
        # dup/dup2 berbagi open file description yang sama (termasuk flag
        # O_NONBLOCK) — stdout biasanya dup dari tty yang sama dengan stdin, jadi
        # set O_NONBLOCK di sana akan bikin stdout non-blocking juga dan rich
        # display gagal menulis (BlockingIOError: EAGAIN) saat buffer tty penuh.
        try:
            import fcntl
            _read_fd = os.open(os.ttyname(_stdin_fd), os.O_RDONLY | os.O_NOCTTY)
            _flags = fcntl.fcntl(_read_fd, fcntl.F_GETFL)
            fcntl.fcntl(_read_fd, fcntl.F_SETFL, _flags | os.O_NONBLOCK)
        except Exception:  # noqa: BLE001
            _read_fd = _stdin_fd
    except Exception:  # noqa: BLE001, S110
        pass
    _esc_count = 0
    _last_esc = 0.0
    try:
        while _cancel_thread_running.is_set() and not _joki_cancel.is_set():
            if _MONITOR_PAUSED.is_set():
                time.sleep(0.1)
                continue
            if _stdin_old is not None:
                import select
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    try:
                        if _read_fd is None:
                            continue
                        key = os.read(_read_fd, 1)
                    except (BlockingIOError, OSError):
                        continue
                    if key == b'\x1b':
                        now = time.time()
                        if now - _last_esc > 1.0:
                            _esc_count = 0
                        _esc_count += 1
                        _last_esc = now
                        if _esc_count >= 2:
                            _joki_cancel.set()
                            break
                    elif key == b'\x03':
                        _joki_cancel.set()
                        break
                    else:
                        _esc_count = 0
            else:
                time.sleep(0.1)
    except Exception:  # noqa: BLE001, S110
        pass
    finally:
        # Jangan restore termios kalau thread utama (via _stop_cancel_monitor)
        # sudah restore secara sinkron — mencegah thread zombie menimpa mode
        # terminal milik prompt_toolkit.
        if _stdin_old is not None and _stdin_fd is not None and not _monitor_restore_done.is_set():
            try:
                import termios
                termios.tcsetattr(_stdin_fd, termios.TCSANOW, _stdin_old)
            except Exception:  # noqa: BLE001, S110
                pass
        if _read_fd is not None and _stdin_fd is not None and _read_fd != _stdin_fd:
            try:
                os.close(_read_fd)
            except Exception:  # noqa: BLE001, S110
                pass

def _start_cancel_monitor():
    global _cancel_monitor
    if _TUI_ACTIVE:
        # Textual memiliki event loop & key binding sendiri; stdin tidak boleh
        # dipegang thread monitor raw.
        return
    if _cancel_monitor is not None and _cancel_monitor.is_alive():
        return
    _cancel_thread_running.set()
    _monitor_restore_done.clear()
    _cancel_monitor = threading.Thread(target=_monitor_cancel_loop, daemon=True, name="joki-cancel-monitor")
    _cancel_monitor.start()

def _stop_cancel_monitor():
    global _cancel_monitor
    if _TUI_ACTIVE:
        return
    _cancel_thread_running.clear()
    if _cancel_monitor is not None:
        # Restore termios SECARA SINKRON di thread utama supaya terminal tidak
        # pernah tertinggal di mode raw setelah agent_loop selesai — ini yang
        # membuat prompt `joki>` tak bisa diketik setelah dry-run stop.
        _snap_old = _monitor_stdin_snapshot.get("old")
        _snap_fd = _monitor_stdin_snapshot.get("fd")
        if _snap_old is not None and _snap_fd is not None:
            try:
                import termios
                termios.tcsetattr(_snap_fd, termios.TCSANOW, _snap_old)
            except Exception:  # noqa: BLE001, S110
                pass
        _monitor_restore_done.set()
        for _ in range(10):
            _cancel_monitor.join(timeout=0.1)
            if not _cancel_monitor.is_alive():
                break
        _cancel_monitor = None
