"""Test question tool: validasi, REPL, env-bypass, dan dialog TUI."""
import io
import queue as _queue
import sys
import threading

from joki import state
from joki.tools.question import _normalize_answer, handle_question


def test_missing_query():
    assert handle_question({}).startswith("Error:")
    assert handle_question({"options": ["a"]}).startswith("Error:")


def test_normalize_answer():
    assert _normalize_answer("2", ["a.py", "b.py"], "") == "b.py"
    assert _normalize_answer("9", ["a.py"], "") == "9"
    assert _normalize_answer("", ["a.py"], "a.py") == "a.py"
    assert _normalize_answer("", [], "") == "(user tidak menjawab)"


def test_env_bypass(monkeypatch):
    monkeypatch.setenv("JOKI_QUESTION_ANSWER", "a.py")
    assert handle_question({"query": "Pilih?"}) == "Jawaban user (otomatis via env): a.py"


def _fake_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


def test_repl_option_number(monkeypatch):
    _fake_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")
    assert handle_question({"query": "Pilih?", "options": ["a.py", "b.py"]}) == "Jawaban user: b.py"


def test_repl_default(monkeypatch):
    _fake_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert handle_question({"query": "Pilih?", "options": ["a"], "default": "a"}) == "Jawaban user: a"


def test_non_tty_fallback(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    out = handle_question({"query": "Pilih?", "options": ["a"]})
    assert out.startswith("[NON-INTERAKTIF]")


def test_tui_inactive_returns_none():
    from joki import state
    assert state._TUI_ACTIVE is False
    assert state._request_tui_question("Q?", ["a"], "", timeout=1) is None


def test_request_id_unique_under_contention():
    """Regresi race counter: rid kembar = jawaban tertukar/worker gantung."""
    import sys

    from joki import state
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # paksa preemption agresif
    try:
        n_threads, n_iter = 16, 1000
        seen, lock = set(), threading.Lock()

        def worker(prefix):
            for _ in range(n_iter):
                rid = state._next_request_id(prefix)
                with lock:
                    seen.add(rid)

        ths = [threading.Thread(target=worker, args=("pw" if i % 2 else "q",))
               for i in range(n_threads)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in ths)
        assert len(seen) == n_threads * n_iter
        assert all(r.startswith(("pw", "q")) for r in seen)
    finally:
        sys.setswitchinterval(old_interval)


def test_concurrent_questions_routed_correctly(monkeypatch):
    """16 worker bersamaan, tiap jawaban harus sampai ke worker yang tepat."""
    import queue as _q

    from joki import state
    monkeypatch.setattr(state, "_TUI_ACTIVE", True)
    q = _q.Queue()
    monkeypatch.setattr(state, "_TUI_QUEUE", q)
    stop = threading.Event()

    def drainer():
        while not stop.is_set():
            try:
                kind, payload = q.get(timeout=0.1)
            except Exception:  # noqa: BLE001, S112
                continue
            if kind == state._QUESTION_KIND:
                rid, query, _opts, _default = payload
                state._resolve_tui_question(rid, f"ans-untuk-{query}")

    results, rlock = {}, threading.Lock()

    def worker(i):
        ans = state._request_tui_question(f"Q{i}?", [], "", timeout=15)
        with rlock:
            results[i] = ans

    d = threading.Thread(target=drainer, daemon=True)
    d.start()
    try:
        ths = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=20)
        assert not any(t.is_alive() for t in ths), "worker gantung (rid kembar?)"
        assert len(results) == 16
        for i in range(16):
            assert results[i] == f"ans-untuk-Q{i}?", results[i]
    finally:
        stop.set()
        d.join(timeout=5)


def test_tui_question_timeout_no_app(monkeypatch):
    """Tanpa drain UI, request harus kembali None setelah timeout, tidak gantung."""
    import time
    monkeypatch.setattr(state, "_TUI_ACTIVE", True)
    monkeypatch.setattr(state, "_TUI_QUEUE", _queue.Queue())
    t0 = time.time()
    try:
        assert state._request_tui_question("Q?", ["a"], "", timeout=0.6) is None
    finally:
        monkeypatch.undo()
    assert time.time() - t0 < 5


def _run_tui_question_flow(answer_action):
    """Jalankan app TUI headless, panggil handle_question dari worker thread,
    lakukan aksi UI, kembalikan jawaban tool."""
    import asyncio

    from joki.tui import JokiApp

    result = {}

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            assert state._TUI_ACTIVE is True

            def worker():
                result["out"] = handle_question(
                    {"query": "File mana?", "options": ["a.py", "b.py"]})

            th = threading.Thread(target=worker, daemon=True)
            th.start()
            for _ in range(100):
                await pilot.pause(0.1)
                if getattr(app, "_q_request_id", None) and app._q_dialog.display:
                    break
            assert app._q_dialog.display, "dialog pertanyaan tidak muncul"
            await answer_action(app, pilot)
            th.join(timeout=10)
            assert not th.is_alive(), "worker gantung setelah dialog dijawab"

    asyncio.run(run())
    return result.get("out")


def test_tui_question_option_submit():
    """Pilih opsi pertama via OptionList -> jawaban = teks opsi."""
    from textual.widgets import OptionList

    async def answer(app, pilot):
        opts = app._q_dialog.query_one("#q_opts", OptionList)
        opts.focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.3)

    out = _run_tui_question_flow(answer)
    assert out == "Jawaban user: a.py", out


def test_tui_question_free_text_submit():
    """Ketik teks bebas di input -> jawaban = teks tersebut."""
    async def answer(app, pilot):
        app._q_dialog.focus_input()
        await pilot.pause(0.2)
        for ch in "custom.py":
            await pilot.press(ch)
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.3)

    out = _run_tui_question_flow(answer)
    assert out == "Jawaban user: custom.py", out
