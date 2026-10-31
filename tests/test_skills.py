import os

from joki import skills


def _seed_skill(directory, fname, always_on=True, body="Isi skill", trigger=None, extra=""):
    os.makedirs(directory, exist_ok=True)
    on = "true" if always_on else "false"
    trig = ""
    if trigger:
        trig = "trigger: [{}]\n".format(", ".join(trigger))
    content = f"---\nname: Skill {fname}\nalways_on: {on}\n{trig}{extra}---\n{body}"
    p = os.path.join(directory, fname)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    return p


def _isolate(tmp_path, monkeypatch):
    """Isolasi semua sumber skill ke tmp + matikan seeding."""
    user_d = os.path.join(str(tmp_path), "user")
    repo_d = os.path.join(str(tmp_path), "repo")
    os.makedirs(user_d, exist_ok=True)
    os.makedirs(repo_d, exist_ok=True)
    monkeypatch.setattr(skills, "_user_skill_dir", lambda: user_d)
    monkeypatch.setattr(skills, "_repo_skill_dir", lambda: repo_d)
    monkeypatch.setattr(skills, "_project_skill_dir", lambda: "")
    monkeypatch.setattr(skills, "_seed_default_skills", lambda: None)
    skills.reload_skills()
    return user_d, repo_d


def test_load_skills_only_always_on(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "always.md", True, "KONTEN_ALWAYS")
    _seed_skill(user_d, "off.md", False, "KONTEN_OFF")

    result = skills._load_skills()
    assert "KONTEN_ALWAYS" in result
    assert "Skill always.md" in result
    assert "KONTEN_OFF" not in result
    assert "Skill off.md" not in result


def test_load_skills_empty_when_none_always_on(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "off.md", False, "KONTEN_OFF")
    assert skills._load_skills() == ""


def test_repo_source_priority_user_wins(tmp_path, monkeypatch):
    user_d, repo_d = _isolate(tmp_path, monkeypatch)
    _seed_skill(repo_d, "sama.md", True, "KONTEN_REPO")
    _seed_skill(user_d, "sama.md", True, "KONTEN_USER")

    result = skills._load_skills()
    assert "KONTEN_USER" in result
    assert "KONTEN_REPO" not in result
    rows = {r["id"]: r for r in skills.list_skills()}
    assert rows["sama"]["source"] == "user"


def test_trigger_matching(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "r.md", False, "BODY_R", trigger=["refactor", "rename"])
    _seed_skill(user_d, "e.md", False, "BODY_E", trigger=["error"])
    _seed_skill(user_d, "dormant.md", False, "BODY_D")

    matched = skills.match_skills("tolong refactor modul ini")
    assert [(i, n) for i, n, _ in matched] == [("r", "Skill r.md")]
    assert skills.match_skills("halo apa kabar") == []
    assert [i for i, _, _ in skills.match_skills("ada ERROR traceback")] == ["e"]
    # always_on tidak ikut matching
    _seed_skill(user_d, "a.md", True, "BODY_A", trigger=["refactor"])
    assert [i for i, _, _ in skills.match_skills("refactor x")] == ["r"]


def test_match_caps(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    for i in range(5):
        _seed_skill(user_d, f"s{i}.md", False, "x" * 100, trigger=["kw"])
    matched = skills.match_skills("ada kw di sini")
    assert len(matched) == skills._MATCH_MAX_SKILLS


def test_on_off_persist(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "r.md", False, "BODY_R", trigger=["refactor"])

    assert skills.set_skill_enabled("r", False) is True
    assert skills.set_skill_enabled("unknown-id", False) is False
    assert skills.match_skills("refactor x") == []
    assert os.path.exists(os.path.join(user_d, "state.json"))

    skills.reload_skills()
    assert skills.match_skills("refactor x") == []
    assert skills.set_skill_enabled("r", True) is True
    assert [i for i, _, _ in skills.match_skills("refactor x")] == ["r"]


def test_disabled_frontmatter(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "x.md", True, "BODY_X", extra="disabled: true\n")
    assert skills._load_skills() == ""
    rows = {r["id"]: r for r in skills.list_skills()}
    assert rows["x"]["active"] is False


def test_always_on_budget(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "big.md", True, "B" * 500)
    _seed_skill(user_d, "small.md", True, "S")
    monkeypatch.setattr(skills, "_ALWAYS_ON_BUDGET_CHARS", 100)
    result = skills._load_skills()
    assert "dilewati karena budget" in result


def test_seed_only_when_repo_missing(tmp_path, monkeypatch):
    user_d = os.path.join(str(tmp_path), "user")
    monkeypatch.setattr(skills, "_user_skill_dir", lambda: user_d)
    # Repo ada → tidak seed.
    monkeypatch.setattr(skills, "_repo_skill_dir", lambda: str(tmp_path))
    skills._seed_default_skills()
    assert os.path.isdir(user_d)
    assert os.listdir(user_d) == []
    # Repo tak ada (frozen) → seed fallback.
    monkeypatch.setattr(skills, "_repo_skill_dir", lambda: "")
    skills._seed_default_skills()
    dest = os.path.join(user_d, "verifikasi-eksekusi.md")
    assert os.path.exists(dest)
    with open(dest, encoding="utf-8") as fh:
        content = fh.read()
    assert content.startswith("---")
    assert "always_on: true" in content


def test_list_modes(tmp_path, monkeypatch):
    user_d, _ = _isolate(tmp_path, monkeypatch)
    _seed_skill(user_d, "a.md", True, "x")
    _seed_skill(user_d, "t.md", False, "x", trigger=["halo"])
    _seed_skill(user_d, "d.md", False, "x")
    rows = {r["id"]: r for r in skills.list_skills()}
    assert rows["a"]["mode"] == "always_on"
    assert rows["t"]["mode"] == "trigger:halo"
    assert rows["d"]["mode"] == "dormant"
    assert all(r["active"] for r in rows.values())
    assert all(r["source"] == "user" for r in rows.values())
