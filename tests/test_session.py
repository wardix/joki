"""Test session store (simpan/muat percakapan) + fix sinkronisasi session.

Latar belakang:
- `_CURRENT_SESSION` di `joki.state` tidak pernah ikut ter-update saat cli
  mengganti session (assignment di modul cli hanya mengubah salinan lokal),
  sehingga todos/memories selalu menulis ke "default.json".
- Fitur baru: percakapan disimpan otomatis tiap giliran dan bisa dilanjutkan
  via `joki --resume [id|nomor]` / `/resume`.
"""
import os
import time


# ============================================================
# Fix sinkronisasi _CURRENT_SESSION
# ============================================================

def test_set_current_session_syncs_state_and_paths(tmp_path, monkeypatch):
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    try:
        st._set_current_session("session_test_abc")
        assert st._get_current_session() == "session_test_abc"
        assert st._CURRENT_SESSION == "session_test_abc"
        assert mem._todo_path().endswith("session_test_abc.json")
        assert mem._memory_path().endswith("session_test_abc.json")
    finally:
        st._set_current_session(None)


def test_memory_reads_session_dynamically(tmp_path, monkeypatch):
    """Memory/todos harus ikut nilai TERBARU di state, bukan salinan import."""
    import joki.state as st
    from joki.tools import memory as mem
    monkeypatch.setattr(mem, "_get_data_dir", lambda: str(tmp_path))
    try:
        st._set_current_session("satu")
        p1 = mem._todo_path()
        st._set_current_session("dua")
        p2 = mem._todo_path()
        assert p1 != p2
        assert p1.endswith("satu.json") and p2.endswith("dua.json")
    finally:
        st._set_current_session(None)


# ============================================================
# Penyimpanan session (joki/sessions.py)
# ============================================================

def test_save_load_list_session(tmp_path, monkeypatch):
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "perbaiki bug login gtd"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "lanjutkan"},
    ]
    path = s.save_session(msgs, session_id="session_unit_1", model="m-1")
    assert path and os.path.exists(path)
    data = s.load_session("session_unit_1")
    assert data["session"] == "session_unit_1"
    assert all(m["role"] != "system" for m in data["messages"])
    assert data["message_count"] == 3
    assert data["first_user"].startswith("perbaiki bug")
    assert data["model"] == "m-1"
    assert data["cwd"]
    # `created` dipertahankan antar save; `updated` berubah; isi bertambah.
    created = data["created"]
    time.sleep(0.01)
    s.save_session(msgs + [{"role": "assistant", "content": "beres"}],
                   session_id="session_unit_1")
    data2 = s.load_session("session_unit_1")
    assert data2["created"] == created
    assert data2["message_count"] == 4
    rows = s.list_sessions()
    assert rows and rows[0]["session"] == "session_unit_1"
    assert rows[0]["message_count"] == 4


def test_resolve_session_by_number_and_prefix(tmp_path, monkeypatch):
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    for i in (1, 2, 3):
        s.save_session([{"role": "user", "content": f"t{i}"}], session_id=f"session_u{i}")
        time.sleep(0.01)
    assert s.latest_session_id() == "session_u3"
    assert s.resolve_session("") == "session_u3"
    assert s.resolve_session("1") == "session_u3"
    assert s.resolve_session("3") == "session_u1"
    assert s.resolve_session("session_u2") == "session_u2"
    assert s.resolve_session("u2") == "session_u2"  # prefix tanpa "session_"
    assert s.resolve_session("session_u") is None  # ambigu (3 kandidat)
    assert s.resolve_session("9") is None  # nomor di luar jangkauan
    assert s.latest_session_id(exclude="session_u3") == "session_u2"


def test_load_session_missing_and_corrupt(tmp_path, monkeypatch):
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    assert s.load_session("tidak_ada") is None
    os.makedirs(s._sessions_dir(), exist_ok=True)
    with open(os.path.join(s._sessions_dir(), "rusak.json"), "w") as f:
        f.write("{bukan json")
    assert s.load_session("rusak") is None
    assert s.list_sessions() == []
    assert s.resolve_session("rusak") is None


def test_save_session_prunes(tmp_path, monkeypatch):
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    for i in range(5):
        s.save_session([{"role": "user", "content": str(i)}], session_id=f"session_p{i}")
        time.sleep(0.01)
    s._prune_sessions(keep=2)
    rows = s.list_sessions(limit=10)
    assert len(rows) == 2
    assert {r["session"] for r in rows} == {"session_p3", "session_p4"}


def test_save_session_truncates_huge_content(tmp_path, monkeypatch):
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    big = "x" * 50000
    s.save_session([{"role": "user", "content": big}], session_id="session_big")
    data = s.load_session("session_big")
    assert len(data["messages"][0]["content"]) < 30000
    assert "dipotong" in data["messages"][0]["content"]


# ============================================================
# Slash command & agent loop
# ============================================================

def test_handle_command_sessions_and_resume(tmp_path, monkeypatch, capsys):
    import joki.cli as cli
    import joki.state as st
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_build_system_prompt", lambda: "SYS")
    saved = [
        {"role": "system", "content": "lama"},
        {"role": "user", "content": "task lama gtd"},
        {"role": "assistant", "content": "selesai"},
    ]
    s.save_session(saved, session_id="session_resume_cli")
    try:
        cont, msgs = cli._handle_command("/sessions", [{"role": "system", "content": "s"}])
        assert cont is True
        assert "session_resume_cli" in capsys.readouterr().out.replace("\n", "")

        cont, msgs = cli._handle_command("/resume session_resume_cli", msgs)
        assert cont is True
        assert msgs[0]["role"] == "system" and msgs[0]["content"] == "SYS"
        assert any(m.get("content") == "task lama gtd" for m in msgs)
        assert st._get_current_session() == "session_resume_cli"
        assert cli._CURRENT_SESSION == "session_resume_cli"

        cont, msgs = cli._handle_command("/resume tidak-ada-xyz", msgs)
        assert cont is True
        assert "tidak ditemukan" in capsys.readouterr().out
    finally:
        st._set_current_session(None)
        cli._CURRENT_SESSION = None


def test_handle_command_resume_by_number(tmp_path, monkeypatch, capsys):
    import joki.cli as cli
    import joki.state as st
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_build_system_prompt", lambda: "SYS")
    s.save_session([{"role": "user", "content": "lama"}], session_id="session_num_1")
    time.sleep(0.01)
    s.save_session([{"role": "user", "content": "baru"}], session_id="session_num_2")
    try:
        cont, msgs = cli._handle_command("/resume 2", [{"role": "system", "content": "s"}])
        assert cont is True
        assert st._get_current_session() == "session_num_1"
    finally:
        st._set_current_session(None)
        cli._CURRENT_SESSION = None


def test_parse_session_flags():
    import joki.cli as cli
    # Ref session dikonsumsi bila berbentuk id (angka / "session_...").
    args, ls, rr = cli._parse_session_flags(["proyek", "--resume", "session_x"])
    assert args == ["proyek"] and ls is False and rr == "session_x"
    args, ls, rr = cli._parse_session_flags(["--resume", "3"])
    assert rr == "3" and args == []
    args, ls, rr = cli._parse_session_flags(["--resume"])
    assert rr == "" and args == []
    # Bukan id session → jangan dikonsumsi (jadi dir/task seperti biasa).
    args, ls, rr = cli._parse_session_flags(["--resume", "kerjakan ini"])
    assert rr == "" and args == ["kerjakan ini"]
    args, ls, rr = cli._parse_session_flags(["--resume", "/tmp"])
    assert rr == "" and args == ["/tmp"]
    args, ls, rr = cli._parse_session_flags(["-c", "halo dunia"])
    assert rr == "" and args == ["halo dunia"]
    # Daftar session.
    args, ls, rr = cli._parse_session_flags(["--sessions"])
    assert ls is True and rr is None and args == []


def test_agent_loop_autosaves_session(tmp_path, monkeypatch):
    import joki.cli as cli
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_CURRENT_SESSION", "session_autosave")
    monkeypatch.setattr(cli, "_SESSION_AUTOSAVE", True)
    monkeypatch.setitem(cli._current_model_config, "model", "test-model")
    monkeypatch.chdir(tmp_path)

    def _llm(msgs):
        return {"role": "assistant", "content": "halo juga"}

    monkeypatch.setattr(cli, "call_llm", _llm)
    cli._joki_cancel.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "halo"}]
    cli.agent_loop(msgs)
    data = s.load_session("session_autosave")
    assert data is not None
    assert data["messages"][0]["content"] == "halo"
    assert data["model"] == "test-model"


def test_agent_loop_does_not_autosave_when_disabled(tmp_path, monkeypatch):
    """Di luar aplikasi normal (autosave belum diaktifkan main()), agent_loop
    tidak boleh menulis file session — mis. saat dipanggil test/library."""
    import joki.cli as cli
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_CURRENT_SESSION", "session_nosave")
    monkeypatch.setattr(cli, "_SESSION_AUTOSAVE", False)
    monkeypatch.chdir(tmp_path)

    def _llm(msgs):
        return {"role": "assistant", "content": "hai"}

    monkeypatch.setattr(cli, "call_llm", _llm)
    cli._joki_cancel.clear()
    cli.agent_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "hai"}])
    assert s.load_session("session_nosave") is None


def test_agent_loop_without_session_does_not_write_default(tmp_path, monkeypatch):
    """Tanpa session aktif (mis. test), jangan menulis sessions/default.json."""
    import joki.cli as cli
    import joki.state as st
    from joki import sessions as s
    monkeypatch.setattr(s, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_CURRENT_SESSION", None)
    monkeypatch.setattr(st, "_CURRENT_SESSION", None)
    monkeypatch.setattr(cli, "_SESSION_AUTOSAVE", True)
    monkeypatch.chdir(tmp_path)

    def _llm(msgs):
        return {"role": "assistant", "content": "hai"}

    monkeypatch.setattr(cli, "call_llm", _llm)
    cli._joki_cancel.clear()
    cli.agent_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "hai"}])
    assert not os.path.exists(os.path.join(str(tmp_path), "sessions", "default.json"))
