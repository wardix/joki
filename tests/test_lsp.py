from types import SimpleNamespace

from joki.tools import lsp


def test_handle_install_already_installed(monkeypatch, capsys):
    monkeypatch.setattr(
        lsp, "_get_available_servers",
        lambda: {"python": {"command": ["pyright-langserver", "--stdio"]}},
    )
    called = []
    monkeypatch.setattr(lsp, "_try_install_lsp", lambda *a, **k: called.append((a, k)))
    lsp.handle_install_lsp_command("python")
    assert called == []
    assert "sudah terinstall" in capsys.readouterr().out


def test_handle_install_auto_installs(monkeypatch):
    monkeypatch.setattr(lsp, "_get_available_servers", dict)
    called = []
    monkeypatch.setattr(lsp, "_try_install_lsp", lambda *a, **k: called.append((a, k)))
    lsp.handle_install_lsp_command("python")
    assert called == [(("python",), {"auto": True})]


def test_try_install_lsp_auto_skips_prompt(monkeypatch):
    monkeypatch.setattr(
        lsp.shutil, "which",
        lambda exe: "/usr/bin/npm" if exe == "npm" else None,
    )
    ran = {}

    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(lsp.subprocess, "run", fake_run)

    def boom(*a, **k):
        raise AssertionError("input() tidak boleh dipanggil saat auto=True")

    monkeypatch.setattr("builtins.input", boom)
    assert lsp._try_install_lsp("python", auto=True) is True
    assert ran["cmd"][:2] == ["npm", "install"]


# ---------- Preopen + cakupan rename ----------

def test_preopen_prioritizes_extra_and_counts(tmp_path):
    from joki.tools import lsp as L

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.py").write_text("z = 3\n")

    opened = []

    class FakeClient:
        def open_file(self, fp):
            opened.append(fp)

    got_opened, got_total = L._preopen_project_files(
        FakeClient(), str(tmp_path), ".py",
        extra_files=[str(tmp_path / "c.py")], limit=2,
    )
    assert got_total == 3
    assert got_opened == 2
    assert opened[0] == str(tmp_path / "c.py")


def test_rename_importers_from_graph(tmp_path):
    from joki.tools import deps as D
    from joki.tools import lsp as L

    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("from a import f\nprint(f())\n")
    got = L._rename_importers(str(tmp_path), str(tmp_path / "a.py"))
    assert got == [str(tmp_path / "b.py")]
    D._GO_MAP_CACHE.clear()
