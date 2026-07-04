import os
import sys
import json
import subprocess
import sqlite3
import re
import time
import random
import base64
import socket
import urllib
import csv
import platform
import ssl
from pathlib import Path
from difflib import unified_diff
from datetime import datetime
import httpx
from duckduckgo_search import DDGS
from joki.state import *
from joki.utils import *
from joki.display import _numbered, _Spinner


def _memory_path(name=None):
    name = name or _CURRENT_SESSION or "default"
    return os.path.join(SESSION_DIR, "memories", f"{name}.json")


def _load_memory(name=None):
    path = _memory_path(name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_memory(data, name=None):
    path = _memory_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ============================================================
# TODO LIST
# ============================================================


def _todo_path():
    return os.path.join(
        SESSION_DIR,
        "todos",
        f"{_CURRENT_SESSION or 'default'}.json")


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


def handle_memory_store(args):
    mem = _load_memory()
    mem[args["key"]] = args["value"]
    _save_memory(mem)
    return f"Memory saved: {args['key']}"


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
    mem = _load_memory()
    if args["key"] in mem:
        del mem[args["key"]]
        _save_memory(mem)
        return f"Memory forgotten: {args['key']}"
    return f"Memory '{args['key']}' not found"


def handle_todo_create(args):
    items = args["items"]
    _save_todo(items)
    lines = [f"  {i+1}. [ ] {item}" for i, item in enumerate(items)]
    return f"TODO list dibuat ({len(items)} item):\n" + "\n".join(lines)


def handle_todo_done(args):
    indices = args["indices"]
    items = _load_todo()
    marked = []
    for idx in indices:
        if 1 <= idx <= len(items):
            items[idx - 1] = f"✅ {items[idx - 1]}"
            marked.append(str(idx))
    _save_todo(items)

    # Trigger visual verification if the last item is completed and mentions
    # "Verifikasi"
    visual_trigger = ""
    if indices and max(indices) == len(items):
        last_item = items[-1]
        if "Verifikasi" in last_item:
            visual_trigger = "\n\n[SISTEM] Deteksi item 'Verifikasi' di akhir TODO. Menyiapkan validasi visual..."

    return f"Item TODO {' dan '.join(marked)} selesai! {visual_trigger}\n" + "\n".join(
        f"  {i+1}. {item}" for i, item in enumerate(items))


def handle_todo_show(args):
    items = _load_todo()
    if not items:
        return "(TODO list kosong)"
    lines = [f"  {i+1}. {item}" for i, item in enumerate(items)]
    done = sum(1 for i in items if i.startswith("✅"))
    return f"TODO list ({done}/{len(items)} selesai):\n" + "\n".join(lines)
