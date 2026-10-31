import os
import shutil
import subprocess

from joki.state import _WORKSPACE_ROOT

_LINTERS = {
    "python": {
        "cmd": ["ruff", "check", "--output-format=concise"],
        "install": "pip install ruff",
        "ext": [".py", ".pyi"],
        "fix_flag": "--fix",
    },
    "javascript": {
        "cmd": ["eslint", "--format=compact"],
        "install": "npm install -g eslint",
        "ext": [".js", ".mjs", ".cjs"],
        "fix_flag": "--fix",
    },
    "typescript": {
        "cmd": ["eslint", "--format=compact", "--ext", ".ts,.tsx"],
        "install": "npm install -g eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin",
        "ext": [".ts", ".tsx"],
        "fix_flag": "--fix",
    },
    "php": {
        "cmd": ["phpcs", "--report=full"],
        "install": "composer global require squizlabs/php_codesniffer",
        "ext": [".php"],
        "fix_flag": None,
    },
    "go": {
        "cmd": ["golangci-lint", "run", "--out-format=line-number"],
        "install": "go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest",
        "ext": [".go"],
        "fix_flag": "--fix",
    },
    "rust": {
        "cmd": ["cargo", "clippy", "--message-format=short"],
        "install": "rustup component add clippy",
        "ext": [".rs"],
        "fix_flag": None,
    },
    "ruby": {
        "cmd": ["rubocop", "--format=simple"],
        "install": "gem install rubocop",
        "ext": [".rb"],
        "fix_flag": "-a",
    },
    "shell": {
        "cmd": ["shellcheck", "-f", "gcc"],
        "install": "apt install shellcheck",
        "ext": [".sh", ".bash", ".zsh"],
        "fix_flag": None,
    },
    "css": {
        "cmd": ["stylelint", "--formatter=compact"],
        "install": "npm install -g stylelint",
        "ext": [".css", ".scss", ".less"],
        "fix_flag": "--fix",
    },
    "html": {
        "cmd": ["htmlhint", "--format=compact"],
        "install": "npm install -g htmlhint",
        "ext": [".html", ".htm"],
        "fix_flag": None,
    },
}


def _get_linter_for_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    for lang, cfg in _LINTERS.items():
        if ext in cfg["ext"]:
            return lang, cfg
    return None, None


def _run_linter(lang, cfg, paths, fix=False):
    if not shutil.which(cfg["cmd"][0]):
        return f"[SKIP] {cfg['cmd'][0]} tidak terinstall.\nInstall: {cfg['install']}"

    cmd = cfg["cmd"][:]
    if fix and cfg["fix_flag"]:
        cmd.append(cfg["fix_flag"])
    cmd.extend(paths)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError:
        return f"[SKIP] {cfg['cmd'][0]} tidak ditemukan."
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] Linter timeout (>120s) untuk {lang}"
    except Exception as e:  # noqa: BLE001
        return f"[ERROR] {e}"

    output = (r.stdout or "") + (r.stderr or "")
    if not output.strip():
        return f"[OK] {lang}: tidak ada masalah."
    lines = output.strip().splitlines()
    severity = "OK"
    fixable_count = 0
    for line in lines:
        if "error" in line.lower():
            severity = "ERROR"
        elif "warning" in line.lower() and severity != "ERROR":
            severity = "WARNING"
        if "fixable" in line.lower():
            fixable_count += 1

    result = f"[{severity}] {lang} ({len(lines)} issue(s))"
    if fixable_count:
        result += f" ({fixable_count} auto-fixable dengan --fix)"
    result += "\n" + "\n".join(lines)
    return result


def handle_run_linter(args):
    path = args.get("path", _WORKSPACE_ROOT)
    fix = args.get("fix", False)
    lang = args.get("lang", "")

    if not os.path.exists(path):
        return f"[ERROR] Path tidak ditemukan: {path}"

    if lang:
        cfg = _LINTERS.get(lang)
        if not cfg:
            return f"[ERROR] Linter untuk '{lang}' tidak dikenal. Tersedia: {', '.join(_LINTERS.keys())}"
        linter_exe = shutil.which(cfg["cmd"][0])
        if not linter_exe:
            return f"[SKIP] {cfg['cmd'][0]} tidak terinstall.\nInstall: {cfg['install']}"
        paths = [path]
        return _run_linter(lang, cfg, paths, fix)

    if os.path.isfile(path):
        lang_name, cfg = _get_linter_for_file(path)
        if not cfg:
            return f"[SKIP] Tidak ada linter untuk file: {path}"
        return _run_linter(lang_name, cfg, [path], fix)

    results = []
    for root, dirs, fnames in os.walk(path):
        dn = os.path.basename(root)
        if dn.startswith(".") or dn in ("node_modules", "__pycache__", "venv", ".git", "vendor", "target"):
            dirs[:] = []
            continue
        for fname in fnames:
            fpath = os.path.join(root, fname)
            lang_name, cfg = _get_linter_for_file(fpath)
            if cfg:
                try:
                    if os.path.getsize(fpath) > 500_000:
                        continue
                except Exception:  # noqa: BLE001, S112
                    continue
                results.append((lang_name, cfg, fpath))

    if not results:
        return "[OK] Tidak ada file yang perlu di-lint."

    lang_groups = {}
    for lang_name, cfg, fpath in results:
        lang_groups.setdefault(lang_name, {"cfg": cfg, "files": []})["files"].append(fpath)

    output = []
    for lang_name, group in lang_groups.items():
        r = _run_linter(lang_name, group["cfg"], group["files"], fix)
        output.append(r)

    return "\n---\n".join(output)


def handle_lint_install(args):
    lang = args.get("lang", "")
    if lang:
        cfg = _LINTERS.get(lang)
        if not cfg:
            return f"[ERROR] Linter untuk '{lang}' tidak dikenal. Tersedia: {', '.join(_LINTERS.keys())}"
        if shutil.which(cfg["cmd"][0]):
            return f"[OK] {cfg['cmd'][0]} sudah terinstall."
        cmd = cfg["install"]
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, check=False)
            if r.returncode == 0:
                return f"[OK] Berhasil install {lang} linter."
            return f"[ERROR] Gagal install: {r.stderr.strip()}"
        except Exception as e:  # noqa: BLE001
            return f"[ERROR] {e}"
    else:
        installed = []
        not_installed = []
        for lang_name, cfg in _LINTERS.items():
            if shutil.which(cfg["cmd"][0]):
                installed.append(lang_name)
            else:
                not_installed.append(lang_name)
        lines = ["Installed: " + (", ".join(installed) if installed else "(none)")]
        if not_installed:
            lines.append("Not installed: " + ", ".join(not_installed))
            lines.append("Gunakan lint_install(lang=\"<nama>\") untuk install.")
        return "\n".join(lines)
