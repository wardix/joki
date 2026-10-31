"""Manajemen session joki: simpan/muat percakapan agar bisa dilanjutkan.

Session disimpan per file di <data_dir>/sessions/<id>.json (default: folder joki):
- ditulis otomatis setiap agent_loop selesai (lihat cli._save_session_snapshot);
- dimuat via `joki --resume [id|nomor]` atau slash command `/resume`;
- daftar via `joki --sessions` atau `/sessions`.

Semua fungsi fail-open: error apa pun diabaikan (mengembalikan None/[]).
"""
import json
import os
import re
from datetime import datetime

from joki.config import _get_data_dir

_SESSIONS_KEEP = 100         # prune: simpan maksimal sekian file session
_SESSION_MAX_MESSAGES = 400  # potong riwayat lama saat menyimpan
_MSG_MAX_CHARS = 20000       # batas panjang konten pesan saat disimpan


def _sessions_dir():
    return os.path.join(_get_data_dir(), "sessions")


def _session_path(session_id):
    """Path file session; id dibersihkan agar aman sebagai nama file."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))[:64] or "default"
    return os.path.join(_sessions_dir(), f"{name}.json")


def _now():
    return datetime.now().astimezone().isoformat()


def _slim_message(m):
    """Salinan pesan yang JSON-safe; konten raksasa dipotong (fail-open)."""
    try:
        out = {}
        for k, v in dict(m).items():
            if k == "content" and isinstance(v, str) and len(v) > _MSG_MAX_CHARS:
                v = v[:_MSG_MAX_CHARS] + "\n[... dipotong saat menyimpan session ...]"
            out[k] = v
        return out
    except Exception:  # noqa: BLE001
        return {"role": "assistant", "content": str(m)[:1000]}


def save_session(messages, session_id=None, model=None, cwd=None):
    """Simpan percakapan (pesan system dibuang). Return path, atau None bila gagal."""
    try:
        if not session_id:
            try:
                from joki.state import _CURRENT_SESSION
                session_id = _CURRENT_SESSION
            except Exception:  # noqa: BLE001, S110
                session_id = None
        session_id = str(session_id or "default")
        path = _session_path(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        msgs = [m for m in (messages or []) if str((m or {}).get("role", "")) != "system"]
        msgs = msgs[-_SESSION_MAX_MESSAGES:]
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            if not isinstance(old, dict):
                old = {}
        except Exception:  # noqa: BLE001
            old = {}
        first_user = str(old.get("first_user") or "")
        if not first_user:
            for m in msgs:
                if m.get("role") == "user":
                    first_user = str(m.get("content") or "")[:160].replace("\n", " ").strip()
                    break
        data = {
            "session": session_id,
            "created": old.get("created") or _now(),
            "updated": _now(),
            "cwd": cwd or os.getcwd(),
            "model": str(model or old.get("model") or ""),
            "message_count": len(msgs),
            "first_user": first_user,
            "messages": [_slim_message(m) for m in msgs],
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)
        _prune_sessions()
        return path
    except Exception:  # noqa: BLE001
        return None


def load_session(session_id):
    """Muat data session lengkap (dict berisi 'messages'), atau None."""
    try:
        path = _session_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def list_sessions(limit=15):
    """Daftar metadata session terbaru (urut waktu simpan terbaru dulu)."""
    out = []
    try:
        d = _sessions_dir()
        if not os.path.isdir(d):
            return out
        files = [f for f in os.listdir(d) if f.endswith(".json")]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
        for f in files[: max(1, int(limit))]:
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    continue
                out.append({
                    "session": str(data.get("session") or f[:-5]),
                    "updated": str(data.get("updated") or ""),
                    "cwd": str(data.get("cwd") or ""),
                    "model": str(data.get("model") or ""),
                    "message_count": int(data.get("message_count") or 0),
                    "first_user": str(data.get("first_user") or ""),
                })
            except Exception:  # noqa: BLE001, S110
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def latest_session_id(exclude=None):
    """Id session terbaru (opsional: lewati satu id, mis. session aktif)."""
    for r in list_sessions(limit=5):
        if exclude and r["session"] == exclude:
            continue
        return r["session"]
    return None


def resolve_session(ref):
    """Cari id session dari: kosong (terbaru), nomor urut 1-based, id, prefix id."""
    ref = str(ref or "").strip()
    rows = list_sessions(limit=max(20, _SESSIONS_KEEP))
    if not rows:
        return None
    if not ref:
        return rows[0]["session"]
    if ref.isdigit():
        i = int(ref)
        return rows[i - 1]["session"] if 1 <= i <= len(rows) else None
    ref_l = ref.lower()
    for r in rows:
        if r["session"].lower() == ref_l:
            return r["session"]
    cand = [r["session"] for r in rows
            if r["session"].lower().startswith(ref_l)
            or r["session"].lower().startswith("session_" + ref_l)]
    return cand[0] if len(cand) == 1 else None


def _prune_sessions(keep=None):
    """Hapus file session paling lama bila melebihi batas (fail-open)."""
    try:
        keep = _SESSIONS_KEEP if keep is None else int(keep)
        d = _sessions_dir()
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")]
        if len(files) <= keep:
            return
        files.sort(key=os.path.getmtime)
        for p in files[: len(files) - keep]:
            try:
                os.remove(p)
            except Exception:  # noqa: BLE001, S110
                pass
    except Exception:  # noqa: BLE001
        pass
