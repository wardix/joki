import os, json, subprocess
from datetime import datetime
from joki.state import *
from joki.config import _get_data_dir
from joki.tools.memory import _load_memory, _save_memory
from joki.display import _clean_latex, _numbered
SESSION_DIR = _get_data_dir()
LOG_DIR = os.path.join(SESSION_DIR, "logs")

def _session_path(name):
    return os.path.join(SESSION_DIR, f"{name}.json")

def _log_path(name):
    return os.path.join(LOG_DIR, f"{name}.log")

def auto_save_log(messages, name):
    """Simpan percakapan ke logs/<session_name>.log dalam format readable."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = _log_path(name)
    ts = subprocess.run(["date", "+%Y-%m-%d %H:%M:%S"], capture_output=True, text=True).stdout.strip()
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"  JOKI SESSION LOG")
    lines.append(f"  Session: {name}")
    lines.append(f"  Date: {ts}")
    lines.append(f"{'='*60}\n")
    role_label = {"system": "SYSTEM", "user": "USER", "assistant": "JOKI", "tool": "TOOL"}
    for msg in messages:
        role = msg.get("role", "unknown")
        content = _clean_latex((msg.get("content") or ""))
        tool_calls = msg.get("tool_calls")
        label = role_label.get(role, role.upper())
        if role == "system":
            continue
        if tool_calls:
            for tc in tool_calls:
                fn = tc["function"]
                lines.append(f"[JOKI → {fn['name']}]")
                args_str = fn.get("arguments", "")
                if isinstance(args_str, str) and len(args_str) > 200:
                    args_str = args_str[:200] + "..."
                lines.append(f"  args: {args_str}")
            lines.append("")
        elif content:
            for c in content.splitlines():
                lines.append(f"[{label}] {c}")
            lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path

def save_session(messages, name=None):
    global _CURRENT_SESSION
    os.makedirs(SESSION_DIR, exist_ok=True)
    if not name:
        ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
        name = f"session_{ts}"
    path = _session_path(name)
    with open(path, "w") as f:
        json.dump({"messages": messages}, f, indent=2)
    _CURRENT_SESSION = name
    mem = _load_memory()
    if mem:
        _save_memory(mem, name=name)
    auto_save_log(messages, name)
    return name, path

def _load_session_data(name):
    path = _session_path(name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def list_sessions():
    os.makedirs(SESSION_DIR, exist_ok=True)
    files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")]
    if not files:
        return "(no saved sessions)"
    files.sort(key=lambda f: os.path.getmtime(os.path.join(SESSION_DIR, f)), reverse=True)
    lines = []
    for i, f in enumerate(files, 1):
        fname = f.replace(".json", "")
        size = os.path.getsize(os.path.join(SESSION_DIR, f))
        mod = os.path.getmtime(os.path.join(SESSION_DIR, f))
        lines.append(f"  {i:>2}. {fname}  ({size}b, {datetime.fromtimestamp(mod).strftime('%Y-%m-%d %H:%M')})")
    return "\n".join(lines), files

def view_session_history(name):
    data = _load_session_data(name)
    if data is None:
        return f"Session '{name}' not found."
    messages = data.get("messages", [])
    if not messages:
        return "(empty session)"
    console = Console()
    output = []
    role_label = {"system": "SYSTEM", "user": "USER", "assistant": "JOKI", "tool": "TOOL"}
    role_color = {"system": "dim", "user": "yellow", "assistant": "cyan", "tool": "magenta"}
    for msg in messages:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "")
        tool_calls = msg.get("tool_calls")
        label = role_label.get(role, role.upper())
        color = role_color.get(role, "white")
        if role == "system":
            continue
        if tool_calls:
            output.append(f"[{label}]")
            for tc in tool_calls:
                fn = tc["function"]
                args_str = fn.get("arguments", "")
                if isinstance(args_str, str) and len(args_str) > 300:
                    args_str = args_str[:300] + "..."
                output.append(f"  \u2192 {fn['name']}({args_str})")
        elif content:
            output.append(f"[{label}]")
            for line in content.strip().splitlines():
                output.append(f"  {line}")
        output.append("")
    return "\n".join(output)

# ============================================================
# MAIN
# ============================================================
