import os
import re
import shlex
import subprocess

from joki.state import _WORKSPACE_ROOT

_SHELL_META = re.compile(r'[;|&`$()<>]')


def _ref_ok(value, label):
    """Validasi nama ref/remote: tolak awalan '-' (injeksi flag) + meta shell.

    Tanpa ini, mis. branch="-h" ditafsir git sebagai flag, bukan nama.
    Return pesan "[ERROR] ..." bila tidak valid, else None.
    """
    v = str(value or "")
    if not v:
        return f"[ERROR] Parameter '{label}' wajib diisi."
    if v.startswith("-"):
        return f"[ERROR] {label} tidak boleh diawali '-': {v}"
    if _SHELL_META.search(v):
        return f"[ERROR] {label} mengandung karakter tidak valid: {v}"
    return None


def _git_run(cmd, cwd=None):
    if not cwd:
        cwd = _WORKSPACE_ROOT or os.getcwd()
    shell = isinstance(cmd, str)
    try:
        r = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, timeout=60, cwd=cwd, check=False
        )
        output = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return output.strip() or "(no output)"
        else:
            return f"[ERROR] exit {r.returncode}\n{output.strip()}"
    except subprocess.TimeoutExpired:
        return "[ERROR] Command timeout (>60s)"
    except FileNotFoundError:
        return "[ERROR] Git tidak ditemukan. Install git terlebih dahulu."
    except Exception as e:  # noqa: BLE001
        return f"[ERROR] {e}"


def handle_git_status(args):
    cwd = args.get("cwd", "")
    return _git_run("git status", cwd)


def handle_git_diff(args):
    cwd = args.get("cwd", "")
    staged = args.get("staged", False)
    path = args.get("path", "")
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--cached")
    if path:
        cmd.extend(["--", path])
    return _git_run(cmd, cwd)


def handle_git_log(args):
    cwd = args.get("cwd", "")
    max_count = args.get("max_count", 10)
    fmt = args.get("format", "oneline")
    branch = args.get("branch", "")

    if branch:
        err = _ref_ok(branch, "branch")
        if err:
            return err
    if fmt and fmt not in ("oneline", "full") and _SHELL_META.search(fmt):
        return f"[ERROR] Format mengandung karakter tidak valid: {fmt}"

    cmd = ["git", "log"]
    if fmt == "oneline":
        cmd.append("--oneline")
    elif fmt == "full":
        pass
    else:
        cmd.append(f"--format={fmt}")
    try:
        max_count = int(max_count)
    except (TypeError, ValueError):
        return f"[ERROR] max_count harus angka: {max_count}"
    max_count = max(1, min(max_count, 500))
    cmd.append(f"--max-count={max_count}")
    if branch:
        # Tanpa '--': di git log, '--' memisahkan PATH bukan branch.
        # Injeksi flag sudah ditutup _ref_ok di atas.
        cmd.append(branch)
    return _git_run(cmd, cwd)


def handle_git_commit(args):
    cwd = args.get("cwd", "")
    message = args.get("message", "")
    if not message:
        return "[ERROR] Parameter 'message' wajib diisi."
    no_verify = args.get("no_verify", False)
    cmd = ["git", "commit"]
    if no_verify:
        cmd.append("--no-verify")
    cmd.extend(["-m", message])
    return _git_run(cmd, cwd)


def handle_git_push(args):
    cwd = args.get("cwd", "")
    remote = args.get("remote", "origin")
    branch = args.get("branch", "")
    force = args.get("force", False)
    set_upstream = args.get("set_upstream", False)
    err = _ref_ok(remote, "remote")
    if err:
        return err
    if branch:
        err = _ref_ok(branch, "branch")
        if err:
            return err
    cmd = ["git", "push"]
    if force:
        cmd.append("--force")
    if set_upstream:
        cmd.append("-u")
    cmd.append(remote)
    if branch:
        cmd.append(branch)
    return _git_run(cmd, cwd)


def handle_git_pull(args):
    cwd = args.get("cwd", "")
    remote = args.get("remote", "origin")
    branch = args.get("branch", "")
    rebase = args.get("rebase", False)
    err = _ref_ok(remote, "remote")
    if err:
        return err
    if branch:
        err = _ref_ok(branch, "branch")
        if err:
            return err
    cmd = ["git", "pull"]
    if rebase:
        cmd.append("--rebase")
    cmd.append(remote)
    if branch:
        cmd.append(branch)
    return _git_run(cmd, cwd)


def handle_git_branch(args):
    cwd = args.get("cwd", "")
    action = args.get("action", "list")
    name = args.get("name", "")
    if action == "list":
        return _git_run(["git", "branch"], cwd)
    elif action in ("create", "delete", "delete_force", "switch", "create_switch"):
        err = _ref_ok(name, "name")
        if err:
            return err
    else:
        return f"[ERROR] Action '{action}' tidak dikenal."
    if action == "create":
        return _git_run(["git", "branch", "--", name], cwd)
    elif action == "delete":
        return _git_run(["git", "branch", "-d", "--", name], cwd)
    elif action == "delete_force":
        return _git_run(["git", "branch", "-D", "--", name], cwd)
    elif action == "switch":
        # Tanpa '--': di checkout, '--' memisahkan PATHSPEC bukan branch.
        return _git_run(["git", "checkout", name], cwd)
    elif action == "create_switch":
        return _git_run(["git", "checkout", "-b", name], cwd)


def handle_git_clone(args):
    url = args.get("url", "")
    dest = args.get("dest", "")
    branch = args.get("branch", "")
    depth = args.get("depth", 0)
    if not url:
        return "[ERROR] Parameter 'url' wajib diisi."
    if str(url).startswith("-"):
        return f"[ERROR] url tidak boleh diawali '-': {url}"
    if dest and str(dest).startswith("-"):
        return f"[ERROR] dest tidak boleh diawali '-': {dest}"
    cmd = ["git", "clone"]
    if branch:
        err = _ref_ok(branch, "branch")
        if err:
            return err
        cmd.extend(["--branch", branch])
    if depth > 0:
        cmd.extend(["--depth", str(depth)])
    cmd.append(url)
    if dest:
        cmd.append(dest)
    return _git_run(cmd)


def handle_git_init(args):
    cwd = args.get("cwd", _WORKSPACE_ROOT)
    return _git_run("git init", cwd)


def handle_git_add(args):
    cwd = args.get("cwd", "")
    files = args.get("files", ".")
    cmd = ["git", "add", "--"] + shlex.split(files)
    return _git_run(cmd, cwd)


def handle_git_merge(args):
    cwd = args.get("cwd", "")
    branch = args.get("branch", "")
    err = _ref_ok(branch, "branch")
    if err:
        return err
    return _git_run(["git", "merge", "--", branch], cwd)


def handle_git_stash(args):
    cwd = args.get("cwd", "")
    action = args.get("action", "save")
    message = args.get("message", "")
    if action == "save":
        cmd = ["git", "stash", "push"]
        if message:
            cmd.extend(["-m", message])
        return _git_run(cmd, cwd)
    elif action == "pop":
        return _git_run(["git", "stash", "pop"], cwd)
    elif action == "list":
        return _git_run(["git", "stash", "list"], cwd)
    elif action == "drop":
        return _git_run(["git", "stash", "drop"], cwd)
    elif action == "apply":
        return _git_run(["git", "stash", "apply"], cwd)
    else:
        return f"[ERROR] Action '{action}' tidak dikenal."


def handle_git_remote(args):
    cwd = args.get("cwd", "")
    action = args.get("action", "list")
    name = args.get("name", "origin")
    url = args.get("url", "")
    if action == "list":
        return _git_run(["git", "remote", "-v"], cwd)
    elif action in ("add", "remove", "set_url"):
        err = _ref_ok(name, "name")
        if err:
            return err
        if action != "remove":
            if not url:
                return f"[ERROR] Parameter 'url' wajib untuk {action}."
            if str(url).startswith("-"):
                return f"[ERROR] url tidak boleh diawali '-': {url}"
    else:
        return f"[ERROR] Action '{action}' tidak dikenal."
    if action == "add":
        return _git_run(["git", "remote", "add", name, url], cwd)
    elif action == "remove":
        return _git_run(["git", "remote", "remove", name], cwd)
    elif action == "set_url":
        return _git_run(["git", "remote", "set-url", name, url], cwd)


def shq(s):
    return "'" + s.replace("'", "'\\''") + "'"
