import re
import sys
import threading
import time

from rich import box
from rich.console import Group
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax

from joki import state
from joki.state import (
    _CURRENT_SPINNER,
    _IS_TTY,
    _STATUS_KIND,
    _bridge_send,
    _console,
    _joki_cancel,
    _joki_card_bg,
    _SideCard,
)

__all__ = [
    "_TOOL_LABEL",
    "Markdown",
    "Syntax",
    "_Spinner",
    "_border_error",
    "_border_ok",
    "_clean_latex",
    "_color_error",
    "_color_info",
    "_color_ok",
    "_color_warn",
    "_message_card",
    "_numbered",
    "_pause_spinner",
    "_resume_spinner",
    "show_edit_diff",
    "stream_print",
]

def _syntax_theme():
    """Pilih tema Syntax rich sesuai mode gelap/terang yang ter-resolve.
    Dipadankan dengan palet TUI: github-dark saat gelap, friendly saat terang."""
    return "github-dark" if state._resolve_theme() else "friendly"


def _syntax_background():
    """Background putih saat tema terang, transparan (ikuti theme) saat gelap."""
    return None if state._resolve_theme() else "white"


def _color_ok():
    """Warna teks status: hitam di tema terang, putih di tema gelap (monokrom)."""
    return "#FFFFFF" if state._resolve_theme() else "#000000"


def _color_error():
    """Warna teks status: hitam di tema terang, putih di tema gelap (monokrom)."""
    return "#FFFFFF" if state._resolve_theme() else "#000000"


def _color_warn():
    """Warna teks peringatan: hitam di tema terang, putih di tema gelap (monokrom)."""
    return "#FFFFFF" if state._resolve_theme() else "#000000"


def _color_info():
    """Warna teks info: hitam di tema terang, putih di tema gelap (monokrom)."""
    return "#FFFFFF" if state._resolve_theme() else "#000000"


def _border_ok():
    """Warna border hijau (semantik) yang tetap kontras di kedua tema."""
    return "#3FB950" if state._resolve_theme() else "#1A7F37"


def _border_error():
    """Warna border merah (semantik) yang tetap kontras di kedua tema."""
    return "#F85149" if state._resolve_theme() else "#CF222E"


_TOOL_LABEL = {
    "read_file": "Membaca file",
    "write_file": "Menulis file",
    "edit_file": "Mengedit file",
    "undo_edit": "Mengembalikan backup file",
    "run_command": "Menjalankan perintah",
    "search_code": "Mencari kode",
    "list_dir": "Melihat isi direktori",
    "db_query": "Menjalankan query database",
    "service_control": "Mengelola service",
    "config_edit": "Mengedit konfigurasi",
    "package_check": "Memeriksa paket",
    "web_fetch": "Mengambil konten web (markdown)",
    "web_search": "Mencari di web (Brave/DDG)",
    "test_and_fix": "Mengetes dan memperbaiki",
    "memory_store": "Menyimpan memori",
    "memory_recall": "Mengambil memori",
    "memory_forget": "Menghapus memori",
    "screenshot": "Mengambil screenshot",
    "port_scan": "Port scanning",
    "dns_enum": "DNS enumeration",
    "web_vuln_scan": "Web vulnerability scan",
    "whois_lookup": "WHOIS lookup",
    "ssl_check": "SSL/TLS check",
    "dir_bruteforce": "Directory brute-force",
    "cve_search": "CVE search",
    "tech_detect": "Technology detection",
    "js_analyze": "JavaScript analysis",
    "api_discover": "API discovery",
    "source_map_check": "Source map check",
    "form_analyze": "Form analysis",
    "apk_analyze": "APK analysis",
    "binary_analyze": "Binary analysis",
    "todo_create": "Membuat Rencana Pengerjaan",
    "todo_done": "Menyelesaikan item Rencana Pengerjaan",
    "todo_show": "Menampilkan Rencana Pengerjaan",
    "ui_screenshot": "Screenshot UI",
    "ui_click": "Klik mouse",
    "ui_type": "Mengetik teks",
    "ui_keypress": "Tekan keyboard",
    "ui_focus": "Fokus window",
    "usb_list": "Daftar USB",
    "serial_send": "Kirim serial",
    "camera_capture": "Capture kamera",
    "sandbox_run": "Sandbox execution",
    "predict_command": "Prediksi perintah",
    "lsp_query": "Menanya LSP",
    "question": "Bertanya ke user",
    "rename_symbol": "Rename simbol",
    "audio_info": "Info audio",
    "audio_transcribe": "Transkripsi audio",
    "video_info": "Info video",
    "video_extract": "Ekstrak video",
    "web_scrape": "Scraping halaman web",
    "web_login": "Login ke website",
    "web_logout": "Logout dari website",
    "web_sessions": "Lihat session website",
    "read_office": "Membaca file office",
    "write_office": "Menulis file office",
    "git_status": "Git status",
    "git_diff": "Git diff",
    "git_log": "Git log",
    "git_commit": "Git commit",
    "git_push": "Git push",
    "git_pull": "Git pull",
    "git_branch": "Git branch",
    "git_clone": "Git clone",
    "git_init": "Git init",
    "git_add": "Git add",
    "git_merge": "Git merge",
    "git_stash": "Git stash",
    "git_remote": "Git remote",
    "run_linter": "Menjalankan linter",
    "lint_install": "Install linter",
    "run_tests": "Menjalankan test",
    "analyze_deps": "Analisa dependency",
    "impact_analysis": "Analisa dampak perubahan",
    "delegate_subtask": "Delegasi sub-tugas",
}

def _clean_latex(text):
    if "$" not in text and "\\" not in text:
        return text
    for latex, plain in _LATEX_REPLACE.items():
        text = text.replace(latex, plain)
    text = re.sub(r"\$\$\\sqrt\{([^}]*)\}\$\$", r"√(\1)", text)
    text = re.sub(r"\$\\sqrt\{([^}]*)\}\$", r"√(\1)", text)
    text = re.sub(r"\$\$\\frac\{([^}]*)\}\{([^}]*)\}\$\$", r"(\1)/(\2)", text)
    text = re.sub(r"\$\\frac\{([^}]*)\}\{([^}]*)\}\$", r"(\1)/(\2)", text)
    # \text{...}
    text = re.sub(r"\\text\{([^}]*)\}", r"\1", text)
    # \displaystyle
    text = re.sub(r"\\displaystyle\s*", "", text)
    # \left, \right (with word boundary to avoid \leftarrow etc.)
    text = re.sub(r"\\(left|right)\b", "", text)
    # bare \commands via regex with word boundary
    for cmd, plain in _BARE_LATEX.items():
        text = re.sub(re.escape(cmd) + r"\b", plain, text)
    # general \frac{}{} and \sqrt{} (anywhere, not just at start of $$)
    text = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^}]*)\}", r"√(\1)", text)
    # subscript _{...} and superscript ^{...}
    text = re.sub(r"_\{([^}]*)\}", r"_\1", text)
    text = re.sub(r"\^\{([^}]*)\}", r"^\1", text)
    # strip $$...$$ and $...$ wrappers
    text = re.sub(r"\$\$(.*?)\$\$", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\$(.*?)\$", r"\1", text, flags=re.DOTALL)
    # remove any remaining unknown \commands
    text = re.sub(r"\\([a-zA-Z]+)", "", text)
    return text

def _is_markdown(text):
    if not text or len(text) < 3:
        return False
    lines = text.strip().splitlines()
    if not lines:
        return False
    md_patterns = 0
    for line in lines[:15]:
        stripped = line.strip()
        if stripped.startswith(("# ", "## ", "### ", "#### ", "##### ", "###### ")):
            md_patterns += 2
        elif stripped.startswith(("- ", "* ", "+ ", "1. ")):
            md_patterns += 1
        elif stripped.startswith("```") or stripped.endswith("```"):
            md_patterns += 2
        elif "**" in stripped or "__" in stripped or "`" in stripped and len(stripped) > 10:
            md_patterns += 1
        elif "|" in stripped and "-" in stripped and "---" in stripped:
            md_patterns += 2
        elif re.search(r'\[.*\]\(.*\)', stripped):
            md_patterns += 1
    return md_patterns >= 2

def _message_card(body, title=""):
    """Card borderless dengan garis biru di samping kiri untuk pesan USER/JOKI.
    Pada tema terang background card dibuat putih."""
    return _SideCard(body, title, "blue", style=_joki_card_bg())


def stream_print(text, delay=0.001, card=None):
    if not text:
        return
    fence_parts = re.split(r'(```[\s\S]*?```)', text)
    items = []
    for part in fence_parts:
        if part.startswith('```') and part.endswith('```'):
            lines = part.splitlines()
            info = lines[0].lstrip('`').strip() if lines else ''
            code = '\n'.join(lines[1:-1]) if len(lines) > 2 else ''
            lang = info.split()[0] if info else "text"
            items.append(Syntax(code, lang, line_numbers=True, word_wrap=True, theme=_syntax_theme(), background_color=_syntax_background()))
        else:
            sub = re.split(r'(`[^`\n]+`)', part)
            for s in sub:
                if s.startswith('`') and s.endswith('`'):
                    items.append(Markdown(s))
                elif s.strip():
                    items.append(Markdown(_clean_latex(s)))
    if not items:
        return
    if card is not None:
        _console.print(_message_card(Group(*items), card))
    elif len(items) == 1:
        _console.print(items[0])
    else:
        _console.print(Group(*items))

def _numbered(text):
    if not text:
        return "1: "
    lines = text.splitlines(keepends=True)
    digits = len(str(len(lines)))
    return "".join(f"{i+1:>{digits}}: {l}" for i, l in enumerate(lines))

class _Spinner:
    def __init__(self, message="Processing"):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        global _CURRENT_SPINNER
        self._stop.clear()
        if state._TUI_ACTIVE:
            _CURRENT_SPINNER = self
            self._thread = threading.Thread(target=self._spin_tui, daemon=True)
            self._thread.start()
            return self
        if not _IS_TTY:
            sys.stdout.write(f"[{self.message}...]\n")
            sys.stdout.flush()
            return self
        _CURRENT_SPINNER = self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin_tui(self):
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while not self._stop.is_set() and not _joki_cancel.is_set():
            _bridge_send(_STATUS_KIND, f"{frames[i % len(frames)]} {self.message}...")
            i += 1
            time.sleep(0.1)

    def _spin(self):
        while not self._stop.is_set() and not _joki_cancel.is_set():
            for c in '|/-\\':
                if self._stop.is_set() or _joki_cancel.is_set():
                    return
                sys.stdout.write(f'\r  {c} {self.message}... ')
                sys.stdout.flush()
                time.sleep(0.05)

    def __exit__(self, *args):
        global _CURRENT_SPINNER
        self._stop.set()
        if self._thread:
            self._thread.join()
        _CURRENT_SPINNER = None
        if state._TUI_ACTIVE:
            _bridge_send(_STATUS_KIND, "")
            return
        if _IS_TTY:
            sys.stdout.write('\r' + ' ' * (len(self.message) + 10) + '\r')
            sys.stdout.flush()

def _pause_spinner():
    s = _CURRENT_SPINNER
    if s and s._thread and s._thread.is_alive():
        s._stop.set()
        s._thread.join()

def _resume_spinner():
    s = _CURRENT_SPINNER
    if s and not (s._thread and s._thread.is_alive()):
        s._stop.clear()
        if state._TUI_ACTIVE:
            s._thread = threading.Thread(target=s._spin_tui, daemon=True)
        else:
            s._thread = threading.Thread(target=s._spin, daemon=True)
        s._thread.start()


_LATEX_REPLACE = {
    # With $ delimiters
    r"$\rightarrow$": "→",
    r"$\Rightarrow$": "⇒",
    r"$\gets$": "←",
    r"$\leftarrow$": "←",
    r"$\Leftarrow$": "⇐",
    r"$\mapsto$": "↦",
    r"$\implies$": "⇒",
    r"$\iff$": "⇔",
    r"$\to$": "→",
    r"$\ge$": "≥",
    r"$\le$": "≤",
    r"$\neq$": "≠",
    r"$\approx$": "≈",
    r"$\equiv$": "≡",
    r"$\cdot$": "·",
    r"$\times$": "×",
    r"$\alpha$": "α",
    r"$\beta$": "β",
    r"$\gamma$": "γ",
    r"$\delta$": "δ",
    r"$\epsilon$": "ε",
    r"$\lambda$": "λ",
    r"$\mu$": "μ",
    r"$\pi$": "π",
    r"$\theta$": "θ",
    r"$\omega$": "ω",
    r"$\sigma$": "σ",
    r"$\phi$": "φ",
    r"$\dots$": "...",
    r"$\ldots$": "...",
    r"$\infty$": "∞",
    r"$\sum$": "Σ",
    r"$\prod$": "Π",
    r"$\sqrt{x}$": "√(x)",
}

_BARE_LATEX = {
    # Longer patterns first to avoid partial matches
    r"\Rightarrow": "⇒",
    r"\rightarrow": "→",
    r"\Leftarrow": "⇐",
    r"\leftarrow": "←",
    r"\leftrightarrow": "↔",
    r"\mapsto": "↦",
    r"\implies": "⇒",
    r"\iff": "⇔",
    r"\gets": "←",
    r"\to": "→",
    r"\ne": "≠",
    r"\neq": "≠",
    r"\approx": "≈",
    r"\equiv": "≡",
    r"\cdot": "·",
    r"\times": "×",
    r"\partial": "∂",
    r"\infty": "∞",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\pi": "π",
    r"\theta": "θ",
    r"\omega": "ω",
    r"\sigma": "σ",
    r"\phi": "φ",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\leq": "≤",
    r"\le": "≤",
    r"\dots": "...",
    r"\ldots": "...",
    r"\exp": "exp",
    r"\sin": "sin",
    r"\cos": "cos",
    r"\tan": "tan",
    r"\ln": "ln",
    r"\log": "log",
    r"\lim": "lim",
    r"\int": "∫",
    r"\sum": "Σ",
    r"\prod": "Π",
}

def show_edit_diff(old_text, new_text, path=""):
    from difflib import unified_diff

    if not old_text and not new_text:
        return

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    old_stripped = old_text.rstrip('\n')
    new_stripped = new_text.rstrip('\n')

    path_label = f" — {path}" if path else ""

    # Red panel for old code
    if old_stripped:
        _console.print(Panel(
            Syntax(old_stripped, "text", line_numbers=False, word_wrap=True, theme=_syntax_theme(), background_color=_syntax_background()),
            border_style=_border_error(),
            title=f"[bold {_color_info()}]\U0001f534 Kode Lama{path_label}[/bold {_color_info()}]",
            box=box.ROUNDED
        ))

    # Green panel for new code
    if new_stripped:
        _console.print(Panel(
            Syntax(new_stripped, "text", line_numbers=False, word_wrap=True, theme=_syntax_theme(), background_color=_syntax_background()),
            border_style=_border_ok(),
            title=f"[bold {_color_info()}]\U0001f7e2 Kode Baru{path_label}[/bold {_color_info()}]",
            box=box.ROUNDED
        ))

    # Unified diff for detail (compact, colored per line)
    diff_lines = list(unified_diff(old_lines, new_lines, fromfile=path, tofile=path))
    if not diff_lines:
        return

    body = diff_lines[2:]
    old_idx = 0
    new_idx = 0
    for line in body:
        line_str = line.rstrip('\n')
        if line_str.startswith('@@'):
            parts = line_str.split()
            old_start = new_start = 0
            if len(parts) >= 3:
                try:
                    old_start = int(parts[1].split(',')[0]) if ',' in parts[1] else int(parts[1])
                    new_start = int(parts[2].split(',')[0]) if ',' in parts[2] else int(parts[2])
                except (ValueError, IndexError):
                    old_start, new_start = 0, 0
            old_idx = max(0, old_start - 1)
            new_idx = max(0, new_start - 1)
            _console.print(f"      [dim]{escape(line_str)}[/dim]")
        elif line_str.startswith(('---', '+++')):
            continue
        elif line_str.startswith('-'):
            old_idx += 1
            _console.print(f"      [{_color_error()}]- {old_idx:>4}| {escape(line_str[1:])}[/{_color_error()}]")
        elif line_str.startswith('+'):
            new_idx += 1
            _console.print(f"      [{_color_ok()}]+ {new_idx:>4}| {escape(line_str[1:])}[/{_color_ok()}]")
        else:
            old_idx += 1
            new_idx += 1
            _console.print(f"      [dim]  {old_idx:>4}| {escape(line_str)}[/dim]")

