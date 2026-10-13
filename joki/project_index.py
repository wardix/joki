import ast
import hashlib
import json
import os
import re
import time

from joki.config import _get_data_dir

_EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
    "coverage", ".ruff_cache", ".mypy_cache", ".pytest_cache", "vendor",
    "target", ".idea", ".vscode", ".next", ".nuxt", "site-packages",
    ".tox", ".nox", ".eggs", ".cache", "htmlcov",
}

_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".scala",
    ".c", ".h", ".cpp", ".hpp", ".cxx", ".cc", ".cs", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".mm", ".dart", ".lua",
    ".sh", ".bash", ".zsh", ".pl", ".pm", ".r", ".jl",
    ".sql", ".css", ".scss", ".less", ".sass", ".vue", ".svelte",
    ".zig", ".nim", ".ex", ".exs", ".html", ".htm", ".xhtml", ".xml",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}

_SYMBOL_RE = re.compile(
    r'^\s*(?:async\s+def|def|class|function|func|pub\s+fn|fn|struct|trait|impl|enum|'
    r'interface|type|const|var|let)\s+([A-Za-z_]\w*)'
)

_MAX_FILES = 2000
_MAX_SYMBOL_LINES = 2000
_MAX_SYMBOL_BYTES = 300_000
_MAX_SYMBOLS_PER_FILE = 80


def _index_dir():
    return os.path.join(_get_data_dir(), "project_index")


def _index_path(root):
    h = hashlib.md5(os.path.realpath(root).encode()).hexdigest()[:16]
    return os.path.join(_index_dir(), f"{h}.json")


def _scan_symbols_ast(path):
    """Ekstrak simbol Python via ast (akurat utk async/nested/multi-line).
    Return None bila file bukan Python valid → caller fallback ke regex."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append((node.name, getattr(node, "lineno", 0)))
    symbols.sort(key=lambda s: s[1])
    return symbols


def _scan_symbols(path):
    """Ekstrak nama fungsi/class/struct dari file kode (terbatas agar cepat)."""
    symbols = []
    try:
        if os.path.getsize(path) > _MAX_SYMBOL_BYTES:
            return symbols
        if os.path.splitext(path)[1].lower() == ".py":
            ast_syms = _scan_symbols_ast(path)
            if ast_syms is not None:
                return ast_syms
        with open(path, encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if lineno > _MAX_SYMBOL_LINES:
                    break
                m = _SYMBOL_RE.match(line)
                if m:
                    symbols.append((m.group(1), lineno))
    except OSError:
        pass
    return symbols


def _scan_project(root):
    """Scan struktur proyek → index ringkas (tree + simbol per file kode)."""
    root = os.path.realpath(root)
    files = {}
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        dirnames.sort()
        for fn in sorted(filenames):
            if file_count >= _MAX_FILES:
                break
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entry = {"mtime": st.st_mtime, "size": st.st_size}
            ext = os.path.splitext(fn)[1].lower()
            if ext in _CODE_EXTS:
                syms = _scan_symbols(full)
                if syms:
                    entry["symbols"] = syms[:_MAX_SYMBOLS_PER_FILE]
            files[rel] = entry
            file_count += 1
        if file_count >= _MAX_FILES:
            break
    return {
        "root": root,
        "scanned_at": time.time(),
        "files": files,
    }


def _load_index(root):
    path = _index_path(root)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _iter_project_files(root):
    """Yield (rel, full, stat) hormati excludes + cap, urutan sama persis
    dengan _scan_project agar validasi sebanding dengan isi cache."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        dirnames.sort()
        for fn in sorted(filenames):
            if count >= _MAX_FILES:
                return
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            count += 1
            yield os.path.relpath(full, root), full, st


def _sync_index(root, idx):
    """Selaraskan cache dengan filesystem (set file + mtime/size per file).

    Entri basi (diubah/di luar Joki) di-scan ulang hanya file itu; file
    hapus dibuang; file baru ditambah. Return index segar (disimpan bila berubah).
    """
    files = idx.setdefault("files", {})
    current = {}
    for rel, full, st in _iter_project_files(root):
        current[rel] = (full, st.st_mtime, st.st_size)
    changed = False
    for rel in [r for r in files if r not in current]:
        files.pop(rel, None)
        changed = True
    for rel, (full, mtime, size) in current.items():
        ent = files.get(rel)
        if ent is None or ent.get("mtime") != mtime or ent.get("size") != size:
            entry = {"mtime": mtime, "size": size}
            if os.path.splitext(rel)[1].lower() in _CODE_EXTS:
                syms = _scan_symbols(full)
                if syms:
                    entry["symbols"] = syms[:_MAX_SYMBOLS_PER_FILE]
            files[rel] = entry
            changed = True
    if changed:
        idx["scanned_at"] = time.time()
        _save_index(root, idx)
    return idx


def _save_index(root, index):
    try:
        path = _index_path(root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f)
        os.replace(tmp, path)
    except OSError:
        pass


def get_project_index(root, force=False):
    """Ambil index proyek (cache disk → selaraskan mtime bila ada → scan
    ulang bila tidak ada/force). Edit di luar Joki tak lagi bikin basi."""
    root = os.path.realpath(root)
    idx = None if force else _load_index(root)
    if idx is None:
        idx = _scan_project(root)
        _save_index(root, idx)
    else:
        idx = _sync_index(root, idx)
    return idx


def refresh_project_index(root, changed_paths):
    """Perbarui index untuk file yang berubah tanpa scan ulang seluruh proyek."""
    root = os.path.realpath(root)
    idx = _load_index(root) or _scan_project(root)
    files = idx.setdefault("files", {})
    for p in changed_paths:
        try:
            rel = os.path.relpath(p, root)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            try:
                st = os.stat(full)
            except OSError:
                continue
            entry = {"mtime": st.st_mtime, "size": st.st_size}
            if os.path.splitext(rel)[1].lower() in _CODE_EXTS:
                syms = _scan_symbols(full)
                if syms:
                    entry["symbols"] = syms[:_MAX_SYMBOLS_PER_FILE]
            files[rel] = entry
        else:
            files.pop(rel, None)
    idx["scanned_at"] = time.time()
    _save_index(root, idx)
    return idx


def _build_index_text(index, max_files=250, max_symbol_files=120, max_symbols=25):
    """Buat teks ringkas peta proyek untuk di-inject ke prompt."""
    files = index.get("files", {})
    rels = sorted(files)
    if not rels:
        return ""
    lines = ["[INDEX PROYEK]", f"Struktur proyek ({len(files)} file terindeks):"]
    shown = 0
    deep_skipped = 0
    for rel in rels:
        if shown >= max_files:
            break
        depth = rel.count(os.sep)
        if depth >= 4:
            deep_skipped += 1
            continue
        indent = "  " * depth
        lines.append(f"{indent}· {rel}")
        shown += 1
    hidden_files = len(rels) - shown - deep_skipped
    if deep_skipped or hidden_files > 0:
        lines.append(f"... [{hidden_files} file lain + {deep_skipped} file dalam disembunyikan — minta path spesifik bila perlu]")
    sym_lines = []
    sym_hidden = 0
    for rel in rels:
        entry = files.get(rel)
        if not entry or not entry.get("symbols"):
            continue
        if len(sym_lines) >= max_symbol_files:
            sym_hidden += 1
            continue
        names = [s[0] for s in entry["symbols"]]
        if len(names) > max_symbols:
            names = names[:max_symbols] + ["..."]
        sym_lines.append(f"  {rel}: {', '.join(names)}")
    if sym_lines:
        lines.append("Simbol utama:")
        lines.extend(sym_lines)
    if sym_hidden:
        lines.append(f"... [{sym_hidden} file bersimbol lain disembunyikan]")
    lines.append("[INDEX BERAKHIR]")
    return "\n".join(lines)
