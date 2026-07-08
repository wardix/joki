import re, threading, sys, time
from rich.console import Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich import box
from joki.state import *

__all__ = [
    "_TOOL_LABEL", "_Spinner", "_numbered", "_clean_latex",
    "stream_print", "Markdown", "Syntax",
]

_TOOL_LABEL = {
    "read_file": "Membaca file",
    "write_file": "Menulis file",
    "edit_file": "Mengedit file",
    "run_command": "Menjalankan perintah",
    "search_code": "Mencari kode",
    "list_dir": "Melihat isi direktori",
    "db_query": "Menjalankan query database",
    "service_control": "Mengelola service",
    "config_edit": "Mengedit konfigurasi",
    "package_check": "Memeriksa paket",
    "web_fetch": "Mengambil konten web",
    "web_search": "Mencari di web",
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
    "todo_create": "Membuat TODO list",
    "todo_done": "Menyelesaikan item TODO",
    "todo_show": "Menampilkan TODO list",
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
    "audio_info": "Info audio",
    "audio_transcribe": "Transkripsi audio",
    "video_info": "Info video",
    "video_extract": "Ekstrak video",
}

def _clean_latex(text):
    if "$" not in text:
        return text
    for latex, plain in _LATEX_REPLACE.items():
        text = text.replace(latex, plain)
    text = re.sub(r"\$\$\\sqrt\{([^}]*)\}\$\$", r"√(\1)", text)
    text = re.sub(r"\$\\sqrt\{([^}]*)\}\$", r"√(\1)", text)
    text = re.sub(r"\$\$\\frac\{([^}]*)\}\{([^}]*)\}\$\$", r"(\1)/(\2)", text)
    text = re.sub(r"\$\\frac\{([^}]*)\}\{([^}]*)\}\$", r"(\1)/(\2)", text)
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
        elif stripped.startswith(("- ", "* ", "+ ")) or stripped.startswith("1. "):
            md_patterns += 1
        elif stripped.startswith("```") or stripped.endswith("```"):
            md_patterns += 2
        elif "**" in stripped or "__" in stripped:
            md_patterns += 1
        elif "`" in stripped and len(stripped) > 10:
            md_patterns += 1
        elif "|" in stripped and "-" in stripped and "---" in stripped:
            md_patterns += 2
        elif re.search(r'\[.*\]\(.*\)', stripped):
            md_patterns += 1
    return md_patterns >= 2

def stream_print(text, delay=0.008):
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
            items.append(Syntax(code, lang, line_numbers=True, word_wrap=True))
        else:
            sub = re.split(r'(`[^`\n]+`)', part)
            for s in sub:
                if s.startswith('`') and s.endswith('`'):
                    items.append(Markdown(s))
                elif s.strip():
                    items.append(Markdown(_clean_latex(s)))
    if not items:
        return
    try:
        if len(items) == 1:
            _console.print(items[0])
        else:
            _console.print(Group(*items))
    except KeyboardInterrupt:
        raise

def _numbered(text):
    lines = text.splitlines(keepends=True)
    digits = len(str(len(lines)))
    return "".join(f"{i+1:>{digits}}: {l}" for i, l in enumerate(lines))

class _Spinner:
    def __init__(self, message="Processing"):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self):
        fd = sys.stdin.fileno()
        old = None
        if _HAS_TTY:
            try:
                old = termios.tcgetattr(fd)
                tty.setraw(fd, termios.TCSANOW)
            except Exception:
                old = None

        try:
            esc_count = 0
            last_esc = 0.0

            while not self._stop.is_set():
                for c in '|/-\\':
                    if self._stop.is_set():
                        break
                    sys.stdout.write(f'\r  {c} {self.message}... ')
                    sys.stdout.flush()

                    now = time.time()
                    if now - last_esc > 1.0:
                        esc_count = 0

                    if old is not None and select.select([sys.stdin], [], [], 0.05)[0]:
                        key = sys.stdin.read(1)
                        if key == '\x1b':
                            esc_count += 1
                            last_esc = now
                            if esc_count >= 2:
                                _joki_cancel.set()
                                self._stop.set()
                                return
                        elif key == '\x03':
                            raise KeyboardInterrupt
                        else:
                            esc_count = 0
                    else:
                        time.sleep(0.05)
        finally:
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, old)
                except Exception:
                    pass

    def __exit__(self, *args):
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write('\r' + ' ' * (len(self.message) + 10) + '\r')
        sys.stdout.flush()

_LATEX_REPLACE = {
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

