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


def handle_read_file(args):
    with open(args["path"]) as f:
        return _numbered(f.read())


def handle_write_file(args):
    path = args["path"]
    new = args["content"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    diff_str = ""
    if os.path.exists(path):
        with open(path) as f:
            old = f.read()
        if old != new:
            diff = unified_diff(
                old.splitlines(
                    keepends=True), new.splitlines(
                    keepends=True), fromfile=path, tofile=path)
            diff_str = "".join(diff)
    with open(path, "w") as f:
        f.write(new)
    msg = f"Written: {path} ({len(new)} bytes)"
    if diff_str:
        msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
    return msg


def handle_edit_file(args):
    with open(args["path"]) as f:
        old = f.read()
    ot = args["old_text"]
    if not ot:
        new = args["new_text"] + old
    else:
        if ot not in old:
            return f"Error: 'old_text' not found in {args['path']}"
        new = old.replace(ot, args["new_text"])
    diff = unified_diff(
        old.splitlines(
            keepends=True),
        new.splitlines(
            keepends=True),
        fromfile=args["path"],
        tofile=args["path"])
    with open(args["path"], "w") as f:
        f.write(new)
    msg = f"Edited: {args['path']}"
    diff_str = "".join(diff)
    if diff_str:
        msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
    return msg


def handle_search_code(args):
    cmd = ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
           "--include=*.html", "--include=*.css", "--include=*.json",
           "--include=*.yaml", "--include=*.yml", "--include=*.md",
           "--include=*.conf", "--include=*.cfg", "--include=*.ini",
           args["pattern"], args.get("path", ".")]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout or "(not found)"


def handle_list_dir(args):
    items = os.listdir(args["path"])
    lines = []
    for item in sorted(items):
        full = os.path.join(args["path"], item)
        label = "DIR" if os.path.isdir(full) else "   "
        lines.append(f"{label} {item}")
    return "\n".join(lines)


def handle_config_edit(args):
    path = args["path"]
    if not os.path.exists(path):
        return f"Error: file not found: {path}"

    with open(path) as f:
        content = f.read()

    directive = args.get("directive")
    set_value = args.get("set_value")

    if not directive:
        return _numbered(content)

    # Show current value
    pattern = re.compile(rf'^\s*{re.escape(directive)}\s+(.+)$', re.MULTILINE)
    matches = pattern.findall(content)
    if not set_value:
        if not matches:
            return f"Directive '{directive}' not found in {path}"
        return f"Current value(s) for '{directive}': {matches}"

    # Backup then edit
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, os.path.basename(path) + ".bak")
    shutil.copy2(path, backup_path)

    if matches:
        # Replace first occurrence
        new_content = pattern.sub(f"{directive} {set_value}", content, count=1)
    else:
        # Append at end
        new_content = content.rstrip() + f"\n{directive} {set_value}\n"

    with open(path, "w") as f:
        f.write(new_content)

    return f"Backup saved: {backup_path}\nEdited: {directive} → {set_value}"
