import queue
import sys
import time

from rich import box
from rich.live import Live
from rich.panel import Panel

from joki import state
from joki.display import _color_info
from joki.state import _IS_TTY, _THINK_KIND, _bridge_send, _console, _joki_card

_THROTTLE = 0.25

class ThinkingDisplay:
    def __init__(self, disabled=False):
        self._queue = queue.Queue()
        self._buf = ""
        self._thinking_text = ""
        self._live = None
        self._disabled = disabled
        self._last_send = 0.0

    def push(self, text):
        if self._disabled:
            return
        self._queue.put(text)

    def __enter__(self):
        if self._disabled:
            return self
        self._buf = ""
        self._thinking_text = ""
        self._last_send = 0.0
        if state._TUI_ACTIVE:
            _bridge_send(_THINK_KIND, "\u23f3 Menunggu respons...")
            return self
        if _IS_TTY:
            self._live = Live(
                Panel("⏳ Menunggu respons...", border_style="blue", title=f"[bold {_color_info()}]\U0001f9e0 Joki Berpikir[/bold {_color_info()}]", box=box.ROUNDED),
                refresh_per_second=10,
                console=_console,
                transient=True
            )
            self._live.__enter__()
        return self

    def update(self):
        if self._disabled:
            return

        while True:
            try:
                token = self._queue.get_nowait()
                self._buf += token
                self._thinking_text += token
            except queue.Empty:
                break

        current = self._buf.strip() or self._thinking_text.strip()

        if state._TUI_ACTIVE:
            if not current:
                display = "\u23f3 Menunggu respons..."
            else:
                display = current[-600:]
            now = time.time()
            if now - self._last_send >= _THROTTLE:
                self._last_send = now
                _bridge_send(_THINK_KIND, display)
            return
        if not _IS_TTY or self._live is None:
            return

        if not current:
            display = "⏳ Menunggu respons..."
        else:
            display = current[-600:]

        self._live.update(Panel(
            display,
            border_style="blue",
            title=f"[bold {_color_info()}]\U0001f9e0 Joki Berpikir[/bold {_color_info()}]",
            box=box.ROUNDED
        ))

    def __exit__(self, *args):
        if self._disabled:
            return
        # Drain any remaining tokens from queue
        while True:
            try:
                token = self._queue.get_nowait()
                self._thinking_text += token
            except queue.Empty:
                break

        if self._live is not None:
            self._live.__exit__(*args)
            self._live = None

        if state._TUI_ACTIVE:
            _bridge_send(_THINK_KIND, "")
            if self._thinking_text.strip():
                _console.print(_joki_card(
                    self._thinking_text.strip()[-2000:],
                    "\U0001f9e0 Joki Berpikir (selesai)",
                    "#fea62b",
                ))
            return

        if not _IS_TTY:
            if self._thinking_text.strip():
                sys.stdout.write(f"[JOKI THINK] {self._thinking_text.strip()[-500:]}\n")
            sys.stdout.flush()
            return

        if self._thinking_text.strip():
            _console.print(Panel(
                self._thinking_text.strip()[-2000:],
                border_style="blue",
                title=f"[bold {_color_info()}]\U0001f9e0 Joki Berpikir (selesai)[/bold {_color_info()}]",
                box=box.ROUNDED
            ))

    def start(self):
        pass

    def finish(self):
        pass
