import asyncio
import queue
import subprocess
import threading
import types

import pytest

from joki import cli, state
from joki.display import _console, _Spinner


def _tui_started():
    return state._TUI_ACTIVE and state._TUI_QUEUE is not None


def _log_plain(app):
    """Gabungkan semua teks Static di dalam #log (pengganti log.lines RichLog)."""
    from rich.text import Text
    from textual.visual import Visual
    from textual.widgets import Static
    log = app.query_one("#log")
    parts = []
    for w in log.query(Static):
        content = w.content
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, Text):
            parts.append(content.plain)
        else:
            try:
                strips = Visual.to_strips(w, w.visual, 200, None, w.visual_style)
                parts.append("\n".join(s.text for s in strips))
            except Exception:  # noqa: BLE001
                parts.append(str(content))
    return "\n".join(parts)


@pytest.fixture
def tui_queue():
    """Aktifkan mode TUI + pasang queue nyata, bersihkan setelah selesai."""
    q = queue.Queue()
    state._tui_start(q, dark=True)
    yield q
    state._tui_stop()


# ============================================================
# _handle_command (diekstrak dari main loop cli)
# ============================================================

def test_handle_command_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msgs = [{"role": "system", "content": "sys"}]
    cont, new_msgs = cli._handle_command("/keluar", msgs)
    assert cont is False
    assert new_msgs == msgs


def test_handle_command_baru(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "halo"},
    ]
    try:
        cont, new_msgs = cli._handle_command("/baru", msgs)
        assert cont is True
        assert [m["role"] for m in new_msgs] == ["system"]
        assert cli._CURRENT_SESSION.startswith("session_")
    finally:
        # Jangan bocorkan session global ke test lain (bisa memicu autosave
        # menulis file session nyata di data dir pengguna).
        cli._CURRENT_SESSION = None
        state._set_current_session(None)


def test_handle_command_unknown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msgs = [{"role": "system", "content": "sys"}]
    cont, new_msgs = cli._handle_command("/halo_dunia", msgs)
    assert cont is True
    assert new_msgs == msgs


def test_handle_command_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msgs = [{"role": "system", "content": "sys"}]
    cont, new_msgs = cli._handle_command("/model", msgs)
    assert cont is True
    assert new_msgs == msgs


# ============================================================
# JokiInput: enter = submit, alt+enter = newline
# ============================================================

def test_input_enter_submits():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            received = []
            app._prompt.focus()
            app._prompt.text = "halo dunia"

            def spy(text):
                received.append(text)

            app._handle_submit_text = spy
            await pilot.press("enter")
            await pilot.pause()
            assert received == ["halo dunia"], received
            assert app._prompt.text == "", "prompt harus kosong setelah submit"

    asyncio.run(run())


def test_input_alt_enter_inserts_newline():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._prompt.text = ""
            await pilot.press("alt+enter")
            await pilot.pause()
            assert "\n" in app._prompt.text, repr(app._prompt.text)

    asyncio.run(run())


def test_input_empty_not_submitted():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            received = []
            app._prompt.text = "   "

            def spy(text):
                received.append(text)

            app._handle_submit_text = spy
            await pilot.press("enter")
            await pilot.pause()
            assert received == []

    asyncio.run(run())


# ============================================================
# JokiInput: panah atas/bawah = riwayat prompt (ala opencode)
# ============================================================

def test_input_history_up_down_navigation():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._handle_submit_text = lambda t: None

            app._prompt.text = "perintah satu"
            await pilot.press("enter")
            await pilot.pause()
            app._prompt.text = "perintah dua"
            await pilot.press("enter")
            await pilot.pause()
            assert app._prompt.text == ""

            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "perintah dua"
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "perintah satu"
            # di ujung paling lama, panah atas tetap tahan di situ
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "perintah satu"
            await pilot.press("down")
            await pilot.pause()
            assert app._prompt.text == "perintah dua"
            await pilot.press("down")
            await pilot.pause()
            assert app._prompt.text == ""

            # kursor ditaruh di akhir teks setelah memuat item riwayat
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.cursor_location == (0, len("perintah dua"))

    asyncio.run(run())


def test_input_history_down_restores_draft():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._handle_submit_text = lambda t: None

            app._prompt.text = "q1"
            await pilot.press("enter")
            await pilot.pause()
            app._prompt.text = "draft yang belum dikirim"
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "q1"
            await pilot.press("down")
            await pilot.pause()
            assert app._prompt.text == "draft yang belum dikirim"

            # panah bawah tanpa navigasi tidak menyerobot teks
            await pilot.press("down")
            await pilot.pause()
            assert app._prompt.text == "draft yang belum dikirim"

    asyncio.run(run())


def test_input_up_multiline_moves_cursor_before_history():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._handle_submit_text = lambda t: None

            app._prompt.text = "q1"
            await pilot.press("enter")
            await pilot.pause()

            app._prompt.text = "baris satu\nbaris dua"
            app._prompt.move_cursor((1, 3))
            await pilot.pause()
            await pilot.press("up")
            await pilot.pause()
            # cursor di baris 2 -> panah atas hanya pindah baris dulu
            assert app._prompt.text == "baris satu\nbaris dua"
            assert app._prompt.cursor_location[0] == 0
            # sudah di baris pertama -> baru masuk riwayat
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "q1"
            # panah bawah kembali ke draft multiline
            await pilot.press("down")
            await pilot.pause()
            assert app._prompt.text == "baris satu\nbaris dua"

    asyncio.run(run())


def test_input_history_seeded_from_session_messages():
    from joki.tui import JokiApp

    async def run():
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "prompt lama"},
            {"role": "assistant", "content": "oke"},
            {"role": "user", "content": "[CHECK-IN] pesan internal"},
            {"role": "user", "content": "prompt terakhir"},
        ]
        app = JokiApp(msgs)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "prompt terakhir"
            await pilot.press("up")
            await pilot.pause()
            assert app._prompt.text == "prompt lama"

    asyncio.run(run())


def test_input_up_with_menu_navigates_menu_not_history():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._prompt.text = "/"
            await pilot.pause()
            assert app._cmd_menu.display
            await pilot.press("up")
            await pilot.pause()
            # menu tetap prioritas: prompt tidak diganti isi riwayat
            assert app._prompt.text == "/"
            assert app._cmd_menu.display

    asyncio.run(run())


# ============================================================
# Alur submit → worker → drain → log
# ============================================================

def test_submission_runs_agent_and_drains_to_log(monkeypatch):
    from joki.tui import JokiApp

    calls = {"n": 0}

    def fake_agent(messages, extra=True):
        calls["n"] += 1
        _console.print("Joki menjawab: oke")
        messages.append({"role": "assistant", "content": "Joki menjawab: oke"})

    monkeypatch.setattr(cli, "agent_loop", fake_agent)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            app._handle_submit_text("kerjakan sesuatu")
            await pilot.pause(0.5)
            assert calls["n"] == 1
            assert app._busy is False, "busy harus False setelah tugas selesai"
            assert len(app.messages) == 3, [m["role"] for m in app.messages]
            plain = _log_plain(app)
            assert "kerjakan sesuatu" in plain
            assert "Joki menjawab" in plain

    asyncio.run(run())


def test_busy_guard_keeps_prompt_text(monkeypatch):
    from joki.tui import JokiApp

    def fake_agent(messages, extra=True):
        _console.print("sedang bekerja...")
        messages.append({"role": "assistant", "content": "ok"})

    monkeypatch.setattr(cli, "agent_loop", fake_agent)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            app._set_busy(True)
            app._prompt.focus()
            app._prompt.text = "pesan baru"
            await pilot.press("enter")
            await pilot.pause()
            assert app._prompt.text == "pesan baru", "saat busy, teks prompt tidak boleh hilang"

    asyncio.run(run())


# ============================================================
# Bridge queue → widget (tanpa menjalankan worker)
# ============================================================

def test_bridge_print_drains_to_richlog(tui_queue):
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            log = app.query_one("#log")
            n = len(log.query("Static"))
            _console.print("[bold yellow]USER[/bold yellow]")
            _console.print("teks biasa dengan [dim]markup[/dim]", style="dim")
            await pilot.pause(0.2)
            assert len(log.query("Static")) > n, "queue TUI tidak ter-drain ke log"
            plain = _log_plain(app)
            assert "teks biasa" in plain
            assert "USER" in plain

    asyncio.run(run())


def test_drain_queue_broken_payload_does_not_crash(tui_queue):
    """P1: payload rusak di queue tidak boleh menjatuhkan TUI, dan payload valid
    yang masih tersisa di antrean yang sama tetap harus ter-render."""
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            # payload _RENDER_KIND harus berupa 5-tuple; kirim shape yang salah
            state._bridge_send(state._RENDER_KIND, ("bukan-tuple-yang-benar",))
            # payload valid di antrean yang sama
            state._bridge_send(state._RENDER_KIND, ((("pesan-valid",), None, True, " ", "\n")))
            app._drain_queue()
            await pilot.pause(0.2)
            plain = _log_plain(app)
            assert "pesan-valid" in plain, "payload valid setelah yang rusak harus tetap ter-render"
            assert "Render error" in plain, "error payload rusak harus tampil (bukan silent swallow)"

    asyncio.run(run())


def test_log_widgets_bounded_under_stress(tui_queue):
    """P0: setelah stress test ribuan render event, jumlah widget di #log harus
    dibatasi oleh _MAX_LOG_WIDGETS dan widget paling lama yang ter-trim duluan."""
    from joki.tui import _LOG_TRIM_TARGET, _MAX_LOG_WIDGETS, JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for i in range(3000):
                state._bridge_send(state._RENDER_KIND, ((f"pesan-{i}",), None, True, " ", "\n"))
            app._drain_queue()
            # beri kesempatan proses prune async (Widget.remove) selesai
            await pilot.pause(0.3)
            log = app.query_one("#log")
            n = len(log.children)
            assert n <= _MAX_LOG_WIDGETS, f"widget log tidak dibatasi: {n}"
            assert n <= _LOG_TRIM_TARGET + 5, f"trim tidak berjalan: {n}"
            plain = _log_plain(app)
            assert "pesan-2999" in plain, "widget paling baru harus tetap ada"
            assert "pesan-0" not in plain, "widget paling lama harus sudah ter-trim"

    asyncio.run(run())


def test_log_rewraps_on_resize(tui_queue):
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            _console.print("kata " * 80)
            await pilot.pause(0.2)
            log = app.query_one("#log")
            msg_wide = log.query(".log_msg")[-1]
            wide_height = msg_wide.virtual_size.height

            await pilot.resize_terminal(40, 30)
            await pilot.pause(0.2)
            msg_narrow = log.query(".log_msg")[-1]
            narrow_height = msg_narrow.virtual_size.height

            assert narrow_height > wide_height, (
                "teks panjang harus di-wrap lebih banyak saat window menyempit "
                f"(wide={wide_height}, narrow={narrow_height})"
            )
            assert len(msg_narrow.visual.plain) > 100, "isi pesan tidak boleh terpotong"  # type: ignore[attr-defined]

    asyncio.run(run())


def test_thinking_kind_shows_card(tui_queue):
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            card = app.query_one("#thinking_card")
            state._bridge_send(state._THINK_KIND, "merenung dalam hati")
            await pilot.pause(0.2)
            assert "active" in card.classes, card.classes
            state._bridge_send(state._THINK_KIND, "")
            await pilot.pause(0.2)
            assert "active" not in card.classes

    asyncio.run(run())


def test_spinner_tui_mode_sends_status(tui_queue):
    with _Spinner("Jalankan run_command"):
        pass
    # status "sedang bekerja" terkirim ke queue lalu dibersihkan
    kinds = [kind for kind, _ in list(tui_queue.queue)]
    assert state._STATUS_KIND in kinds
    # dan ada payload status kosong (tanda selesai)
    status_payloads = [p for k, p in list(tui_queue.queue) if k == state._STATUS_KIND]
    assert any(p == "" for p in status_payloads)


def test_thinking_display_tui_sends_cards(tui_queue):
    from joki.thinking import ThinkingDisplay

    with ThinkingDisplay() as thinking:
        thinking.push("langkah 1")
        thinking.update()
    kinds = [kind for kind, _ in list(tui_queue.queue)]
    assert state._THINK_KIND in kinds
    assert state._RENDER_KIND in kinds, "panel Joki Berpikir (selesai) harus jadi render ke log"


# ============================================================
# Dropdown slash-command (_cmd_menu_state)
# ============================================================

def _menu_state(text):
    from types import SimpleNamespace

    from joki.tui import JokiApp

    fake = SimpleNamespace(_prompt=SimpleNamespace(text=text))
    return JokiApp._cmd_menu_state(fake)  # type: ignore[arg-type]


def test_menu_hidden_when_not_slash():
    assert _menu_state("") is None
    assert _menu_state("halo dunia") is None


def test_menu_lists_all_commands_on_slash():
    result = _menu_state("/")
    assert result is not None
    phase, options = result
    assert phase == "cmd"
    cmds = [c for c, _ in options]
    assert "/model" in cmds
    assert "/keluar" in cmds


def test_menu_filters_commands_by_prefix():
    result = _menu_state("/mo")
    assert result is not None
    phase, options = result
    assert phase == "cmd"
    assert [c for c, _ in options] == ["/model"]


def test_menu_unknown_command_hidden():
    assert _menu_state("/foobar") is None


def test_menu_model_suboptions_on_exact_command():
    result = _menu_state("/model")
    assert result is not None
    phase, options = result
    assert phase == "model"
    keys = [k for k, _ in options]
    assert keys and all(not k.startswith("/") for k in keys)


def test_menu_model_suboptions_filtered(monkeypatch):
    import joki.tui as tui
    monkeypatch.setattr(tui, "_model_sub_options", lambda: [
        ("deepseek-v4-flash", "DeepSeek V4 Flash"),
        ("nemotron-3", "Nemotron 3"),
    ])
    result = _menu_state("/model de")
    assert result is not None
    phase, options = result
    assert phase == "model"
    assert options
    for k, n in options:
        assert "de" in k or "de" in n.lower()


def test_menu_hides_after_exact_model_selected(monkeypatch):
    import joki.tui as tui
    monkeypatch.setattr(tui, "_model_sub_options", lambda: [
        ("deepseek-v4-flash-0731-nvidia", "DeepSeek V4 Flash Nvidia"),
    ])
    # key model yang persis di registry -> menu tersembunyi (siap submit)
    assert _menu_state("/model deepseek-v4-flash-0731-nvidia") is None


def test_menu_lsp_suboptions_on_exact_command():
    result = _menu_state("/install-lsp")
    assert result is not None
    phase, options = result
    assert phase == "lsp"
    keys = [k for k, _ in options]
    assert "python" in keys
    assert all(not k.startswith("/") for k in keys)
    for _k, n in options:
        assert "terinstall" in n, "label harus menyertakan status terinstall/belum"


def test_menu_lsp_suboptions_filtered():
    result = _menu_state("/install-lsp py")
    assert result is not None
    phase, options = result
    assert phase == "lsp"
    assert [k for k, _ in options] == ["python"]


def test_menu_hides_after_exact_lsp_selected():
    assert _menu_state("/install-lsp python") is None


def test_menu_completed_command_hidden():
    assert _menu_state("/baru") is None
    assert _menu_state("/keluar") is None
    assert _menu_state("/reload") is None


# ============================================================
# Perilaku keyboard dropdown (Enter pilih, bukan submit)
# ============================================================

def _press(app, keys):
    """Jalankan rangkaian keypress dengan delay cukup agar menu sempat update."""
    async def go(pilot):
        for k in keys:
            await pilot.press(k)
            await pilot.pause(0.08)

    return go


def _typed_submit_result(keys):
    import asyncio

    from joki.tui import JokiApp

    received = []
    result = {}

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()

            def spy(text):
                received.append(text)

            app._handle_submit_text = spy
            await _press(app, keys)(pilot)
            menu = app._cmd_menu
            result.update(text=app._prompt.text, shown=menu.display, opts=menu.option_count)

    asyncio.run(run())
    return result["text"], result["shown"], result["opts"], received


def test_enter_selects_command_not_submit():
    text, shown, opts, submitted = _typed_submit_result(["/", "enter"])
    # Enter pertama memilih /model (command pertama) -> terisi "/model " + lanjut sub-opsi model
    assert submitted == []
    assert text == "/model "
    assert shown and opts > 0, "menu harus lanjut ke sub-opsi model setelah pilih /model"


def test_select_model_via_enter_then_run():
    _, _, _, submitted = _typed_submit_result(["/", "m", "o", "d", "e", "l", "enter", "enter"])
    assert submitted and submitted[0].startswith("/model ")
    assert submitted[0].strip() != "/model"


def test_select_lsp_via_enter_then_run():
    _, _, _, submitted = _typed_submit_result(
        ["/", "i", "n", "s", "t", "a", "l", "l", "-", "l", "s", "p", "enter", "enter"]
    )
    assert submitted and submitted[0].strip().startswith("/install-lsp ")


def test_escape_closes_menu_without_exit():
    text, shown, _, submitted = _typed_submit_result(["/", "escape"])
    assert text == "/"
    assert not shown
    assert submitted == []


# ============================================================
# textual_compat: patch Alt+Enter untuk Textual 8.2.8
# ============================================================

def test_compat_patch_applies_and_is_idempotent():
    from textual._xterm_parser import XTermParser

    from joki import textual_compat as tc

    assert XTermParser._sequence_to_key_events is tc._patched_sequence_to_key_events
    assert XTermParser._parse_extended_key is tc._patched_parse_extended_key
    tc.apply_textual_compat_patch()
    assert XTermParser._sequence_to_key_events is tc._patched_sequence_to_key_events


def test_compat_legacy_alt_enter_parses_to_alt_enter():
    from textual._xterm_parser import XTermParser

    from joki import textual_compat as tc
    tc.apply_textual_compat_patch()

    parser = XTermParser(debug=False)
    keys = [e.key for e in parser._sequence_to_key_events("\r", alt=True)]
    assert keys == ["alt+enter"]


def test_compat_kitty_alt_enter_parses_to_alt_enter():
    from textual._xterm_parser import XTermParser

    from joki import textual_compat as tc
    tc.apply_textual_compat_patch()

    parser = XTermParser(debug=False)
    key = parser._parse_extended_key("\x1b[13;2u")
    assert key[0].key == "alt+enter"


def test_compat_plain_enter_unaffected():
    from textual._xterm_parser import XTermParser

    from joki import textual_compat as tc
    tc.apply_textual_compat_patch()

    parser = XTermParser(debug=False)
    keys = [e.key for e in parser._sequence_to_key_events("\r", alt=False)]
    assert keys == ["enter"]


# ============================================================
# Prompt password via TUI (sudo)
# ============================================================

def _tui_state():
    from joki import state

    return state


def test_tui_password_request_resolve_handshake():
    from joki import state

    q = queue.Queue()
    state._tui_start(q, dark=True)
    try:
        results = {}

        def worker():
            results["v"] = state._request_tui_password("halo")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        kind, payload = q.get(timeout=2)
        assert kind == state._PASSWORD_KIND
        rid, msg = payload
        assert msg == "halo"
        state._resolve_tui_password(rid, "pw123")
        t.join(timeout=2)
        assert results["v"] == "pw123"
    finally:
        state._tui_stop()


def test_tui_password_request_cancel_returns_none():
    from joki import state

    q = queue.Queue()
    state._tui_start(q, dark=True)
    try:
        results = {}

        def worker():
            results["v"] = state._request_tui_password("batal")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        q.get(timeout=2)
        state._resolve_tui_password("bukan_rid", None)
        state._joki_cancel.set()
        t.join(timeout=2)
        assert results["v"] is None
    finally:
        state._joki_cancel.clear()
        state._tui_stop()


def test_prompt_sudo_uses_tui_dialog_when_tui_active(monkeypatch):
    from joki import state, utils

    q = queue.Queue()
    state._tui_start(q, dark=True)
    monkeypatch.setattr(utils, "_SUDO_PASSWORD", None)
    # Test harus menguji jalur dialog TUI, bukan shortcut "__ROOT__"
    # saat suite kebetulan dijalankan sebagai root.
    monkeypatch.setattr(utils, "_is_admin", lambda: False)
    try:
        monkeypatch.setattr(utils, "_request_tui_password", lambda msg: "pw123")

        def fake_sudo(args, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(utils.subprocess, "run", fake_sudo)
        assert utils._prompt_sudo() == "pw123"
        assert utils._SUDO_PASSWORD == "pw123"
    finally:
        state._tui_stop()
        monkeypatch.setattr(utils, "_SUDO_PASSWORD", None)


def test_prompt_sudo_tui_cancel_returns_none(monkeypatch):
    from joki import state, utils

    q = queue.Queue()
    state._tui_start(q, dark=True)
    monkeypatch.setattr(utils, "_SUDO_PASSWORD", None)
    monkeypatch.setattr(utils, "_is_admin", lambda: False)
    try:
        monkeypatch.setattr(utils, "_request_tui_password", lambda msg: None)
        assert utils._prompt_sudo() is None
    finally:
        state._tui_stop()
        monkeypatch.setattr(utils, "_SUDO_PASSWORD", None)


# ============================================================
# Resolver tema adaptif (config + environment)
# ============================================================

def test_detect_dark_config_light(monkeypatch):
    monkeypatch.setattr(state, "_config_theme_setting", lambda: "light")
    assert state._detect_dark() is False


def test_detect_dark_config_dark(monkeypatch):
    monkeypatch.setattr(state, "_config_theme_setting", lambda: "dark")
    assert state._detect_dark() is True


def test_detect_dark_system_follows_gsettings(monkeypatch):
    monkeypatch.setattr(state, "_config_theme_setting", lambda: "system")
    monkeypatch.setattr(state, "_gsettings_color_scheme", lambda: "dark")
    assert state._detect_dark() is True
    monkeypatch.setattr(state, "_gsettings_color_scheme", lambda: "light")
    assert state._detect_dark() is False


def test_gsettings_default_follows_gtk_theme(monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, *a, **k):
        calls["n"] += 1
        import types
        res = types.SimpleNamespace()
        if "color-scheme" in cmd:
            res.stdout = "'default'"
        else:
            res.stdout = "'Yaru'" if calls["n"] < 3 else "'Yaru-dark'"
        return res

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert state._gsettings_color_scheme() == "light"
    calls["n"] = 2
    assert state._gsettings_color_scheme() == "dark"


def test_detect_dark_system_fallback_osc11(monkeypatch):
    monkeypatch.setattr(state, "_config_theme_setting", lambda: "system")
    monkeypatch.setattr(state, "_gsettings_color_scheme", lambda: None)
    monkeypatch.setattr(state, "_osc11_background", lambda: (18, 18, 22))
    assert state._detect_dark() is True
    monkeypatch.setattr(state, "_osc11_background", lambda: (235, 235, 235))
    assert state._detect_dark() is False


def test_detect_dark_default_is_dark(monkeypatch):
    monkeypatch.setattr(state, "_config_theme_setting", lambda: "system")
    monkeypatch.setattr(state, "_gsettings_color_scheme", lambda: None)
    monkeypatch.setattr(state, "_osc11_background", lambda: None)
    assert state._detect_dark() is True


def test_resolve_theme_caches_result(monkeypatch):
    monkeypatch.setattr(state, "_THEME_DARK", None)
    monkeypatch.setattr(state, "_detect_dark", lambda: True)
    assert state._resolve_theme() is True
    monkeypatch.setattr(state, "_detect_dark", lambda: False)
    assert state._resolve_theme() is True
    monkeypatch.setattr(state, "_THEME_DARK", None)
    assert state._resolve_theme() is False


def test_set_theme_updates_and_persists(tmp_path, monkeypatch):
    import json as _json

    from joki import config

    cfg = tmp_path / "config.json"
    cfg.write_text('{"theme": "system", "models": {}}')
    monkeypatch.setattr(config, "_get_config_path", lambda: str(cfg))
    monkeypatch.setattr(state, "_THEME_DARK", None)

    assert state._set_theme(False) is False
    assert state._resolve_theme() is False
    assert _json.loads(cfg.read_text())["theme"] == "light"

    assert state._set_theme("dark") is True
    assert state._resolve_theme() is True
    assert _json.loads(cfg.read_text())["theme"] == "dark"


def test_handle_command_themes_light(tmp_path, monkeypatch):
    import json as _json

    from joki import config

    cfg = tmp_path / "config.json"
    cfg.write_text('{"theme": "system", "models": {}}')
    monkeypatch.setattr(config, "_get_config_path", lambda: str(cfg))
    monkeypatch.setattr(state, "_THEME_DARK", None)

    msgs = [{"role": "system", "content": "sys"}]
    cont, new_msgs = cli._handle_command("/themes light", msgs)
    assert cont is True
    assert new_msgs == msgs
    assert state._resolve_theme() is False
    assert _json.loads(cfg.read_text())["theme"] == "light"

    cont, _ = cli._handle_command("/themes gelap", msgs)
    assert cont is True
    assert state._resolve_theme() is True
    assert _json.loads(cfg.read_text())["theme"] == "dark"


def test_config_theme_setting_reads_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"theme": "light", "models": {}}')
    from joki import config
    monkeypatch.setattr(config, "_get_config_path", lambda: str(cfg))
    assert state._config_theme_setting() == "light"
    cfg.write_text('{"models": {}}')
    assert state._config_theme_setting() == "system"


def test_cmd_menu_themes_shows_two_options():
    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._prompt.text = "/themes"
            st = app._cmd_menu_state()
            assert st is not None
            phase, options = st
            assert phase == "theme"
            assert [k for k, _ in options] == ["dark", "light"]
            assert [n for _, n in options] == ["Gelap", "Terang"]

            app._update_cmd_menu()
            assert app._cmd_menu.display
            assert app._cmd_menu.option_count == 2

            app._cmd_menu.highlighted = 0
            app._select_cmd_option()
            assert app._prompt.text == "/themes dark"

    asyncio.run(run())


def test_apply_theme_changes_app_dark(monkeypatch):
    from joki.tui import JokiApp

    async def run():
        monkeypatch.setattr(state, "_THEME_DARK", False)
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._apply_theme()
            assert app.theme == "joki-light"
            assert app.current_theme.dark is False
            monkeypatch.setattr(state, "_THEME_DARK", True)
            app._apply_theme()
            assert app.theme == "joki-dark"
            assert app.current_theme.dark is True

    asyncio.run(run())




def test_cmd_menu_skills_phase_levels():
    from types import SimpleNamespace

    from joki import tui as T

    app = T.JokiApp.__new__(T.JokiApp)

    def _keys(text):
        app._prompt = SimpleNamespace(text=text)
        st = T.JokiApp._cmd_menu_state(app)
        return (st[0] if st else None, [k for k, _ in (st[1] if st else [])])

    assert _keys("/skills o") == ("skills", ["on", "off"])
    assert _keys("/skills off r") == ("skills", ["refactor-aman"])
    phase, keys = _keys("/skills on ")
    assert phase == "skills"
    assert keys == ["debug-error", "laravel-proyek", "node-proyek", "refactor-aman", "verifikasi-eksekusi"]
    assert _keys("/skills bogus ") == (None, [])


def test_tui_skills_list_does_not_kill_app():
    import asyncio

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            for text in ("/skills list", "/skills off [x]", "/skills bogus [y]"):
                app._prompt.text = text
                await pilot.press("enter")
                await pilot.pause(1.5)
                assert app.is_running, text

    asyncio.run(run())


def test_cmd_menu_skills_two_level_flow_keeps_on_off():
    """Regresi: /skills -> on -> <id> harus jadi '/skills on <id>'.

    Dulu id yang dipilih menimpa 'on' sehingga perintah terkirim sebagai
    '/skills <id>' dan ditolak 'Sub-perintah tak dikenal'.
    """
    import asyncio

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()

            def _ids():
                return [app._cmd_menu.get_option_at_index(i).id
                        for i in range(app._cmd_menu.option_count)]

            # Level 1: pilih "on" -> prompt harus "/skills on " (spasi akhir).
            app._prompt.text = "/skills "
            app._update_cmd_menu()
            assert app._cmd_menu.display
            assert "on" in _ids()
            app._cmd_menu.highlighted = _ids().index("on")
            app._select_cmd_option()
            assert app._prompt.text == "/skills on "

            # Level 2: pilih id -> on/off wajib dipertahankan.
            app._update_cmd_menu()
            assert app._cmd_menu.display
            assert "laravel-proyek" in _ids()
            app._cmd_menu.highlighted = _ids().index("laravel-proyek")
            app._select_cmd_option()
            assert app._prompt.text == "/skills on laravel-proyek"

    asyncio.run(run())


def test_log_follows_bottom_after_new_output(tui_queue):
    """Regresi UX: log harus menempel ke baris paling baru tanpa scroll manual.

    Dulu scroll_end() dipanggil sinkron tepat setelah mount() (yang async),
    sehingga scroll berhenti di bawah-versi-lama. Kini scroll dijadwalkan
    ulang via call_after_refresh setelah layout selesai.
    """
    import asyncio

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = app.query_one("#log")
            # Isi sampai meluap viewport.
            for i in range(60):
                state._bridge_send(state._RENDER_KIND, ((f"baris-{i}",), None, True, " ", "\n"))
            app._drain_queue()
            await pilot.pause(0.3)
            assert log.max_scroll_y > 0, "konten harus meluap agar scroll teruji"
            assert log.scroll_y >= log.max_scroll_y - 1, (
                f"log tidak menempel bawah: scroll_y={log.scroll_y} max={log.max_scroll_y}"
            )
            # User sempat scroll ke atas, output baru harus narik ke bawah lagi.
            log.scroll_home(animate=False)
            await pilot.pause(0.2)
            for i in range(60, 70):
                state._bridge_send(state._RENDER_KIND, ((f"baris-{i}",), None, True, " ", "\n"))
            app._drain_queue()
            await pilot.pause(0.3)
            assert log.scroll_y >= log.max_scroll_y - 1, (
                f"log tidak kembali ke bawah: scroll_y={log.scroll_y} max={log.max_scroll_y}"
            )

    asyncio.run(run())


# ============================================================
# Regresi: tag style tak dikenal ('[budi]') menutup TUI
# ============================================================
# Kronologi bug: user ketik "/session" + Enter di prompt joki → menu dropdown
# melengkapi ke "/sessions" → Enter kedua menjalankan /sessions → baris daftar
# memuat "[budi]" (basename cwd '/home/budi') → Rich membaca "[budi]" sebagai
# tag style, validasi style ditunda Rich, dan MissingStyle baru meledak saat
# render Textual di layout → exception menutup app (keluar ke terminal).


def _seed_session_budi(tmp_path, monkeypatch):
    """Session tiruan dengan cwd '/home/budi' + first_user ber-bracket."""
    import json as _json

    from joki import sessions as s

    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / "session_budi_1.json").write_text(_json.dumps({
        "session": "session_budi_1",
        "updated": "2026-10-18T06:03:00+07:00",
        "cwd": "/home/budi",
        "model": "m",
        "message_count": 4,
        "first_user": "[login] perbaiki bug",
        "messages": [],
    }))
    return s


def test_safe_markup_text_fallback_style_tak_dikenal():
    from rich.text import Text

    from joki.tui import _safe_markup_text

    t = _safe_markup_text("[budi] halo")
    assert isinstance(t, Text)
    assert t.plain == "[budi] halo", "teks harus tampil apa adanya"
    # Tag valid tetap dirender sebagai style, bukan jatuh ke teks biasa.
    t2 = _safe_markup_text("[bold]halo[/bold]")
    assert t2.plain == "halo"
    assert t2.spans, "markup valid harus tetap jadi span style"


def test_make_log_widgets_style_tak_dikenal_tidak_meledak():
    from joki.tui import JokiApp, _markup_styles_valid

    app = JokiApp.__new__(JokiApp)
    widgets = app._make_log_widgets(("[budi] teks",), None, True, " ")
    assert len(widgets) == 1
    text = widgets[0].content
    assert text.plain == "[budi] teks"
    assert _markup_styles_valid(text), "tidak boleh ada span style tak dikenal"


def test_handle_command_sessions_escape_tag_cwd(tui_queue, tmp_path, monkeypatch):
    from rich.text import Text

    from joki.tui import _markup_styles_valid

    _seed_session_budi(tmp_path, monkeypatch)
    msgs = [{"role": "system", "content": "sys"}]
    cont, _ = cli._handle_command("/sessions", msgs)
    assert cont is True
    payloads = [
        p[0][0] for k, p in list(tui_queue.queue)
        if k == state._RENDER_KIND and p and isinstance(p[0], tuple)
        and p[0] and isinstance(p[0][0], str)
    ]
    body = "\n".join(payloads)
    assert "\\[budi]" in body, f"tag cwd harus di-escape: {body!r}"
    text = Text.from_markup(body)
    assert _markup_styles_valid(text), "tidak boleh ada span style tak dikenal"
    assert "[budi]" in text.plain, "tampilan akhir tetap '[budi]'"


def test_tui_sessions_tidak_mematikan_app(tmp_path, monkeypatch):
    """Regresi langsung laporan user: '/session' + Enter tidak boleh keluar."""
    from joki.tui import JokiApp

    _seed_session_budi(tmp_path, monkeypatch)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            for ch in "/session":
                await pilot.press(ch)
            await pilot.pause(0.2)
            await pilot.press("enter")  # pilih /sessions dari dropdown
            await pilot.pause(0.2)
            await pilot.press("enter")  # submit /sessions
            await pilot.pause(1.2)
            assert app.is_running, "TUI keluar setelah /sessions (regresi style 'budi')"
            plain = _log_plain(app)
            assert "[budi]" in plain, plain

    asyncio.run(run())


def test_status_style_tak_dikenal_tidak_mematikan_app():
    from textual.widgets import Static

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._set_status("[budi] sedang bekerja")
            await pilot.pause(0.3)
            assert app.is_running, "status dengan '[budi]' menutup app"
            status = app.query_one("#status", Static).content
            assert "[budi]" in status.plain

    asyncio.run(run())


def test_menu_resume_label_ber_bracket_tidak_mematikan_app(tmp_path, monkeypatch):
    """Label dropdown /resume memuat first_user — '[kata]' di dalamnya dulu
    juga berpotensi menjatuhkan render menu."""
    from joki.tui import JokiApp

    _seed_session_budi(tmp_path, monkeypatch)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            app._prompt.text = "/resume"
            app._update_cmd_menu()
            await pilot.pause(0.4)
            assert app._cmd_menu.display
            assert app.is_running, "menu /resume menutup app"

    asyncio.run(run())


# ============================================================
# Riwayat percakapan setelah /resume
# ============================================================


def _seed_session_hist(tmp_path, monkeypatch):
    """Session tiruan berisi percakapan user/assistant untuk uji /resume."""
    from joki import sessions as s

    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    s.save_session(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "buat file halo.py"},
            {"role": "assistant", "content": "file halo.py sudah dibuat"},
            {"role": "user", "content": "jalankan test-nya"},
            {"role": "assistant", "content": "semua test lulus (3/3)"},
        ],
        session_id="session_hist_1",
        model="m",
    )
    return s


def _render_payload_texts(q):
    """Ambil teks dari payload _RENDER_KIND milik queue TUI (string + _SideCard)."""
    from rich.markdown import Markdown as _RMarkdown

    out = []
    for k, p in list(q.queue):
        if k != state._RENDER_KIND or not (isinstance(p, tuple) and p and isinstance(p[0], tuple)):
            continue
        obj = p[0][0] if p[0] else None
        if isinstance(obj, str):
            out.append(obj)
        else:
            body = getattr(obj, "body", None)
            if isinstance(body, _RMarkdown):
                out.append(body.markup)
    return "\n".join(out)


def test_handle_command_resume_menampilkan_riwayat(tui_queue, tmp_path, monkeypatch):
    """/resume <nomor> harus langsung mencetak riwayat percakapan session."""
    _seed_session_hist(tmp_path, monkeypatch)
    msgs = [{"role": "system", "content": "sys"}]
    try:
        cont, new_msgs = cli._handle_command("/resume 1", msgs)
        assert cont is True
        body = _render_payload_texts(tui_queue)
        assert "Riwayat percakapan" in body, body
        assert "buat file halo.py" in body, body
        assert "file halo.py sudah dibuat" in body, body
        assert "jalankan test-nya" in body, body
        assert "semua test lulus (3/3)" in body, body
        # Pesan benar-benar dimuat ke konteks, bukan cuma ditampilkan.
        saved = [m for m in new_msgs if m.get("role") != "system"]
        assert [m["content"] for m in saved][:2] == [
            "buat file halo.py", "file halo.py sudah dibuat",
        ]
    finally:
        cli._CURRENT_SESSION = None
        state._set_current_session(None)


def test_riwayat_dipotong_sesuai_batas(tmp_path, monkeypatch):
    """Riwayat panjang dipotong ke pesan terakhir + ada catatan jumlah tersembunyi."""
    from joki import cli as _cli
    from joki import sessions as s

    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    banyak = []
    for i in range(_cli._HISTORY_MAX_MESSAGES + 10):
        banyak.append({"role": "user", "content": f"pesan-{i}"})
        banyak.append({"role": "assistant", "content": f"balasan-{i}"})
    s.save_session(banyak, session_id="session_hist_panjang")
    q = queue.Queue()
    state._tui_start(q, dark=True)
    try:
        cont, _ = _cli._handle_command("/resume session_hist_panjang", [{"role": "system", "content": "sys"}])
        assert cont is True
        body = _render_payload_texts(q)
        assert "pesan lebih lama disembunyikan" in body
        assert f"pesan-{len(banyak) // 2 - 1}" in body  # pesan terakhir tampil
        assert "pesan-0" not in body                     # pesan paling lama disembunyikan
    finally:
        state._tui_stop()
        _cli._CURRENT_SESSION = None
        state._set_current_session(None)


def test_tui_resume_menampilkan_riwayat_tanpa_keluar(tmp_path, monkeypatch):
    """Skenario user: pilih /resume dari dropdown, Enter, riwayat muncul."""
    from joki.tui import JokiApp

    _seed_session_hist(tmp_path, monkeypatch)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._prompt.focus()
            for ch in "/resume 1":
                await pilot.press(ch)
            await pilot.pause(0.3)
            await pilot.press("enter")  # pilih session dari dropdown
            await pilot.pause(0.3)
            await pilot.press("enter")  # submit /resume
            await pilot.pause(1.2)
            assert app.is_running, "TUI keluar setelah /resume"
            plain = _log_plain(app)
            assert "Riwayat percakapan" in plain, plain
            assert "buat file halo.py" in plain, plain
            assert "semua test lulus (3/3)" in plain, plain

    try:
        asyncio.run(run())
    finally:
        cli._CURRENT_SESSION = None
        state._set_current_session(None)


def test_tui_startup_resume_langsung_tampilkan_riwayat(tmp_path, monkeypatch):
    """`joki --resume <id>`: TUI dibuka dengan messages berisi riwayat → log
    harus langsung memuat riwayat percakapan (bukan layar kosong)."""
    from joki import sessions as s
    from joki.tui import JokiApp

    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    s.save_session(
        [
            {"role": "user", "content": "perbaiki bug login"},
            {"role": "assistant", "content": "bug login sudah diperbaiki"},
        ],
        session_id="session_hist_2",
        model="m",
    )
    data = s.load_session("session_hist_2")
    messages = cli._resume_into_messages(data)

    async def run():
        app = JokiApp(messages)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.5)
            assert app.is_running
            plain = _log_plain(app)
            assert "Riwayat percakapan" in plain, plain
            assert "perbaiki bug login" in plain, plain
            assert "bug login sudah diperbaiki" in plain, plain

    try:
        asyncio.run(run())
    finally:
        cli._CURRENT_SESSION = None
        state._set_current_session(None)


# ============================================================
# Header atas: pemakaian token & konteks
# ============================================================

def test_context_header_text_format_dan_zona(monkeypatch):
    """Helper header: format angka + penanda zona warn/kritis + fail-open."""
    from joki import llm, tui

    monkeypatch.setattr(llm, "_current_tools", lambda: [])
    msgs = [{"role": "user", "content": "x" * 400}]  # ~107 token (len(str)//4)

    monkeypatch.setattr(llm, "_effective_max_tokens", lambda: 1000)
    normal = tui._context_header_text(msgs)
    assert normal == "Konteks ~10.7% (107/1.000 token)", normal

    monkeypatch.setattr(llm, "_effective_max_tokens", lambda: 150)
    warn = tui._context_header_text(msgs)
    assert warn.startswith("\u26a0 "), warn  # zona summarize otomatis (>=65%)
    assert "71.3%" in warn

    monkeypatch.setattr(llm, "_effective_max_tokens", lambda: 110)
    kritis = tui._context_header_text(msgs)
    assert kritis.startswith("\u26d4 "), kritis  # kritis (>=90%)

    monkeypatch.setattr(llm, "_effective_max_tokens", lambda: 0)
    assert tui._context_header_text(msgs) == ""

    def boom(*a, **k):
        raise RuntimeError("gagal hitung")

    monkeypatch.setattr(llm, "context_usage", boom)
    assert tui._context_header_text(msgs) == ""  # fail-open, jangan jatuhkan TUI


def test_topbar_menampilkan_persentase_token(tui_queue):
    """Header atas: nama model di kiri, persentase token/konteks di kanan."""
    from textual.widgets import Static

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.4)
            title = app.query_one("#topbar_title", Static).content
            ctx = app.query_one("#topbar_ctx", Static).content
            assert "JOKI" in title and "\u2022" in title, title
            assert "Konteks ~" in ctx and "%" in ctx, ctx
            assert ctx.endswith("token)"), ctx

            # Messages bertambah → angka konteks ikut berubah saat refresh.
            app.messages.append({"role": "user", "content": "x" * 40000})
            app._refresh_topbar()
            await pilot.pause(0.1)
            ctx2 = app.query_one("#topbar_ctx", Static).content
            assert ctx2 != ctx, (ctx, ctx2)
            assert "Konteks ~" in ctx2
            assert app.is_running

    asyncio.run(run())


def test_topbar_diperbarui_setelah_command(tui_queue):
    """Command yang mengganti messages (/baru) harus menyegarkan header."""
    from textual.widgets import Static

    from joki.tui import JokiApp

    async def run():
        app = JokiApp([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 20000},
        ])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.4)
            ctx1 = app.query_one("#topbar_ctx", Static).content
            app._handle_submit_text("/baru")
            await pilot.pause(1.0)
            ctx2 = app.query_one("#topbar_ctx", Static).content
            assert ctx1 != ctx2, (ctx1, ctx2)
            assert app.is_running

    try:
        asyncio.run(run())
    finally:
        cli._CURRENT_SESSION = None
        state._set_current_session(None)


def test_topbar_disegarkan_saat_tugas_berjalan(monkeypatch, tui_queue):
    """Selama tugas berjalan, konteks bertambah → header ikut diperbarui."""
    from textual.widgets import Static

    from joki.tui import JokiApp

    gate = threading.Event()

    def agent(messages, extra=True):
        messages.append({"role": "user", "content": "y" * 40000})
        gate.wait(5)  # tahan tugas supaya masih busy saat diperiksa
        messages.append({"role": "assistant", "content": "selesai"})

    monkeypatch.setattr(cli, "agent_loop", agent)

    async def run():
        app = JokiApp([{"role": "system", "content": "sys"}])
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            before = app.query_one("#topbar_ctx", Static).content
            app._handle_submit_text("kerjakan")
            await pilot.pause(0.5)
            assert app._busy, "tugas harus masih berjalan"
            mid = app.query_one("#topbar_ctx", Static).content
            assert mid != before, (before, mid)
            gate.set()
            await pilot.pause(0.6)
            assert not app._busy
            assert app.is_running

    asyncio.run(run())
