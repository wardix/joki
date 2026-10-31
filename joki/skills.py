import glob
import json
import os
import re

import yaml
from rich.markup import escape

from joki.config import _get_data_dir
from joki.display import _color_error
from joki.state import _console

# ============================================================
# SUMBER SKILL (prioritas rendah → tinggi, nama file sama dimenangkan
# oleh sumber berprioritas lebih tinggi)
#   1. skill/ di folder utama repo Joki (bawaan, ikut version-control)
#   2. skills/ di folder joki (folder data, milik user)
#   3. ./.joki/skills/ di cwd (per proyek, prioritas tertinggi)
# ============================================================

def _repo_skill_dir():
    """Folder skill/ di folder utama repo ('' bila tak ada, mis. build frozen)."""
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(repo_root, "skill")
        return d if os.path.isdir(d) else ""
    except Exception:  # noqa: BLE001
        return ""


def _user_skill_dir():
    return os.path.join(_get_data_dir(), "skills")


def _project_skill_dir():
    try:
        d = os.path.join(os.getcwd(), ".joki", "skills")
        return d if os.path.isdir(d) else ""
    except Exception:  # noqa: BLE001
        return ""


_ALWAYS_ON_BUDGET_CHARS = 6000   # batas total always-on agar tak jadi bom token
_MATCH_MAX_SKILLS = 2            # maks skill trigger per turn
_MATCH_MAX_CHARS = 3000          # batas total skill trigger per turn

_SKILL_CACHE = {"key": None, "skills": []}
_DISABLED = {"names": None}  # cache state.json; None = belum dimuat


def _skill_files():
    """Kumpulkan file *.md dari semua sumber. Return [(source, path)]."""
    out = []
    for source, d in (("repo", _repo_skill_dir()),
                      ("user", _user_skill_dir()),
                      ("project", _project_skill_dir())):
        if not d:
            continue
        for f in sorted(glob.glob(os.path.join(d, "*.md"))):
            out.append((source, f))
    return out


def _cache_key(files):
    try:
        return tuple((f, os.path.getmtime(f)) for _, f in files)
    except OSError:
        return None


def _parse_skill_file(path):
    """Parse satu file skill → dict atau None (skip bila tak valid).

    Format: frontmatter YAML (---...---) + body markdown.
    Field: name (wajib, default nama file), always_on (bool, default False),
    trigger (list[str], default []), disabled (bool, default False).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        if not raw.startswith("---"):
            return None
        _, fm_raw, body = raw.split("---", 2)
        meta = yaml.safe_load(fm_raw) or {}
        if not isinstance(meta, dict):
            return None
        triggers = meta.get("trigger", []) or []
        if isinstance(triggers, str):
            triggers = [triggers]
        return {
            "id": os.path.splitext(os.path.basename(path))[0],
            "name": str(meta.get("name", os.path.basename(path))),
            "always_on": bool(meta.get("always_on", False)),
            "trigger": [str(t).lower() for t in triggers if str(t).strip()],
            "disabled_file": bool(meta.get("disabled", False)),
            "body": body.strip(),
            "path": path,
        }
    except Exception as e:  # noqa: BLE001
        _console.print(f"[{_color_error()}]Gagal memuat skill {path}: {escape(str(e))}[/{_color_error()}]")
        return None


def _load_state_disabled():
    """Daftar id skill yang dimatikan user (state.json). Cache per sesi."""
    if _DISABLED["names"] is not None:
        return _DISABLED["names"]
    names = set()
    try:
        with open(os.path.join(_user_skill_dir(), "state.json"), encoding="utf-8") as fh:
            data = json.load(fh) or {}
        for n in data.get("disabled", []) or []:
            names.add(str(n))
    except (OSError, ValueError):
        pass
    _DISABLED["names"] = names
    return names


def _save_state_disabled(names):
    _DISABLED["names"] = set(names)
    try:
        os.makedirs(_user_skill_dir(), exist_ok=True)
        with open(os.path.join(_user_skill_dir(), "state.json"), "w", encoding="utf-8") as fh:
            json.dump({"disabled": sorted(set(names))}, fh)
    except OSError as e:
        _console.print(f"[{_color_error()}]Gagal menyimpan state skills: {escape(str(e))}[/{_color_error()}]")


def _all_skills():
    """Semua skill valid dari semua sumber (dedupe per id: project>user>repo).

    Cache berdasarkan mtime file; gunakan reload_skills() untuk paksa baca ulang.
    """
    files = _skill_files()
    key = _cache_key(files)
    if key is not None and key == _SKILL_CACHE["key"]:
        return _SKILL_CACHE["skills"]
    by_id = {}
    for source, f in files:
        sk = _parse_skill_file(f)
        if not sk:
            continue
        sk["source"] = source
        by_id[sk["id"]] = sk  # sumber belakangan (prioritas tinggi) menang
    skills = [by_id[k] for k in sorted(by_id)]
    _SKILL_CACHE["key"] = key
    _SKILL_CACHE["skills"] = skills
    return skills


def reload_skills():
    """Paksa baca ulang semua file skill + state (untuk /skills reload)."""
    _SKILL_CACHE["key"] = None
    _SKILL_CACHE["skills"] = []
    _DISABLED["names"] = None


def is_skill_active(sk):
    """Aktif bila tidak disabled via file maupun state user."""
    if sk.get("disabled_file"):
        return False
    return sk["id"] not in _load_state_disabled()


def list_skills():
    """Daftar semua skill + status, untuk /skills list."""
    return [
        {
            "id": sk["id"],
            "name": sk["name"],
            "source": sk.get("source", "?"),
            "mode": ("always_on" if sk["always_on"]
                     else ("trigger:" + ",".join(sk["trigger"]) if sk["trigger"] else "dormant")),
            "active": is_skill_active(sk),
        }
        for sk in _all_skills()
    ]


def set_skill_enabled(skill_id, enabled):
    """Nyalakan/matikan skill (persist ke state.json). Return True bila id dikenal."""
    ids = {sk["id"] for sk in _all_skills()}
    if skill_id not in ids:
        return False
    names = _load_state_disabled()
    if enabled:
        names.discard(skill_id)
    else:
        names.add(skill_id)
    _save_state_disabled(names)
    return True


def _load_skills() -> str:
    """Blok always-on untuk system prompt (tanda tangan dipertahankan).

    Hanya skill aktif + always_on, dibatasi budget agar tak jadi bom token.
    """
    parts = []
    total = 0
    skipped = []
    for sk in _all_skills():
        if not sk["always_on"] or not is_skill_active(sk):
            continue
        block = f"### Skill: {sk['name']}\n{sk['body']}"
        if total + len(block) > _ALWAYS_ON_BUDGET_CHARS:
            skipped.append(sk["id"])
            continue
        parts.append(block)
        total += len(block)
    out = "\n\n---\n\n".join(parts)
    if skipped:
        out += f"\n\n_[Skill dilewati karena budget ({_ALWAYS_ON_BUDGET_CHARS} char): {', '.join(skipped)}]_"
    return out


def _trigger_hit(trigger, text_lower):
    """Cocokkan satu trigger dengan word-boundary agar 'error' tidak match
    'terror', 'port' tidak match 'report'/'import'. Fallback substring bila
    regex gagal (trigger mengandung karakter aneh)."""
    t = (trigger or "").strip()
    if not t:
        return False
    try:
        return re.search(r"\b" + re.escape(t) + r"\b", text_lower) is not None
    except re.error:
        return t in text_lower


def match_skills(user_text):
    """Cocokkan skill trigger terhadap pesan user. Return [(id, name, body)].

    Hanya skill aktif, non-always_on, yang salah satu trigger-nya muncul
    (word-boundary, case-insensitive). Dibatasi jumlah + total char per turn.
    """
    if not (user_text or "").strip():
        return []
    low = user_text.lower()
    matched = []
    total = 0
    for sk in _all_skills():
        if sk["always_on"] or not is_skill_active(sk) or not sk["trigger"]:
            continue
        if not any(_trigger_hit(t, low) for t in sk["trigger"]):
            continue
        if len(matched) >= _MATCH_MAX_SKILLS or total + len(sk["body"]) > _MATCH_MAX_CHARS:
            break
        matched.append((sk["id"], sk["name"], sk["body"]))
        total += len(sk["body"])
    return matched


# Fallback minimal HANYA untuk build frozen (tanpa folder skill repo).
# Source of truth tetap skill/*.md di repo — jangan edit di sini bila
# file repo tersedia.
_FALLBACK_SKILLS = {
    "verifikasi-eksekusi.md": """\
---
name: Verifikasi Eksekusi Sebelum Klaim Selesai
always_on: true
---

Sebelum menyatakan sebuah task/fix selesai, WAJIB verifikasi dengan
mengeksekusi jalur kode yang diubah secara langsung (bukan cuma
import/syntax check atau mengandalkan test suite lolos), lalu laporkan
spesifik apa yang dites dan hasilnya.
""",
}


def _seed_default_skills():
    """Pastikan folder skill user ada. Tulis skill bawaan HANYA bila folder
    skill repo tak tersedia (mis. build frozen) — supaya update skill di repo
    selalu efektif dan tidak diduplikasi bayangan di folder user.

    (Tanda tangan dipertahankan untuk backward-compat.)
    """
    try:
        os.makedirs(_user_skill_dir(), exist_ok=True)
    except Exception:  # noqa: BLE001
        return
    if _repo_skill_dir():
        return
    for fname, content in _FALLBACK_SKILLS.items():
        dest = os.path.join(_user_skill_dir(), fname)
        if not os.path.exists(dest):
            try:
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception as e:  # noqa: BLE001
                _console.print(
                    f"[{_color_error()}]Gagal menulis skill bawaan {fname}: {escape(str(e))}[/{_color_error()}]"
                )
