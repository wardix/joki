"""Tool `question`: bertanya terstruktur ke manusia dan tunggu jawaban.

Beda dengan teks bebas: hasil tool ini tercatat sebagai bukti klarifikasi,
sehingga menutup lubang "AI menebak tanpa bertanya".
"""
import os
import sys

from joki.display import _pause_spinner, _resume_spinner
from joki.state import _MONITOR_PAUSED, _console


def _normalize_answer(ans, options, default):
    """Samakan jawaban mentah jadi jawaban final: nomor -> opsi, kosong ->
    default -> penanda tak-menjawab."""
    ans = str(ans or "").strip()
    if not ans and default:
        ans = str(default)
    if ans.isdigit() and options:
        idx = int(ans)
        if 1 <= idx <= len(options):
            ans = options[idx - 1]
    if not ans:
        ans = "(user tidak menjawab)"
    return ans


def _question_timeout():
    try:
        return max(10.0, float(os.getenv("JOKI_QUESTION_TIMEOUT", "300")))
    except (TypeError, ValueError):
        return 300.0


def handle_question(args):
    query = (args or {}).get("query", "") or (args or {}).get("question", "")
    options = (args or {}).get("options", []) or []
    default = (args or {}).get("default", "")
    query = str(query).strip()
    if not query:
        return "Error: Parameter 'query' wajib diisi. Contoh: question(query=\"File mana yang dimaksud?\", options=[\"a.py\", \"b.py\"])"
    if not isinstance(options, list):
        options = [str(options)]
    options = [str(o).strip() for o in options if str(o).strip()][:5]

    # Mode otomasi/test: jawab via env agar tidak blokir.
    auto = os.getenv("JOKI_QUESTION_ANSWER")
    if auto is not None:
        return f"Jawaban user (otomatis via env): {auto}"

    try:
        from joki.state import _TUI_ACTIVE
    except Exception:  # noqa: BLE001
        _TUI_ACTIVE = False
    if _TUI_ACTIVE:
        try:
            from joki.state import _request_tui_question
        except Exception:  # noqa: BLE001
            _request_tui_question = None
        if _request_tui_question is not None:
            ans = _request_tui_question(query, options, default, timeout=_question_timeout())
            if ans is None:
                return "Jawaban user: (tidak dijawab — dibatalkan atau waktu habis)"
            return f"Jawaban user: {_normalize_answer(ans, options, default)}"
        opts = f" Opsi: {', '.join(options)}" if options else ""
        return (
            "[TUI] question tool tidak tersedia di mode TUI — "
            f"tulis pertanyaan sebagai teks dan tunggu user. Pertanyaan: {query}.{opts}"
        )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        opts = f" Opsi: {', '.join(options)}" if options else ""
        return (
            "[NON-INTERAKTIF] Tidak bisa bertanya (bukan TTY). "
            f"Pertanyaan tertunda: {query}.{opts} "
            "Lanjutkan dengan asumsi paling aman dan laporkan asumsi itu."
        )

    try:
        _pause_spinner()
        _MONITOR_PAUSED.set()
        try:
            _console.print(f"[bold yellow]❓ {query}[/bold yellow]")
            if options:
                for i, opt in enumerate(options, 1):
                    _console.print(f"  {i}. {opt}")
                _console.print("[dim]Ketik nomor opsi, atau tulis jawaban sendiri. Kosong = default.[/dim]")
            prompt = f"  Jawab{f' [{default}]' if default else ''}: "
            ans = input(prompt).strip()
            ans = _normalize_answer(ans, options, default)
        finally:
            _MONITOR_PAUSED.clear()
        _resume_spinner()
        return f"Jawaban user: {ans}"
    except (EOFError, KeyboardInterrupt):
        try:
            _MONITOR_PAUSED.clear()
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            _resume_spinner()
        except Exception:  # noqa: BLE001, S110
            pass
        return "Jawaban user: (dibatalkan)"
