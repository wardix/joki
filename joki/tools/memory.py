import ast
import json
import os
import re
import time

from joki.config import _get_data_dir
from joki.state import *
from joki.utils import *


# Default TTL untuk entri memori: 7 hari. Entri yang lebih lama dari TTL
# dianggap basi dan akan dibuang otomatis saat dimuat.
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def _memory_path(name=None):
    # Session aktif dibaca dinamis dari state (bukan salinan modul ini),
    # agar /baru & resume session memakai file memori masing-masing.
    name = name or _get_current_session() or "default"
    return os.path.join(_get_data_dir(), "memories", f"{name}.json")


def _memory_entry(value, ttl=None):
    """Bungkus nilai memori dengan metadata timestamp + TTL."""
    return {
        "value": value,
        "ts": time.time(),
        "ttl": ttl if ttl is not None else _DEFAULT_TTL_SECONDS,
    }


def _is_expired(entry, now=None):
    now = now or time.time()
    ttl = entry.get("ttl", _DEFAULT_TTL_SECONDS)
    if ttl is None:
        return False  # ttl None = simpan selamanya (tidak kadaluarsa)
    return (now - entry.get("ts", 0)) > ttl


def _load_memory_raw(name=None):
    """Muat format nested {key: {value, ts, ttl}}, buang entri kadaluarsa.

    Format lama (flat string {key: value}) juga diterima dan diupgrade ke
    nested tanpa TTL (selalu valid).
    """
    path = _memory_path(name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    now = time.time()
    cleaned = {}
    changed = False
    for k, v in data.items():
        if isinstance(v, dict) and "value" in v:
            if _is_expired(v, now):
                changed = True
                continue
            cleaned[k] = v
        elif isinstance(v, str):
            # Format lama: flat string, treat sebagai tanpa TTL (selalu valid).
            cleaned[k] = {"value": v, "ts": 0, "ttl": None}
            changed = True
        else:
            changed = True  # entri rusak, buang
    if changed:
        _save_memory_raw(cleaned, name)
    return cleaned


def _save_memory_raw(data, name=None):
    path = _memory_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_memory(name=None):
    """Backward-compat: kembalikan {key: value_string} (flat)."""
    raw = _load_memory_raw(name)
    return {
        k: (v.get("value", "") if isinstance(v, dict) else str(v))
        for k, v in raw.items()
    }


def _memory_list(name=None):
    """Kembalikan {key: entry_dict} dengan metadata (digunakan system prompt)."""
    return _load_memory_raw(name)


# ============================================================
# RENCANA PENGERJAAN
# ============================================================

_TODO_PREFIX_RE = re.compile(r"^\s*(?:[-*•>]\s*|\d+[.)]\s*|\(\d+\)\s*)+")


def _coerce_list(value):
    """Normalisasi nilai dari LLM menjadi list of str."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, str):
        raw = value.strip()
        parsed = None
        if raw[:1] in ("[", "("):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError, MemoryError):
                    parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            parts = re.split(r"[\n;]+", raw)
            value = [p for p in (part.strip() for part in parts) if p]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, (list, tuple)):
            item = " ".join(str(x) for x in item)
        text = str(item).strip()
        if not text:
            continue
        cleaned = _TODO_PREFIX_RE.sub("", text).strip()
        out.append(cleaned or text)
    return out


def _coerce_indices(value):
    """Normalisasi indices jadi list of int."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, str):
        raw = value.strip()
        parsed = None
        if raw[:1] == "[":
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError, MemoryError):
                    parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            value = [p for p in re.split(r"[,\s;]+", raw) if p]
    if isinstance(value, (int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        try:
            out.append(int(float(item)))
        except (TypeError, ValueError):
            continue
    return out


def _todo_path():
    # Todos per session: saat session di-resume, rencana pengerjaannya ikut kembali.
    return os.path.join(
        _get_data_dir(),
        "todos",
        f"{_get_current_session() or 'default'}.json")


def _load_todo():
    path = _todo_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_todo(items):
    path = _todo_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(items, f, indent=2)

# ============================================================
# TOOL EXECUTOR
# ============================================================

_SENSITIVE_KEY_WORDS = (
    "password", "passwd", "secret", "token",
    "api_key", "apikey", "credential", "auth",
)


def _is_sensitive_key(key):
    k = (key or "").lower()
    return any(w in k for w in _SENSITIVE_KEY_WORDS)


def handle_memory_store(args):
    key = args.get("key", "")
    value = args.get("value", "")
    if not key:
        return "Error: Parameter 'key' wajib diisi. Contoh: memory_store(key=\"db_password\", value=\"secret123\")"
    if not value:
        return "Error: Parameter 'value' wajib diisi. Contoh: memory_store(key=\"db_password\", value=\"secret123\")"
    if _is_sensitive_key(key):
        return (f"Error: key '{key}' terdeteksi berisi credential "
                f"(password/secret/token/api_key). Jangan simpan credential di "
                f"memory_store — file memory disimpan sebagai plaintext. "
                f"Gunakan environment variable atau config untuk credential.")
    ttl = args.get("ttl")
    if ttl is not None:
        try:
            ttl = int(ttl)
        except (TypeError, ValueError):
            ttl = None
    mem = _load_memory_raw()
    mem[key] = _memory_entry(value, ttl)
    _save_memory_raw(mem)
    return f"Memory saved: {key}"


def handle_memory_recall(args):
    mem = _load_memory()
    key = args.get("key", "")
    if key:
        if key in mem:
            return f"{key}: {mem[key]}"
        return f"Memory '{key}' not found"
    if not mem:
        return "(no memories stored)"
    lines = [
        f"  {k}: {v[:100]}{'...' if len(v) > 100 else ''}" for k,
        v in mem.items()]
    return f"Memori tersimpan ({len(mem)}):\n" + "\n".join(lines)


def handle_memory_forget(args):
    key = args.get("key", "")
    if not key:
        return "Error: Parameter 'key' wajib diisi. Contoh: memory_forget(key=\"db_password\")"
    mem = _load_memory_raw()
    if key in mem:
        del mem[key]
        _save_memory_raw(mem)
        return f"Memory forgotten: {key}"
    return f"Memory '{key}' not found"


def handle_todo_create(args):
    items = _coerce_list(args.get("items"))
    if not items:
        return "Error: Parameter 'items' wajib diisi sebagai array. Contoh: todo_create(items=[\"Buat file\", \"Test\"])"
    _save_todo(items)
    lines = [f"  {i+1}. [ ] {item}" for i, item in enumerate(items)]
    return f"Rencana Pengerjaan dibuat ({len(items)} item):\n" + "\n".join(lines)


def handle_todo_done(args):
    indices = _coerce_indices(args.get("indices"))
    if not indices:
        return "Error: Parameter 'indices' wajib diisi sebagai array. Contoh: todo_done(indices=[1, 2])"
    items = _load_todo()
    marked = []
    for idx in indices:
        if 1 <= idx <= len(items):
            items[idx - 1] = f"✅ {items[idx - 1]}"
            marked.append(str(idx))
    _save_todo(items)

    visual_trigger = ""
    if indices and max(indices) == len(items):
        last_item = items[-1]
        if "Verifikasi" in last_item:
            visual_trigger = "\n\n[SISTEM] Deteksi item 'Verifikasi' di akhir Rencana Pengerjaan. Menyiapkan validasi visual..."

    return f"Item Rencana Pengerjaan {' dan '.join(marked)} selesai! {visual_trigger}\n" + "\n".join(
        f"  {i+1}. {item}" for i, item in enumerate(items))


def handle_todo_show(args):
    items = _load_todo()
    if not items:
        return "(Rencana Pengerjaan kosong)"
    lines = [f"  {i+1}. {item}" for i, item in enumerate(items)]
    done = sum(1 for i in items if i.startswith("✅"))
    return f"Rencana Pengerjaan ({done}/{len(items)} selesai):\n" + "\n".join(lines)
