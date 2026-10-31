import queue
import re
import sys
import time
from typing import ClassVar

from rich.markup import escape
from rich.rule import Rule
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from joki import cli, state
from joki.display import Markdown, _color_error, _color_warn, _console, _message_card
from joki.state import (
    _CONFIRM_KIND,
    _PASSWORD_KIND,
    _QUESTION_KIND,
    _RENDER_KIND,
    _RULE_KIND,
    _STATUS_KIND,
    _THINK_KIND,
    MODE_CODE,
    MODE_GENERAL,
    MODE_SECURITY,
    MODE_SYSADMIN,
    _bridge_send,
    _current_model_config,
    _current_task_mode,
    _joki_cancel,
)
from joki.textual_compat import apply_textual_compat_patch

apply_textual_compat_patch()

_MODE_LABELS = {
    MODE_CODE: "Coding",
    MODE_SYSADMIN: "Sysadmin",
    MODE_SECURITY: "Security",
    MODE_GENERAL: "General",
}


def _context_header_text(messages):
    """Teks pemakaian token/konteks untuk sisi kanan header TUI.

    Format: 'Konteks ~3.2% (4.100/127.808 token)' dengan penanda zona:
    '\u26a0' saat mencapai ambang summarize otomatis (>=65%) dan '\u26d4'
    saat kritis (>=90%). Fail-open: kembalikan '' bila perhitungan gagal.
    """
    try:
        from joki.llm import SUMMARY_TRIGGER_RATIO, _current_tools, context_usage

        info = context_usage(messages, _current_tools())
        pct = float(info.get("pct") or 0.0)
        used = int(info.get("used") or 0)
        budget = int(info.get("budget") or 0)
    except Exception:  # noqa: BLE001
        return ""
    if budget <= 0:
        return ""
    if pct >= 90:
        mark = "\u26d4 "
    elif pct >= SUMMARY_TRIGGER_RATIO * 100:
        mark = "\u26a0 "
    else:
        mark = ""
    return (
        f"{mark}Konteks ~{pct:.1f}% "
        f"({cli._fmt_num(used)}/{cli._fmt_num(budget)} token)"
    )

# Tema terang: background putih bersih — semua huruf hitam pekat agar kontras
# maksimal di atas latar putih menyeluruh.
_JOKI_LIGHT_THEME = Theme(
    name="joki-light",
    primary="#0969DA",
    secondary="#8250DF",
    accent="#E8A33D",
    warning="#BC4C00",
    error="#CF222E",
    success="#1A7F37",
    foreground="#000000",
    background="#FFFFFF",
    surface="#FFFFFF",
    panel="#FFFFFF",
    dark=False,
)

# Tema gelap: palet lembut (gaya GitHub dark) — semua huruf putih agar kontras
# maksimal di atas background charcoal hangat (#0D1117).
_JOKI_DARK_THEME = Theme(
    name="joki-dark",
    primary="#58A6FF",
    secondary="#BC8CFF",
    accent="#D29922",
    warning="#D29922",
    error="#F85149",
    success="#3FB950",
    foreground="#FFFFFF",
    background="#0D1117",
    surface="#161B22",
    panel="#21262D",
    dark=True,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_MAX_LOG_WIDGETS = 1500   # batas jumlah widget yang boleh ada di #log sekaligus
_LOG_TRIM_TARGET = 1200   # setelah trim, sisakan sejumlah ini (biar nggak trim tiap kali nambah 1)

_SLASH_COMMANDS = [
    ("/model", "ganti model aktif"),
    ("/context", "info pemakaian token konteks"),
    ("/skills", "kelola skills (list/on/off/reload)"),
    ("/baru", "mulai session baru"),
    ("/sessions", "daftar session tersimpan"),
    ("/resume", "lanjutkan session (nomor/id)"),
    ("/keluar", "keluar"),
    ("/reset_quota", "reset state quota API key"),
    ("/reload", "reload config.json"),
    ("/themes", "ganti tema (gelap / terang)"),
    ("/install-lsp", "install LSP server"),
]


def _model_sub_options():
    """Daftar (key, nama) model aktif dari registry cli."""
    return [(k, v.get("name", k)) for k, v in cli._MODELS.items()]


def _markup_styles_valid(text):
    """False bila ada span dengan style yang tidak bisa diparse Rich.

    Rich menunda validasi style: `Text.from_markup('[budi]')` sukses (span
    style = 'budi'), error MissingStyle baru muncul saat render Textual —
    di layout, exception itu menutup app (kejadian nyata: /sessions dengan
    cwd basename 'budi').
    """
    for span in text.spans:
        st = span.style
        if isinstance(st, str):
            try:
                Style.parse(st)
            except Exception:  # noqa: BLE001
                return False
    return True


def _safe_markup_text(obj, style=None):
    """Text hasil parse markup yang aman dirender Textual.

    Bila tag-nya bukan style valid (teks data pengguna, mis. '[budi]'),
    jatuh ke teks biasa agar tetap tampil tanpa menjatuhkan app.
    """
    try:
        text = Text.from_markup(obj, style=style)
    except Exception:  # noqa: BLE001
        return Text(obj, style=style)
    if not _markup_styles_valid(text):
        return Text(obj, style=style)
    return text


def _theme_sub_options():
    """Daftar opsi tema: (value, label)."""
    return [("dark", "Gelap"), ("light", "Terang")]


def _session_sub_options():
    """Daftar (session_id, label) session tersimpan untuk menu /resume."""
    try:
        rows = cli._sessions.list_sessions(limit=12)
    except Exception:  # noqa: BLE001
        rows = []
    out = []
    for r in rows:
        upd = str(r.get("updated") or "")[:16].replace("T", " ")
        label = f"{str(r.get('first_user') or '')[:45]}  ({upd})"
        out.append((r["session"], label))
    return out


def _lsp_sub_options():
    """Daftar (lang, label) LSP server dengan status terinstall/belum."""
    from joki.tools.lsp import _BUILTIN_SERVERS, _LANG_NAMES, _get_available_servers

    available = _get_available_servers()
    options = []
    for lang in sorted(_BUILTIN_SERVERS):
        name = _LANG_NAMES.get(lang, lang)
        status = "terinstall" if lang in available else "belum terinstall"
        options.append((lang, f"{name} ({status})"))
    return options


class _QueueStdout:
    """Redirection sys.stdout/stderr selama TUI aktif supaya print()/write()
    yang tersisa (mis. di cli.py) ikut masuk ke log chat, bukan merusak layar TUI."""

    def __init__(self, real):
        self._real = real

    def write(self, data):
        if data:
            _bridge_send(_RENDER_KIND, ((data.rstrip("\n"),), None, False, " ", "\n"))

    def flush(self):
        pass

    def isatty(self):
        return True

    def fileno(self):
        return self._real.fileno()


class _PasswordDialog(Container):
    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class Cancelled(Message):
        pass

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("escape", "pw_cancel", "Batal", priority=True, show=False),
    ]

    def __init__(self, message: str = ""):
        super().__init__(id="pw_dialog")
        self._pw_message = message
        self._input = Input(password=True, id="pw_input")

    def compose(self) -> ComposeResult:
        with Container(id="pw_box"):
            yield Static(self._pw_message, id="pw_msg", markup=True)
            yield self._input
            yield Static("[dim]Enter = kirim • Esc = batal[/dim]", id="pw_hint")

    def on_mount(self) -> None:
        self.display = False

    def set_prompt(self, message: str) -> None:
        self._pw_message = message
        self.query_one("#pw_msg", Static).update(message)
        self._input.value = ""

    def focus_input(self) -> None:
        self._input.focus()

    def action_pw_cancel(self) -> None:
        self.post_message(self.Cancelled())

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        self.post_message(self.Submitted(event.value))


class _QuestionDialog(Container):
    """Dialog pertanyaan question-tool: daftar opsi + input jawaban bebas.

    Aturan submit (Enter di input): isi input bila ada; bila kosong pakai opsi
    yang sedang disorot; bila tak ada opsi pakai default (bisa string kosong).
    Esc = batal.
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class Cancelled(Message):
        pass

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("escape", "q_cancel", "Batal", priority=True, show=False),
    ]

    def __init__(self, query: str = "", options: list | None = None, default: str = ""):
        super().__init__(id="q_dialog")
        self._q_query = query
        self._q_options = list(options or [])
        self._q_default = default or ""
        self._input = Input(id="q_input")
        self._opts: OptionList | None = None

    def compose(self) -> ComposeResult:
        with Container(id="q_box"):
            yield Static(escape(self._q_query), id="q_msg", markup=True)
            if self._q_options:
                self._opts = OptionList(
                    *[Option(escape(f"{i}. {opt}"), id=f"qopt{i}") for i, opt in enumerate(self._q_options, 1)],
                    id="q_opts",
                )
                yield self._opts
            yield self._input
            hint = "[dim]Enter = kirim • Esc = batal"
            if self._q_default:
                hint += f" • kosong = default ({escape(str(self._q_default))})"
            hint += "[/dim]"
            yield Static(hint, id="q_hint")

    def on_mount(self) -> None:
        self.display = False

    def set_prompt(self, query: str, options: list | None = None, default: str = "") -> None:
        self._q_query = query
        self._q_options = list(options or [])
        self._q_default = default or ""
        self.query_one("#q_msg", Static).update(escape(query))
        self._input.value = ""
        try:
            old = self.query_one("#q_opts", OptionList)
            old.remove()
        except Exception:  # noqa: BLE001, S110
            pass
        self._opts = None
        if self._q_options:
            self._opts = OptionList(
                *[Option(escape(f"{i}. {opt}"), id=f"qopt{i}") for i, opt in enumerate(self._q_options, 1)],
                id="q_opts",
            )
            self.mount(self._opts, before="#q_input")

    def focus_input(self) -> None:
        self._input.focus()

    def _resolve_value(self) -> str:
        typed = (self._input.value or "").strip()
        if typed:
            return typed
        if self._opts is not None:
            try:
                idx = self._opts.highlighted
                if idx is not None:
                    opt = self._opts.get_option_at_index(idx)
                    opt_id = str(getattr(opt, "id", "") or "")
                    if opt_id.startswith("qopt"):
                        n = int(opt_id[4:])
                        if 1 <= n <= len(self._q_options):
                            return self._q_options[n - 1]
            except Exception:  # noqa: BLE001, S110
                pass
        return self._q_default

    def action_q_cancel(self) -> None:
        self.post_message(self.Cancelled())

    @on(Input.Submitted)
    def _on_q_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(self._resolve_value()))

    @on(OptionList.OptionSelected)
    def _on_q_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        try:
            idx = event.option_index
            if idx is not None and 0 <= idx < len(self._q_options):
                self.post_message(self.Submitted(self._q_options[idx]))
                return
        except Exception:  # noqa: BLE001, S110
            pass
        self.post_message(self.Submitted(self._resolve_value()))


class _ConfirmDialog(Container):
    """Dialog konfirmasi destruktif: Ya-sekali / Ya-untuk-sesi / Batal.

    Dipakai agent_loop saat butuh persetujuan manusia di mode TUI — pengganti
    prompt Y/N terminal. Opsi ketiga (\"sesi\") mencatat allow_session() untuk
    kategori risiko terkait agar tak ditanya ulang. Esc = batal (aman).
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            # value: "once" | "session"
            self.value = value
            super().__init__()

    class Cancelled(Message):
        pass

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("escape", "c_cancel", "Batal", priority=True, show=False),
    ]

    def __init__(self, reason: str = "", detail: str = "", category_label: str = ""):
        super().__init__(id="c_dialog")
        self._c_reason = reason
        self._c_detail = detail
        self._c_label = category_label
        self._opts: OptionList | None = None

    def compose(self) -> ComposeResult:
        with Container(id="c_box"):
            yield Static("", id="c_msg", markup=True)
            yield Static("", id="c_detail", markup=True)
            self._opts = OptionList(
                Option("Ya, jalankan sekali", id="conce"),
                Option("Ya, izinkan kategori ini untuk sesi ini", id="csession"),
                Option("Batal (aman)", id="ccancel"),
                id="c_opts",
            )
            yield self._opts
            yield Static("[dim]↑/↓ pilih • Enter kirim • Esc batal[/dim]", id="c_hint")

    def on_mount(self) -> None:
        self.display = False
        self._render_texts()

    def _render_texts(self) -> None:
        try:
            reason = escape(self._c_reason or "Aksi berisiko butuh persetujuan")
            label = escape(self._c_label) if self._c_label else ""
            head = f"[bold]⚠ KONFIRMASI MANUSIA[/bold]\n{reason}"
            if label:
                head += f"\n[dim]Kategori sesi: {label}[/dim]"
            self.query_one("#c_msg", Static).update(head)
            det = escape((self._c_detail or "")[:400])
            self.query_one("#c_detail", Static).update(f"[dim]{det}[/dim]" if det else "")
        except Exception:  # noqa: BLE001, S110
            pass

    def set_prompt(self, reason: str, detail: str = "", category_label: str = "") -> None:
        self._c_reason = reason
        self._c_detail = detail
        self._c_label = category_label
        self._render_texts()
        try:
            if self._opts is not None:
                self._opts.highlighted = 0
        except Exception:  # noqa: BLE001, S110
            pass

    def focus_input(self) -> None:
        try:
            if self._opts is not None:
                self._opts.focus()
        except Exception:  # noqa: BLE001, S110
            pass

    def action_c_cancel(self) -> None:
        self.post_message(self.Cancelled())

    @on(OptionList.OptionSelected)
    def _on_c_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        try:
            opt_id = str(getattr(event.option, "id", "") or "")
        except Exception:  # noqa: BLE001
            opt_id = ""
        if opt_id == "csession":
            self.post_message(self.Submitted("session"))
        elif opt_id == "conce":
            self.post_message(self.Submitted("once"))
        else:
            self.post_message(self.Cancelled())


class JokiInput(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    BINDINGS = TextArea.BINDINGS + [
        Binding("enter", "submit", "Kirim", priority=True, show=False),
    ]

    # Batas riwayat prompt yang disimpan (ala shell; tidak menghapus yang lama
    # di sesi — hanya membatasi memori).
    MAX_HISTORY = 200

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._hist_pos: int | None = None  # None = tidak sedang navigasi
        self._hist_draft = ""              # teks yang diketik sebelum navigasi
        self._hist_loaded = ""             # isi item riwayat yang sedang tampil

    # ---------- riwayat prompt (panah atas/bawah, ala opencode/shell) ----------

    def add_history(self, text: str) -> None:
        """Catat prompt yang dikirim; dedup berturut-turut, kerat ke MAX_HISTORY."""
        text = str(text).rstrip()
        if not text.strip():
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            if len(self._history) > self.MAX_HISTORY:
                del self._history[: len(self._history) - self.MAX_HISTORY]
        self._hist_pos = None
        self._hist_draft = ""
        self._hist_loaded = ""

    def seed_history(self, items) -> None:
        """Isi awal riwayat (mis. pesan user sesi yang di-resume)."""
        for it in items:
            if isinstance(it, str):
                self.add_history(it)

    def _load_history_text(self, text: str) -> None:
        self.text = text
        row = text.count("\n")
        col = len(text.rsplit("\n", 1)[-1])
        self.move_cursor((row, col))
        try:
            self.scroll_cursor_visible()
        except Exception:  # noqa: BLE001, S110
            pass
        self._hist_loaded = text

    def _history_prev(self) -> bool:
        """Tampilkan prompt yang lebih lama. True = tombol dikonsumsi."""
        if not self._history:
            return False
        if self._hist_pos is None:
            self._hist_draft = self.text
            self._hist_pos = len(self._history) - 1
        elif self._hist_pos > 0:
            self._hist_pos -= 1
        else:
            return True  # sudah paling lama — tahan di situ
        self._load_history_text(self._history[self._hist_pos])
        return True

    def _history_next(self) -> bool:
        """Tampilkan prompt yang lebih baru; melewati ujung = pulihkan draft."""
        if self._hist_pos is None:
            return False
        if self._hist_pos < len(self._history) - 1:
            self._hist_pos += 1
            self._load_history_text(self._history[self._hist_pos])
        else:
            self._hist_pos = None
            draft = self._hist_draft
            self._hist_draft = ""
            self._load_history_text(draft)
        return True

    def _history_key(self, key: str) -> bool:
        """Navigasi riwayat untuk panah atas/bawah.

        Aman untuk input multiline: panah hanya "diambil alih" bila kursor di
        batas yang wajar (baris pertama untuk atas, baris terakhir untuk bawah),
        kecuali buffer belum diubah sejak item riwayat dimuat (navigasi beruntun
        tetap bisa melewati prompt multiline).
        """
        if key == "up":
            if not self._history:
                return False
            if self.cursor_location[0] != 0:
                dirty = self._hist_pos is not None and self.text != self._hist_loaded
                if self._hist_pos is None or dirty:
                    return False
            return self._history_prev()
        # down
        if self._hist_pos is None:
            return False
        if self.text != self._hist_loaded and self.cursor_location[0] != self.text.count("\n"):
            return False
        return self._history_next()

    def action_submit(self) -> None:
        menu = getattr(self.app, "_cmd_menu", None)
        if menu is not None and menu.display and menu.option_count > 0:
            self.app._select_cmd_option()  # type: ignore[attr-defined]
            return
        text = self.text
        if text.strip():
            self.add_history(text)
            self.clear()
            self.post_message(self.Submitted(text))

    async def _on_key(self, event) -> None:
        if event.key in ("alt+enter", "escape+enter", "ctrl+alt+enter", "shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        menu = getattr(self.app, "_cmd_menu", None)
        if menu is not None and menu.display:
            key = event.key
            if key in ("up", "down"):
                event.stop()
                event.prevent_default()
                if menu.option_count > 0:
                    if key == "down":
                        menu.action_cursor_down()
                    else:
                        menu.action_cursor_up()
                return
            if key == "tab":
                event.stop()
                event.prevent_default()
                self.app._select_cmd_option()  # type: ignore[attr-defined]
                return
        if event.key in ("up", "down") and self._history_key(event.key):
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


class JokiApp(App):
    TITLE = "Joki"
    SUB_TITLE = "AI agent — Rahmad Budiman"

    BINDINGS: ClassVar[list] = [
        Binding("ctrl+c", "cancel", "Batal", priority=True, show=False),
        Binding("escape", "cancel", "Batal", priority=True, show=False),
    ]

    CSS = """
    #layout { layout: vertical; }

    #topbar {
        layout: horizontal;
        height: 1;
        background: $accent;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    #topbar_title { width: 1fr; text-wrap: nowrap; }
    #topbar_ctx { width: auto; text-wrap: nowrap; }

    #log {
        height: 1fr;
        background: $surface;
        overflow-y: auto;
        overflow-x: hidden;
        padding: 0 0 1 0;
        scrollbar-size: 0 0;
    }

    #log .log_msg {
        width: 1fr;
        padding: 0 2;
        margin: 0 1 1 1;
        text-wrap: wrap;
    }

    #log .log_rule {
        width: 1fr;
        padding: 0 2;
        margin: 1 1 1 1;
        color: $text-muted;
    }

    #thinking_card {
        height: auto;
        max-height: 12;
        border-left: round $accent;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
        margin: 0 1;
        text-wrap: wrap;
        display: none;
    }
    #thinking_card.active { display: block; }

    #status {
        height: auto;
        color: $text-muted;
        padding: 0 1;
        text-wrap: wrap;
    }

    #prompt_card {
        height: auto;
        max-height: 12;
        background: $panel;
        border-left: round $accent;
        padding: 0 1;
    }

    #prompt {
        height: auto;
        max-height: 8;
        border: none;
        background: transparent;
    }
    #prompt:focus {
        background: transparent;
        border: none;
    }

    #prompt_meta_row {
        layout: horizontal;
        height: auto;
        padding: 1 0 1 0;
        color: $text-muted;
        text-wrap: wrap;
    }
    #prompt_meta_left { width: 1fr; }
    #prompt_meta_right { width: auto; }

    #cmd_menu {
        display: none;
        height: auto;
        max-height: 8;
        margin: 0 0 1 0;
        background: $panel;
        border: round $accent;
    }

    #pw_dialog {
        display: none;
        height: 100%;
        width: 100%;
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #pw_box {
        height: auto;
        width: 60%;
        border: round $accent;
        background: $panel;
        padding: 1 2 1 2;
    }

    #pw_msg {
        margin-bottom: 1;
    }

    #pw_hint {
        color: $text-muted;
        margin-top: 1;
    }

    #pw_input {
        margin: 0 0 1 0;
    }

    #q_dialog {
        display: none;
        height: 100%;
        width: 100%;
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #q_box {
        height: auto;
        max-height: 80%;
        width: 70%;
        border: round $accent;
        background: $panel;
        padding: 1 2 1 2;
    }

    #q_msg {
        margin-bottom: 1;
    }

    #q_opts {
        height: auto;
        max-height: 8;
        margin: 0 0 1 0;
        background: $panel;
        border: round $accent;
    }

    #q_hint {
        color: $text-muted;
        margin-top: 1;
    }

    #q_input {
        margin: 0 0 1 0;
    }

    #c_dialog {
        display: none;
        height: 100%;
        width: 100%;
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #c_box {
        height: auto;
        max-height: 80%;
        width: 70%;
        border: round $accent;
        background: $panel;
        padding: 1 2 1 2;
    }

    #c_msg {
        margin-bottom: 1;
    }

    #c_detail {
        margin-bottom: 1;
    }

    #c_opts {
        height: auto;
        max-height: 8;
        margin: 0 0 1 0;
        background: $panel;
        border: round $accent;
    }

    #c_hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, messages, model_display="", **kwargs):
        super().__init__(**kwargs)
        self.messages = messages
        self.model_display = model_display
        self._busy = False
        self._draining = False
        self._thinking_active = False
        self._owns_queue = False
        # Throttle refresh header konteks saat tugas berjalan (lihat _drain_queue).
        self._topbar_refresh_ts = 0.0
        self._prompt = JokiInput(
            id="prompt",
            language=None,
            soft_wrap=True,
            placeholder="Tanya apa saja... — Enter kirim, Alt+Enter/Ctrl+J baris baru, ↑/↓ riwayat",
        )
        self._initial_input = None

    def compose(self) -> ComposeResult:
        with Container(id="layout"):
            with Container(id="topbar"):
                yield Static(id="topbar_title", markup=True)
                yield Static(id="topbar_ctx", markup=True)
            yield ScrollableContainer(id="log")
            yield Static(id="thinking_card", markup=True)
            yield Static(id="status", markup=True)
            with Container(id="prompt_card"):
                yield OptionList(id="cmd_menu")
                yield self._prompt
                yield Container(
                    Static(id="prompt_meta_left", markup=True),
                    Static(id="prompt_meta_right", markup=True),
                    id="prompt_meta_row",
                )
            yield _PasswordDialog()
            yield _QuestionDialog()
            yield _ConfirmDialog()

    def _resolve_tui_theme(self) -> str:
        """Pilih tema TUI: joki-light (putih lembut) saat terang, joki-dark saat gelap."""
        return "joki-dark" if state._resolve_theme() else "joki-light"

    def on_mount(self) -> None:
        self.register_theme(_JOKI_LIGHT_THEME)
        self.register_theme(_JOKI_DARK_THEME)
        self.theme = self._resolve_tui_theme()
        if state._TUI_QUEUE is None:
            state._tui_start(queue.Queue(), dark=True)
            self._owns_queue = True
        self._cmd_menu = self.query_one("#cmd_menu", OptionList)
        self._cmd_menu.display = False
        self._pw_dialog = self.query_one("#pw_dialog", _PasswordDialog)
        self._pw_request_id: str | None = None
        self._q_dialog = self.query_one("#q_dialog", _QuestionDialog)
        self._q_request_id: str | None = None
        self._c_dialog = self.query_one("#c_dialog", _ConfirmDialog)
        self._c_request_id: str | None = None
        self.set_interval(0.05, self._drain_queue)
        self._refresh_topbar()
        self._refresh_prompt_meta()
        self._prompt.focus()
        # Riwayat panah atas/bawah: mulai dari prompt user sesi ini supaya
        # setelah /resume atau --resume riwayat langsung terisi. Pesan internal
        # (nudge sistem) selalu diawali "[" — jangan ikut masuk riwayat.
        try:
            seeds = [
                m.get("content")
                for m in self.messages
                if isinstance(m, dict)
                and m.get("role") == "user"
                and isinstance(m.get("content"), str)
                and m["content"].strip()
                and not m["content"].lstrip().startswith("[")
            ]
            if seeds:
                self._prompt.seed_history(seeds[-50:])
        except Exception:  # noqa: BLE001, S110
            pass
        self._drain_queue()
        if self._initial_input:
            initial = self._initial_input
            self._initial_input = None
            self._handle_submit_text(initial)
        else:
            # Session hasil `joki --resume`: langsung tampilkan riwayat
            # percakapan supaya konteksnya terlihat tanpa /sessions.
            try:
                cli._render_session_history(self.messages, session_id=cli._CURRENT_SESSION)
            except Exception:  # noqa: BLE001, S110
                pass

    def _refresh_topbar(self) -> None:
        """Perbarui header atas: nama model (kiri) + pemakaian token/konteks (kanan)."""
        mc = _current_model_config
        model = mc.get("name", "?")
        self.query_one("#topbar_title", Static).update(
            f"JOKI  •  {model}"
        )
        self.query_one("#topbar_ctx", Static).update(_context_header_text(self.messages))

    def _refresh_prompt_meta(self) -> None:
        mode_label = _MODE_LABELS.get(_current_task_mode, "General")
        left = f"[b]{mode_label}[/b]"
        right = f"[b {_color_error()}]RUN[/b {_color_error()}]" if self._busy else "[dim]idle[/dim]"
        self.query_one("#prompt_meta_left", Static).update(left)
        self.query_one("#prompt_meta_right", Static).update(right)

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(_safe_markup_text(text or ""))

    def _set_thinking(self, text: str) -> None:
        card = self.query_one("#thinking_card", Static)
        if text:
            card.update(f"[bold]\U0001f9e0 Joki Berpikir[/bold]\n{escape(text)}")
            if not self._thinking_active:
                card.add_class("active")
                self._thinking_active = True
        else:
            card.update("")
            if self._thinking_active:
                card.remove_class("active")
                self._thinking_active = False

    def _make_log_widgets(self, objects, style, markup, sep):
        """Ubah objek output (str / Text / renderable rich) jadi daftar Static.
        Static di dalam ScrollableContainer otomatis re-wrap saat lebar window
        berubah — menggantikan RichLog yang merender sekali lalu meng-cache."""
        widgets = []
        for obj in objects:
            if isinstance(obj, str):
                if not obj:
                    continue
                try:
                    if markup:
                        text = _safe_markup_text(obj, style)
                    elif _ANSI_RE.search(obj):
                        text = Text.from_ansi(obj)
                    else:
                        text = Text(obj, style=style)
                except Exception:  # noqa: BLE001
                    text = Text(obj, style=style)
                widgets.append(Static(text, classes="log_msg"))
            else:
                try:
                    widgets.append(Static(obj, classes="log_msg"))
                except Exception:  # noqa: BLE001
                    widgets.append(Static(str(obj), markup=True, classes="log_msg"))
        return widgets

    def _scroll_log_to_end(self, log=None) -> None:
        """Tempel tampilan ke baris paling baru (follow-bottom).

        mount() Textual bersifat async: widget baru belum punya tinggi saat
        scroll_end() dipanggil sinkron tepat sesudahnya, sehingga scroll
        berhenti di bawah-versi-lama dan user harus scroll manual. Karena
        itu scroll dilakukan 2x: langsung (best-effort) + dijadwalkan ulang
        via call_after_refresh agar jalan setelah layout selesai.
        """
        try:
            target = log if log is not None else self.query_one("#log", ScrollableContainer)
        except Exception:  # noqa: BLE001
            return
        try:
            target.scroll_end(animate=False, immediate=True, x_axis=False)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            def _follow() -> None:
                try:
                    target.scroll_end(animate=False)
                except Exception:  # noqa: BLE001, S110
                    pass
            self.call_after_refresh(_follow)
        except Exception:  # noqa: BLE001, S110
            pass

    def _append_log_widgets(self, widgets) -> None:
        if not widgets:
            return
        log = self.query_one("#log", ScrollableContainer)
        log.mount(*widgets)
        children = log.children
        if len(children) > _MAX_LOG_WIDGETS:
            excess = len(children) - _LOG_TRIM_TARGET
            removed = 0
            for w in list(children):
                if removed >= excess:
                    break
                if getattr(w, "_pruning", False):
                    continue
                w.remove()
                removed += 1
        self._scroll_log_to_end(log)

    def _drain_queue(self) -> None:
        if self._draining:
            return
        self._draining = True
        try:
            widgets = []
            while True:
                try:
                    kind, payload = state._TUI_QUEUE.get_nowait()  # type: ignore[union-attr]
                except queue.Empty:
                    break
                try:
                    if kind == _RENDER_KIND:
                        objects, style, markup, sep, _end = payload
                        widgets.extend(self._make_log_widgets(objects, style, markup, sep))
                    elif kind == _RULE_KIND:
                        title, style = payload
                        widgets.append(Static(Rule(title, style=style), classes="log_rule"))
                    elif kind == _STATUS_KIND:
                        self._set_status(payload)
                    elif kind == _THINK_KIND:
                        self._set_thinking(payload)
                    elif kind == _PASSWORD_KIND:
                        rid, message = payload
                        self._request_password_ui(rid, message)
                    elif kind == _QUESTION_KIND:
                        rid, query, options, default = payload
                        self._request_question_ui(rid, query, options, default)
                    elif kind == _CONFIRM_KIND:
                        rid, reason, detail, label = payload
                        self._request_confirm_ui(rid, reason, detail, label)
                except Exception as exc:  # noqa: BLE001
                    # satu payload rusak tidak boleh menjatuhkan seluruh TUI
                    try:
                        widgets.append(Static(f"[{_color_error()}]\u26a0 Render error ({kind}): {escape(str(exc))}[/{_color_error()}]", classes="log_msg"))
                    except Exception:  # noqa: BLE001, S110
                        pass
            if widgets:
                try:
                    self._append_log_widgets(widgets)
                except Exception as exc:  # noqa: BLE001
                    try:
                        log = self.query_one("#log", ScrollableContainer)
                        log.mount(Static(f"[{_color_error()}]\u26a0 Render batch error: {escape(str(exc))}[/{_color_error()}]", classes="log_msg"))
                        self._scroll_log_to_end(log)
                    except Exception:  # noqa: BLE001, S110
                        pass
        finally:
            self._draining = False
        # Saat tugas berjalan, konteks bertambah dari tool call/result — segarkan
        # header (maks 1x/detik agar tidak membebani; hitungan token itu iteratif).
        if self._busy:
            now = time.monotonic()
            if now - self._topbar_refresh_ts >= 1.0:
                self._topbar_refresh_ts = now
                try:
                    self._refresh_topbar()
                except Exception:  # noqa: BLE001, S110
                    pass

    def _request_password_ui(self, rid: str, message: str) -> None:
        self._pw_request_id = rid
        self._pw_dialog.set_prompt(message)
        self._pw_dialog.display = True
        self.call_after_refresh(self._pw_dialog.focus_input)

    def _hide_password_ui(self) -> None:
        self._pw_request_id = None
        self._pw_dialog.display = False

    @on(_PasswordDialog.Submitted)
    def _on_password_submitted(self, event: _PasswordDialog.Submitted) -> None:
        rid = self._pw_request_id
        self._hide_password_ui()
        state._resolve_tui_password(rid, event.value)

    @on(_PasswordDialog.Cancelled)
    def _on_password_cancelled(self, event: _PasswordDialog.Cancelled) -> None:
        rid = self._pw_request_id
        self._hide_password_ui()
        state._resolve_tui_password(rid, None)

    def _request_question_ui(self, rid: str, query: str, options: list, default: str) -> None:
        self._q_request_id = rid
        self._q_dialog.set_prompt(query, options, default)
        self._q_dialog.display = True
        self.call_after_refresh(self._q_dialog.focus_input)

    def _hide_question_ui(self) -> None:
        self._q_request_id = None
        self._q_dialog.display = False

    @on(_QuestionDialog.Submitted)
    def _on_question_submitted(self, event: _QuestionDialog.Submitted) -> None:
        rid = self._q_request_id
        self._hide_question_ui()
        state._resolve_tui_question(rid, event.value)

    @on(_QuestionDialog.Cancelled)
    def _on_question_cancelled(self, event: _QuestionDialog.Cancelled) -> None:
        rid = self._q_request_id
        self._hide_question_ui()
        state._resolve_tui_question(rid, None)

    def _request_confirm_ui(self, rid: str, reason: str, detail: str, label: str) -> None:
        self._c_request_id = rid
        self._c_dialog.set_prompt(reason, detail, label)
        self._c_dialog.display = True
        self.call_after_refresh(self._c_dialog.focus_input)

    def _hide_confirm_ui(self) -> None:
        self._c_request_id = None
        self._c_dialog.display = False

    @on(_ConfirmDialog.Submitted)
    def _on_confirm_submitted(self, event: _ConfirmDialog.Submitted) -> None:
        rid = self._c_request_id
        self._hide_confirm_ui()
        state._resolve_tui_confirm(rid, event.value)

    @on(_ConfirmDialog.Cancelled)
    def _on_confirm_cancelled(self, event: _ConfirmDialog.Cancelled) -> None:
        rid = self._c_request_id
        self._hide_confirm_ui()
        state._resolve_tui_confirm(rid, None)

    @on(JokiInput.Submitted)
    def _on_prompt_submitted(self, event: JokiInput.Submitted) -> None:
        self._handle_submit_text(event.text)

    @on(OptionList.OptionSelected)
    def _on_cmd_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_cmd_option()

    @on(TextArea.Changed)
    def _on_prompt_changed(self, event: TextArea.Changed) -> None:
        self._update_cmd_menu()

    def _cmd_menu_state(self):
        """Tentukan isi dropdown slash-command berdasarkan isi prompt.

        Mengembalikan (phase, options) atau None jika menu harus disembunyikan.
        phase: 'cmd'  -> daftar perintah slash
               'model' -> sub-opsi daftar model untuk /model
               'lsp'   -> sub-opsi daftar LSP server untuk /install-lsp
        """
        line = self._prompt.text.split("\n", 1)[0]
        if not line.startswith("/"):
            return None
        parts = line.split(" ", 1)
        token = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        has_arg = rest != ""

        if token == "/model":
            subs = _model_sub_options()
            if not subs:
                return None
            if has_arg:
                q = rest.strip()
                if any(k == q for k, _ in subs):
                    return None
                matches = [(k, n) for k, n in subs if q in k or q.lower() in n.lower()]
            else:
                matches = subs
            if not matches:
                return None
            return ("model", matches)

        if token == "/themes":
            subs = _theme_sub_options()
            if has_arg:
                q = rest.strip()
                if any(k == q for k, _ in subs):
                    return None
                matches = [(k, n) for k, n in subs if q in k or q.lower() in n.lower()]
            else:
                matches = subs
            if not matches:
                return None
            return ("theme", matches)

        if token == "/skills":
            from joki.skills import list_skills
            try:
                ids = [(r["id"], r["name"]) for r in list_skills()]
            except Exception:  # noqa: BLE001
                ids = []
            ids = sorted(ids)
            subs = [("list", "tampilkan semua skill"), ("on", "nyalakan skill"),
                    ("off", "matikan skill"), ("reload", "baca ulang file skill")]
            parts = rest.strip().split() if rest.strip() else []
            trailing_space = bool(rest) and rest[-1].isspace()
            if not parts:
                matches = subs + ids
            elif len(parts) == 1 and not trailing_space:
                # Argumen pertama: hanya sub-perintah (prefix match).
                q = parts[0]
                if any(k == q for k, _ in subs):
                    return None
                matches = [(k, n) for k, n in subs if k.startswith(q)]
            else:
                # Argumen kedua: hanya id skill, dan hanya setelah on/off.
                if parts[0] not in ("on", "off"):
                    return None
                last = "" if trailing_space else parts[-1]
                if last and any(k == last for k, _ in ids):
                    return None
                matches = [(k, n) for k, n in ids if k.startswith(last)]
            if not matches:
                return None
            return ("skills", matches)

        if token == "/install-lsp":
            subs = _lsp_sub_options()
            if not subs:
                return None
            if has_arg:
                q = rest.strip()
                if any(k == q for k, _ in subs):
                    return None
                matches = [(k, n) for k, n in subs if q in k or q.lower() in n.lower()]
            else:
                matches = subs
            if not matches:
                return None
            return ("lsp", matches)

        if token in ("/resume", "/lanjut"):
            subs = _session_sub_options()
            if not subs:
                return None
            if has_arg:
                q = rest.strip()
                if any(k == q for k, _ in subs):
                    return None
                matches = [(k, n) for k, n in subs if q in k or q.lower() in n.lower()]
            else:
                matches = subs
            if not matches:
                return None
            return ("resume", matches)

        cmd_names = [c for c, _ in _SLASH_COMMANDS]
        if token in cmd_names:
            return None
        matches = [(c, d) for c, d in _SLASH_COMMANDS if c.startswith(token)]
        if not matches:
            return None
        return ("cmd", matches)

    def _update_cmd_menu(self) -> None:
        menu = self._cmd_menu
        st = self._cmd_menu_state()
        if st is None:
            menu.display = False
            menu.clear_options()
            return
        phase, options = st
        menu.clear_options()
        if phase in ("model", "theme", "lsp", "skills", "resume"):
            for key, name in options:
                # Label bisa memuat data pengguna (mis. first_user session) —
                # '[kata]' di dalamnya bukan style Rich dan dulu menjatuhkan
                # app saat menu dirender.
                menu.add_option(Option(escape(f"{key}  —  {name}"), id=key))
            menu._joki_phase = phase  # type: ignore[attr-defined]
        else:
            for cmd, desc in options:
                menu.add_option(Option(f"{cmd}  —  {desc}", id=cmd))
            menu._joki_phase = "cmd"  # type: ignore[attr-defined]
        menu.display = True
        if menu.option_count > 0:
            menu.highlighted = 0

    def _select_cmd_option(self) -> None:
        menu = self._cmd_menu
        if not menu.display or menu.option_count == 0:
            return
        idx = menu.highlighted if menu.highlighted is not None else 0
        option = menu.get_option_at_index(idx)
        fill = option.id
        phase = getattr(menu, "_joki_phase", "cmd")
        if phase == "model":
            new_value = f"/model {fill}"
        elif phase == "skills":
            # /skills dua level: sub-perintah (on/off/...) lalu id skill.
            # on/off yang sudah dipilih WAJIB dipertahankan saat id dipilih,
            # kalau tidak perintah jadi "/skills <id>" dan ditolak dengan
            # "Sub-perintah tak dikenal".
            token = self._prompt.text.split(" ", 1)[0].lower()
            args = self._prompt.text.split()[1:]
            if fill in ("list", "on", "off", "reload"):
                # Spasi akhir agar menu id langsung muncul di level 2.
                new_value = f"{token} {fill} "
            elif args and args[0] in ("on", "off"):
                new_value = f"{token} {args[0]} {fill}"
            else:
                new_value = f"{token} {fill}"
        elif phase in ("theme", "lsp", "resume"):
            token = self._prompt.text.split(" ", 1)[0].lower()
            new_value = f"{token} {fill}"
        else:
            new_value = (fill or "") + " "
        self._prompt.text = new_value
        self._prompt.move_cursor((0, len(new_value)))
        self._prompt.focus()

    def _hide_cmd_menu(self) -> None:
        self._cmd_menu.display = False
        self._cmd_menu.clear_options()
        self._prompt.focus()

    def _handle_submit_text(self, text: str) -> None:
        if self._busy:
            self._prompt.insert(text)
            self._set_status(f"[{_color_warn()}]Tunggu sampai tugas selesai sebelum kirim pesan baru.[/{_color_warn()}]")
            return
        text = text.strip()
        if not text:
            return
        self._set_busy(True)
        self.run_worker(
            lambda: self._process_submission(text),
            thread=True,
            group="task",
            exclusive=True,
        )

    def _process_submission(self, text: str) -> None:
        exiting = False
        try:
            if text.startswith("/"):
                cont, self.messages = cli._handle_command(text, self.messages)
                self.call_from_thread(self._apply_theme)
                self.call_from_thread(self._refresh_prompt_meta)
                # Command bisa mengubah messages/model (mis. /baru, /resume,
                # /model) — header token harus ikut diperbarui.
                self.call_from_thread(self._refresh_topbar)
                if not cont:
                    exiting = True
                    self.call_from_thread(self.exit)
                    return
            else:
                _console.print(_message_card(Markdown(text), f"[bold {_color_warn()}]USER[/bold {_color_warn()}]"))
                self.messages.append({"role": "user", "content": text})
                cli.agent_loop(self.messages)
        except Exception as exc:  # noqa: BLE001
            _console.print(f"[{_color_error()}]Error: {escape(str(exc))}[/{_color_error()}]")
        finally:
            if not exiting:
                try:
                    self.call_from_thread(self._set_busy, False)
                except Exception:  # noqa: BLE001, S110
                    pass

    def _apply_theme(self) -> None:
        self.theme = self._resolve_tui_theme()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._set_status("[bold]Joki sedang bekerja... (Esc / Ctrl+C untuk membatalkan)[/bold]")
        else:
            self._set_status("")
            if self._pw_request_id is not None:
                state._resolve_tui_password(self._pw_request_id, None)
                self._hide_password_ui()
            if self._q_request_id is not None:
                state._resolve_tui_question(self._q_request_id, None)
                self._hide_question_ui()
            if self._c_request_id is not None:
                state._resolve_tui_confirm(self._c_request_id, None)
                self._hide_confirm_ui()
        self._refresh_topbar()
        self._refresh_prompt_meta()

    def action_cancel(self) -> None:
        menu = getattr(self, "_cmd_menu", None)
        if menu is not None and menu.display:
            self._hide_cmd_menu()
            return
        if self._pw_request_id is not None and self._pw_dialog.display:
            state._resolve_tui_password(self._pw_request_id, None)
            self._hide_password_ui()
            return
        if self._q_request_id is not None and self._q_dialog.display:
            state._resolve_tui_question(self._q_request_id, None)
            self._hide_question_ui()
            return
        if self._c_request_id is not None and self._c_dialog.display:
            state._resolve_tui_confirm(self._c_request_id, None)
            self._hide_confirm_ui()
            return
        if self._busy:
            _joki_cancel.set()
            self._set_status(f"[{_color_warn()}]Membatalkan...[/{_color_warn()}]")
        else:
            self.exit()

    def on_unmount(self) -> None:
        if self._owns_queue:
            state._tui_stop()


def run_tui(messages, initial_input=None):
    """Jalankan TUI. Harus dipanggil dari cli.main() setelah TTY terdeteksi.

    `state._tui_start` membuat _console menulis ke queue (bukan stdout). Selama TUI,
    sys.stdout/stderr diarahkan ke sink supaya sisa print() tidak merusak layar.
    """
    q = queue.Queue()
    state._tui_start(q, dark=state._resolve_theme())
    real_stdout = sys.stdout
    real_stderr = sys.stderr
    sys.stdout = _QueueStdout(real_stdout)
    sys.stderr = _QueueStdout(real_stderr)
    try:
        app = JokiApp(messages)
        app._initial_input = initial_input
        app.run()
    finally:
        sys.stdout = real_stdout
        sys.stderr = real_stderr
        state._tui_stop()
