import ast
import hashlib
import json
import os
import re
import time
from collections import defaultdict

from joki.config import _get_data_dir
from joki.state import _WORKSPACE_ROOT, _is_project_root

_RESOLVE_EXTS = [".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx",
                ".go", ".php", ".rs", ".rb", ".java", ".cs"]


_JS_TS_SHARED = [
    re.compile(r'require\([\'"](\S+?)[\'"]\)'),
    re.compile(r'import\s*\(\s*[\'"](\S+?)[\'"]\s*\)'),  # dynamic import()
    re.compile(r'^import\s+type\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    re.compile(r'^import\s+(?:\{[^}]*\}|\S+)\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    re.compile(r'^import\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    re.compile(r'^export\s+(?:\{[^}]*\}|\*).*?\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    re.compile(r'^export\s+type\s+(?:\{[^}]*\}|\S+).*?\s+from\s+[\'"](\S+?)[\'"]', re.MULTILINE),
]

_IMPORT_PATTERNS = {
    ".py": [
        re.compile(r'^\s*import\s+(\S+)', re.MULTILINE),
        re.compile(r'^\s*from\s+(\S+)\s+import', re.MULTILINE),
    ],
    ".js": _JS_TS_SHARED,
    ".jsx": _JS_TS_SHARED,
    ".mjs": _JS_TS_SHARED,
    ".cjs": _JS_TS_SHARED,
    ".ts": _JS_TS_SHARED,
    ".tsx": _JS_TS_SHARED,
    ".mts": _JS_TS_SHARED,
    ".cts": _JS_TS_SHARED,
    ".go": [
        re.compile(r'^\s*import\s+[\'"](.+?)[\'"]', re.MULTILINE),
        re.compile(r'^\s*import\s+\(([^)]+)\)', re.MULTILINE),
    ],
    ".php": [
        re.compile(r'^\s*(?:<\?php\s+)?(?:use\s+function\s+|use\s+const\s+|use\s+)([\w\\]+)\s*;', re.MULTILINE),
        re.compile(r'^\s*(?:<\?php\s+)?require(?:_once)?\s*\(?[\'"](\S+?)[\'"]\)?', re.MULTILINE),
        re.compile(r'^\s*(?:<\?php\s+)?include(?:_once)?\s*\(?[\'"](\S+?)[\'"]\)?', re.MULTILINE),
    ],
    ".rs": [
        re.compile(r'^\s*use\s+(\S+);', re.MULTILINE),
        re.compile(r'^\s*extern\s+crate\s+(\S+);', re.MULTILINE),
    ],
    ".rb": [
        re.compile(r'^\s*require\s+[\'"](\S+?)[\'"]', re.MULTILINE),
        re.compile(r'^\s*require_relative\s+[\'"](\S+?)[\'"]', re.MULTILINE),
    ],
    ".java": [
        re.compile(r'^\s*import\s+(\S+);', re.MULTILINE),
    ],
    ".cs": [
        re.compile(r'^\s*using\s+(\S+);', re.MULTILINE),
    ],
}

_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                 "vendor", "target", "build", "dist", ".next",
                 "coverage", ".ruff_cache", ".mypy_cache", ".pytest_cache"}


def _scan_python_imports_ast(content):
    """Scan Python imports dengan AST sungguhan.

    Mengembalikan None saat SyntaxError sehingga caller bisa fallback ke regex.
    Relative import di-encode dengan titik di depan: '.utils', '..helpers'.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    imports = []
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name not in seen:
                    seen.add(name)
                    imports.append(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.module:
                    names = ["." * node.level + node.module]
                else:
                    names = ["." * node.level + a.name for a in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                if name and name not in seen:
                    seen.add(name)
                    imports.append(name)
    return imports


def _scan_go_imports(content):
    """Scan import Go: bentuk blok maupun single-line, sertakan alias.

    Mengembalikan list import path unik berurutan (mis. 'fmt',
    'example.com/m/pkg'). Regex generik merusak blok multi-baris
    (menangkap seluruh blok sebagai satu string), jadi perlu parser khusus.
    """
    imports = []
    for m in re.finditer(r'^\s*import\s*\((.*?)\)', content, re.MULTILINE | re.DOTALL):
        for q in re.finditer(r'"([^"]+)"', m.group(1)):
            imports.append(q.group(1))
    no_blocks = re.sub(r'^\s*import\s*\(.*?\)', '', content, flags=re.MULTILINE | re.DOTALL)
    for m in re.finditer(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', no_blocks, re.MULTILINE):
        imports.append(m.group(1))
    seen, out = set(), []
    for imp in imports:
        if imp not in seen:
            seen.add(imp)
            out.append(imp)
    return out


def _scan_js_ts_imports(content):
    """Scan import JS/TS: kembalikan specifier UTUH tanpa dipotong.

    Heuristik lama (split('.') ala pola `import a.b`) merusak specifier
    relatif ('./help' → '') dan subpath paket ('lodash/map' → 'lodash'),
    jadi JS/TS punya jalur sendiri. Resolusi yang menentukan lokal vs
    eksternal, bukan pemotongan string di sini.
    """
    imports = []
    for pattern in _JS_TS_SHARED:
        for m in pattern.findall(content):
            s = m[0] if isinstance(m, tuple) else m
            if s and s not in imports:
                imports.append(s)
    return imports


def _scan_file_imports(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    patterns = _IMPORT_PATTERNS.get(ext, [])
    if not patterns:
        return [], ""
    try:
        with open(file_path, "r", errors="ignore") as f:
            content = f.read()
    except OSError:
        return [], ""
    if ext == ".py":
        ast_imports = _scan_python_imports_ast(content)
        if ast_imports is not None:
            return ast_imports, ext
    if ext == ".go":
        return _scan_go_imports(content), ext
    if ext in (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx"):
        return _scan_js_ts_imports(content), ext
    imports = set()
    for pattern in patterns:
        matches = pattern.findall(content)
        is_from_pattern = "from" in pattern.pattern and "import" in pattern.pattern
        for m in matches:
            if isinstance(m, str):
                if is_from_pattern:
                    imports.add(m)
                else:
                    imports.add(m.split(".")[0])
            elif isinstance(m, tuple):
                if is_from_pattern:
                    imports.add(m[0])
                else:
                    imports.add(m[0].split(".")[0])
    return list(imports), ext


def _find_local_files(root_dir):
    files_by_ext = defaultdict(list)
    for root, dirs, fnames in os.walk(root_dir):
        dn = os.path.basename(root)
        if dn.startswith(".") or dn in _EXCLUDE_DIRS:
            dirs[:] = []
            continue
        for fname in fnames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in _IMPORT_PATTERNS:
                try:
                    if os.path.getsize(fpath) > 500_000:
                        continue
                    files_by_ext[ext].append(fpath)
                except Exception:  # noqa: BLE001, S112
                    continue
    return files_by_ext


def _candidate_paths(module_path, ext):
    exts = [ext] + [e for e in _RESOLVE_EXTS if e != ext]
    candidates = []
    for base_ext in exts:
        candidates.extend([
            f"{module_path}{base_ext}",
            f"{module_path}/__init__{base_ext}",
            f"{module_path}/index{base_ext}",
            f"{module_path}/mod{base_ext}",
            f"{module_path}/main{base_ext}",
        ])
    return candidates


def _resolve_relative_import(module_name, ext, from_dir, search_dirs):
    base = from_dir or (search_dirs[0] if search_dirs else None)
    if not base or not os.path.isdir(base):
        return None
    stripped = module_name.lstrip(".")
    level = len(module_name) - len(stripped)
    pkg_dir = os.path.abspath(base)
    for _ in range(max(0, level - 1)):
        parent = os.path.dirname(pkg_dir)
        if parent == pkg_dir:
            return None
        pkg_dir = parent
    if stripped:
        module_path = stripped.replace(".", "/")
        candidates = _candidate_paths(f"{pkg_dir}/{module_path}", ext)
    else:
        candidates = _candidate_paths(f"{pkg_dir}/__init__", ext)
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None


def _module_to_path(module_name, ext, search_dirs=None, from_dir=None):
    if not module_name:
        return None
    if search_dirs is None:
        search_dirs = ["."]

    if module_name.startswith("."):
        return _resolve_relative_import(module_name, ext, from_dir, search_dirs)

    def _try_resolve(mod_name):
        if "." in mod_name:
            module_path = mod_name.replace(".", "/")
        else:
            module_path = mod_name
        candidates = _candidate_paths(module_path, ext)
        for d in search_dirs:
            for c in candidates:
                fp = os.path.join(d, c)
                if os.path.exists(fp):
                    return os.path.abspath(fp)
        return None

    # Coba full path dulu (e.g. "joki.state" → "joki/state.py")
    result = _try_resolve(module_name)
    if result:
        return result

    # Fallback ke segmen pertama (e.g. "httpx" untuk "from httpx import X")
    first_seg = module_name.split(".")[0]
    if first_seg != module_name:
        return _try_resolve(first_seg)
    return None


def _resolve_file_entry(root_dir, fpath):
    """Hitung entri graph untuk satu file (imports + resolved lokal + stamp
    mtime/size agar cache bisa divalidasi tanpa resolve ulang semua)."""
    imports, ext = _scan_file_imports(fpath)
    try:
        st = os.stat(fpath)
        stamp = [st.st_mtime_ns, st.st_size]
    except OSError:
        stamp = None
    rel = os.path.relpath(fpath, root_dir)
    search_dirs = [os.path.dirname(fpath), root_dir]
    resolved = set()
    for imp in imports:
        special = _resolve_special_import(imp, ext, root_dir, os.path.dirname(fpath), fpath)
        resolved_path = special or _module_to_path(imp, ext, search_dirs, from_dir=os.path.dirname(fpath))
        if resolved_path:
            if isinstance(resolved_path, list):
                for rp in resolved_path:
                    rrel = os.path.relpath(rp, root_dir)
                    if rrel != rel:
                        resolved.add(rrel)
            else:
                rrel = os.path.relpath(resolved_path, root_dir)
                if rrel != rel:
                    resolved.add(rrel)
    return rel, {"imports": imports, "resolved": sorted(resolved), "stamp": stamp}


_JS_TS_EXTS = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx"}
_TSCONFIG_CACHE = {}
_COMPOSER_CACHE = {}
_GO_MAP_CACHE = {}


def _strip_json_comments(text):
    """Hapus // dan /* */ ala tsconfig (heuristik; gagal → caller fallback)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    lines = [re.sub(r"(^|[^:])//.*$", r"\1", ln) for ln in text.splitlines()]
    return "\n".join(lines)


def _load_tsconfig_paths(start_dir):
    """Muat (base_dir, paths) tsconfig terdekat. Cache per direktori."""
    cur = os.path.abspath(start_dir or ".")
    if cur in _TSCONFIG_CACHE:
        return _TSCONFIG_CACHE[cur]
    result = None
    d = cur
    for _ in range(6):
        cand = os.path.join(d, "tsconfig.json")
        if os.path.isfile(cand):
            try:
                if os.path.getsize(cand) > 200_000:
                    break
                with open(cand, encoding="utf-8", errors="ignore") as f:
                    data = json.loads(_strip_json_comments(f.read()))
                opts = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
                base = os.path.normpath(os.path.join(d, opts.get("baseUrl", ".")))
                paths = opts.get("paths", {}) if isinstance(opts.get("paths"), dict) else {}
                result = (base, paths)
            except (OSError, ValueError):
                result = None
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    _TSCONFIG_CACHE[cur] = result
    return result


def _resolve_ts_alias(module_name, ext, from_dir):
    """Resolve import non-relatif JS/TS via tsconfig paths. Return abspath/None."""
    if not module_name or module_name.startswith("."):
        return None
    cfg = _load_tsconfig_paths(from_dir)
    if not cfg:
        return None
    base, paths = cfg
    for alias, targets in paths.items():
        if not isinstance(targets, list) or not targets:
            continue
        star = alias.find("*")
        if star == -1:
            if module_name != alias:
                continue
            prefix, suffix, rest = alias, "", ""
        else:
            prefix, suffix = alias[:star], alias[star + 1:]
            if not module_name.startswith(prefix) or not module_name.endswith(suffix):
                continue
            if len(module_name) < len(prefix) + len(suffix):
                continue
            rest = module_name[len(prefix):len(module_name) - len(suffix) if suffix else None]
        for target in targets:
            if not isinstance(target, str):
                continue
            t = target.replace("*", rest)
            for cand in _candidate_paths(os.path.join(base, t), ext):
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
    return None


def _composer_psr4(root_dir):
    """Parse composer.json autoload.psr-4 → [(prefix, [dirs])] terpanjang dulu."""
    key = os.path.abspath(root_dir)
    if key in _COMPOSER_CACHE:
        return _COMPOSER_CACHE[key]
    result = []
    try:
        with open(os.path.join(key, "composer.json"), encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        for section in ("autoload", "autoload-dev"):
            psr4 = (data.get(section, {}) or {}).get("psr-4", {}) or {}
            for prefix, dirs in psr4.items():
                if isinstance(dirs, str):
                    dirs = [dirs]
                result.append((prefix, [os.path.normpath(os.path.join(key, d)) for d in dirs if isinstance(d, str)]))
    except (OSError, ValueError):
        pass
    result.sort(key=lambda x: -len(x[0]))
    _COMPOSER_CACHE[key] = result
    return result


def _resolve_php_use(module_name, root_dir):
    """Resolve 'use A\\B\\C' via composer PSR-4. Return abspath/None."""
    if not module_name or "\\" not in module_name:
        return None
    cls = module_name.lstrip("\\").rstrip(";")
    for prefix, dirs in _composer_psr4(root_dir):
        if not cls.startswith(prefix):
            continue
        rest = cls[len(prefix):].replace("\\", "/")
        for d in dirs:
            cand = os.path.join(d, rest + ".php")
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    return None


def _go_package_map(root_dir):
    """Petakan package Go lokal via 'go list -e -json ./...'. Cache per root.

    Return (module_path, {rel_dir: [imports]}) atau (None, {}) bila gagal.
    """
    key = os.path.abspath(root_dir)
    if key in _GO_MAP_CACHE:
        return _GO_MAP_CACHE[key]
    result = (None, {})
    if not os.path.isfile(os.path.join(key, "go.mod")):
        _GO_MAP_CACHE[key] = result
        return result
    import shutil as _shutil
    import subprocess as _sp
    if not _shutil.which("go"):
        _GO_MAP_CACHE[key] = result
        return result
    try:
        r = _sp.run(["go", "list", "-e", "-json", "./..."], capture_output=True,
                    text=True, timeout=60, check=False, cwd=key)
        if r.returncode != 0 or not r.stdout.strip():
            _GO_MAP_CACHE[key] = result
            return result
        module = None
        dirs = {}
        dec = json.JSONDecoder()
        idx, s = 0, r.stdout.strip()
        while idx < len(s):
            try:
                obj, idx = dec.raw_decode(s, idx)
            except ValueError:
                break
            while idx < len(s) and s[idx] in " \t\r\n":
                idx += 1
            if not isinstance(obj, dict):
                continue
            if not module and isinstance(obj.get("Module"), dict):
                module = obj["Module"].get("Path", "")
            dirpath = obj.get("Dir", "")
            if dirpath:
                try:
                    rel = os.path.relpath(dirpath, key)
                except ValueError:
                    continue
                if not rel.startswith(".."):
                    imports = obj.get("Imports", []) or []
                    dirs[rel] = [i for i in imports if isinstance(i, str)]
        result = (module or "", dirs)
    except (OSError, ValueError):
        pass
    _GO_MAP_CACHE[key] = result
    return result


def _resolve_go_import(module_name, root_dir, fpath):
    """Resolve import Go ke daftar file .go lokal (over-approximasi per paket).

    Return list[abspath] atau None (eksternal/tak dikenal → fallback generik).
    """
    if not module_name or module_name.startswith("."):
        return None
    module, _dirs = _go_package_map(root_dir)
    if module and module_name.startswith(module):
        rest = module_name[len(module):].strip("/")
        target_rel = rest.replace("/", os.sep) if rest else "."
    elif "/" not in module_name and "." not in module_name:
        return None  # stdlib (fmt, os, ...) atau relatif — bukan paket lokal
    else:
        return None  # import path eksternal (github.com/...) → bukan file lokal
    target_dir = os.path.normpath(os.path.join(root_dir, target_rel))
    if not os.path.isdir(target_dir):
        return None
    out = []
    try:
        for fn in sorted(os.listdir(target_dir)):
            if fn.endswith(".go") and not fn.endswith("_test.go"):
                out.append(os.path.abspath(os.path.join(target_dir, fn)))
    except OSError:
        return None
    if not out:
        return None
    self_abs = os.path.abspath(fpath)
    return [p for p in out if p != self_abs] or None


def _resolve_special_import(module_name, ext, root_dir, from_dir, fpath=""):
    """Resolver deterministik per bahasa sebelum fallback generik.

    Return abspath, list[abspath], atau None (= lanjut ke _module_to_path).
    """
    try:
        if ext in _JS_TS_EXTS:
            return _resolve_ts_alias(module_name, ext, from_dir)
        if ext == ".php":
            return _resolve_php_use(module_name, root_dir)
        if ext == ".go":
            return _resolve_go_import(module_name, root_dir, fpath or from_dir)
    except Exception:  # noqa: BLE001, S110
        pass
    return None


def _build_file_graph(root_dir):
    files_by_ext = _find_local_files(root_dir)
    file_graph = {}
    for ext_files in files_by_ext.values():
        for fpath in ext_files:
            rel, entry = _resolve_file_entry(root_dir, fpath)
            file_graph[rel] = entry
    return file_graph


def _graph_index_path(root):
    h = hashlib.md5(os.path.realpath(root).encode()).hexdigest()[:16]
    return os.path.join(_get_data_dir(), "dep_graph", f"{h}.json")


def _scan_file_symbols(fpath):
    """Ekstrak simbol file → [{"name", "line"}]. Reuse scanner project_index
    (AST untuk Python, regex untuk lainnya). Tak pernah raise."""
    try:
        from joki.project_index import _scan_symbols
        return [{"name": n, "line": ln} for n, ln in (_scan_symbols(fpath) or [])]
    except Exception:  # noqa: BLE001
        return []


def _save_graph_cache(root_dir, files, symbols):
    try:
        path = _graph_index_path(root_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"root": root_dir, "scanned_at": time.time(),
                       "files": files, "symbols": symbols}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _load_graph_cache(root_dir):
    """Return (files, symbols) atau (None, None) bila cache tak valid."""
    try:
        with open(_graph_index_path(root_dir), encoding="utf-8") as f:
            data = json.load(f)
        files = data.get("files")
        symbols = data.get("symbols")
        if isinstance(files, dict) and isinstance(symbols, dict):
            return files, symbols
    except (OSError, ValueError):
        pass
    return None, None


def _build_symbols(files_by_ext, root_dir):
    symbols = {}
    for ext_files in files_by_ext.values():
        for fpath in ext_files:
            try:
                rel = os.path.relpath(fpath, root_dir)
                syms = _scan_file_symbols(fpath)
                if syms:
                    symbols[rel] = syms[:80]
            except (OSError, ValueError):
                continue
    return symbols


def _sync_graph_cache(root_dir, files, symbols):
    """Selaraskan cache graph dengan filesystem (set file + stamp mtime/size).

    Hanya file tambah/hapus/berubah yang di-resolve ulang (+ simbolnya);
    memo referensi dibuang bila ada perubahan. Return (files, symbols, changed).
    Entri cache lama tanpa stamp dianggap basi dan di-resolve ulang sekali.
    """
    try:
        files_by_ext = _find_local_files(root_dir)
    except Exception:  # noqa: BLE001
        return files, symbols, False
    current = {}
    for ext_files in files_by_ext.values():
        for fpath in ext_files:
            try:
                current[os.path.relpath(fpath, root_dir)] = fpath
            except ValueError:
                continue
    changed = False
    for rel in [r for r in files if r not in current]:
        files.pop(rel, None)
        symbols.pop(rel, None)
        for info in files.values():
            res = info.get("resolved", [])
            if rel in res:
                info["resolved"] = [r for r in res if r != rel]
        changed = True
    for rel, fpath in current.items():
        stamp = _file_stamp(fpath)
        ent = files.get(rel)
        if ent is None or ent.get("stamp") != list(stamp or []):
            try:
                if os.path.getsize(fpath) > 500_000:
                    continue
                rel2, entry = _resolve_file_entry(root_dir, fpath)
                files[rel2] = entry
                syms = _scan_file_symbols(fpath)
                if syms:
                    symbols[rel2] = syms[:80]
                else:
                    symbols.pop(rel2, None)
                changed = True
            except OSError:
                continue
    if changed:
        _drop_ref_cache(root_dir)
        _save_graph_cache(root_dir, files, symbols)
    return files, symbols, changed


def get_file_graph(root_dir, force=False):
    """Ambil dependency graph proyek (cache disk → selaraskan mtime bila ada
    → build bila tak ada/force).

    Cache membuat analyze_deps/impact_analysis cepat di repo besar; refresh
    inkremental via refresh_file_graph() menjaga kesegaran tanpa scan ulang;
    edit di luar Joki ditangkap validasi stamp saat load.
    """
    root_dir = os.path.abspath(root_dir)
    if not force:
        files, symbols = _load_graph_cache(root_dir)
        if files is not None:
            files, _symbols, _changed = _sync_graph_cache(root_dir, files, symbols or {})
            return files
    files_by_ext = _find_local_files(root_dir)
    file_graph = {}
    for ext_files in files_by_ext.values():
        for fpath in ext_files:
            rel, entry = _resolve_file_entry(root_dir, fpath)
            file_graph[rel] = entry
    _save_graph_cache(root_dir, file_graph, _build_symbols(files_by_ext, root_dir))
    return file_graph


def get_file_symbols(root_dir, force=False):
    """Ambil indeks simbol per file {rel: [{name, line}]} (cache disk).

    Lapisan C indeks semantik: definisi per file. Referensi antar-file
    dihitung on-demand via find_symbol_references (butuh LSP)."""
    root_dir = os.path.abspath(root_dir)
    if not force:
        _files, symbols = _load_graph_cache(root_dir)
        if symbols is not None:
            if _files is not None:
                _files, symbols, _changed = _sync_graph_cache(root_dir, _files, symbols)
            return symbols
    files_by_ext = _find_local_files(root_dir)
    symbols = _build_symbols(files_by_ext, root_dir)
    try:
        files, _old = _load_graph_cache(root_dir)
    except Exception:  # noqa: BLE001
        files = None
    if files is None:
        files = get_file_graph(root_dir, force=True)
    _save_graph_cache(root_dir, files, symbols)
    return symbols


def refresh_file_graph(root_dir, changed_paths):
    """Perbarui entri graph + simbol untuk file berubah/tambah/hapus.

    Best-effort: gagal baca file → entri dihapus; selalu kembalikan graph
    (cache bila refresh gagal total). Referensi simbol yang di-memo untuk
    root ini ikut dibuang agar tak basi.
    """
    root_dir = os.path.abspath(root_dir)
    try:
        graph = get_file_graph(root_dir)
        _files, symbols = _load_graph_cache(root_dir)
    except Exception:  # noqa: BLE001
        return {}
    if symbols is None:
        symbols = {}
    changed = False
    for p in changed_paths or []:
        try:
            rel = os.path.relpath(os.path.abspath(p), root_dir)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        full = os.path.join(root_dir, rel)
        if os.path.isfile(full):
            try:
                if os.path.getsize(full) > 500_000:
                    continue
                rel2, entry = _resolve_file_entry(root_dir, full)
                graph[rel2] = entry
                syms = _scan_file_symbols(full)
                if syms:
                    symbols[rel2] = syms[:80]
                else:
                    symbols.pop(rel2, None)
                changed = True
            except OSError:
                pass
        else:
            if rel in graph:
                graph.pop(rel, None)
                for info in graph.values():
                    if rel in info.get("resolved", []):
                        info["resolved"] = [r for r in info["resolved"] if r != rel]
                changed = True
            if rel in symbols:
                symbols.pop(rel, None)
                changed = True
    _drop_ref_cache(root_dir)
    if changed:
        _save_graph_cache(root_dir, graph, symbols)
    return graph


_REF_CACHE = {}


def _drop_ref_cache(root_dir):
    """Buang memo referensi simbol untuk root (dipanggil saat ada perubahan)."""
    prefix = os.path.abspath(root_dir)
    for key in [k for k in _REF_CACHE if k[0] == prefix]:
        _REF_CACHE.pop(key, None)


def _file_stamp(path):
    """Stempel murah (mtime+size) untuk validitas memo. None bila tak ada."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


_REF_LOC_RE = re.compile(r"^(.+?):(\d+):(\d+)\s*$")


def _ensure_lsp_indexed(root_dir, ext):
    """Preopen file se-ekstensi agar findReferences melihat lintas file.

    Tanpa ini referensi antar-file tak ditemukan (server hanya tahu file
    yang dibuka). Idempotent dan murah bila sudah terbuka. Return bool."""
    try:
        from joki.tools import lsp as _lsp_mod
        available = _lsp_mod._get_available_servers()
        if _lsp_mod._ext_to_lang(ext, available) is None:
            return False
        lang = _lsp_mod._ext_to_lang(ext, available)
        client = _lsp_mod._get_lsp_client(lang, root_dir)
        if not client:
            return False
        _lsp_mod._preopen_project_files(client, root_dir, ext)
        time.sleep(3)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_symbol_references(root_dir, file_rel_or_path, symbol):
    """Cari lokasi referensi simbol via LSP findReferences (memo per hash file).

    Return list [(rel, line, col)] 1-indexed, sorted. Kosong bila server tak
    ada / simbol tak dikenal / error. Tak pernah raise.
    """
    root_dir = os.path.abspath(root_dir)
    full = file_rel_or_path if os.path.isabs(file_rel_or_path) else os.path.join(root_dir, file_rel_or_path)
    if not os.path.isfile(full) or not symbol:
        return []
    stamp = _file_stamp(full)
    key = (root_dir, os.path.relpath(full, root_dir), symbol)
    if key in _REF_CACHE and _REF_CACHE[key][0] == stamp:
        return _REF_CACHE[key][1]
    locs = []
    try:
        _ensure_lsp_indexed(root_dir, os.path.splitext(full)[1].lower())
        from joki.tools.lsp import handle_lsp_query
        out = handle_lsp_query({"operation": "findReferences", "file_path": full, "symbol": symbol})
        for line in (out or "").splitlines():
            m = _REF_LOC_RE.match(line.strip())
            if not m:
                continue
            fpath, ln, col = m.group(1), int(m.group(2)), int(m.group(3))
            try:
                rel = os.path.relpath(fpath, root_dir)
            except ValueError:
                continue
            if rel.startswith(".."):
                continue
            locs.append((rel, ln, col))
        locs = sorted(set(locs))
    except Exception:  # noqa: BLE001, S110
        pass
    _REF_CACHE[key] = (stamp, locs)
    return locs


def symbol_impact(root_dir, file_rel_or_path, symbol, max_refs=15):
    """Seksi impact level simbol: definisi + titik referensi.

    Return string (kosong bila simbol tak dikenal di indeks)."""
    root_dir = os.path.abspath(root_dir)
    full = file_rel_or_path if os.path.isabs(file_rel_or_path) else os.path.join(root_dir, file_rel_or_path)
    try:
        rel = os.path.relpath(full, root_dir)
    except ValueError:
        return ""
    if not symbol:
        return ""
    try:
        symbols = get_file_symbols(root_dir)
    except Exception:  # noqa: BLE001
        return ""
    definers = sorted(f for f, syms in symbols.items()
                      if any(s.get("name") == symbol for s in syms))
    lines = [f"Simbol '{symbol}':"]
    if rel in definers:
        ln = next((s.get("line", 0) for s in symbols.get(rel, []) if s.get("name") == symbol), 0)
        lines.append(f"  Didefinisikan di {rel}:{ln}")
    elif definers:
        lines.append(f"  Didefinisikan di: {', '.join(definers[:5])}")
    else:
        lines.append("  Definisi tak ditemukan di indeks (mungkin dinamis/eksternal).")
    refs = find_symbol_references(root_dir, rel, symbol)
    if refs:
        lines.append(f"  Referensi ({len(refs)} titik):")
        for r, ln, col in refs[:max_refs]:
            lines.append(f"    → {r}:{ln}:{col}")
        if len(refs) > max_refs:
            lines.append(f"    ... dan {len(refs) - max_refs} lainnya")
    else:
        lines.append("  Referensi: (tidak ditemukan via LSP — cek manual dengan search_code)")
    return "\n".join(lines)


def _compute_dependants(file_graph, target_rel, transitive=False):
    """Return (direct, indirect) dependants dari target_rel.

    direct   = file yang langsung mengimport target.
    indirect = file yang mengimport (secara transitif) file yang mengimport target.
    """
    direct = sorted([f for f, info in file_graph.items() if target_rel in info["resolved"]])
    if not transitive:
        return direct, []
    affected = set()
    visited = set(direct)
    queue = list(direct)
    while queue:
        current = queue.pop(0)
        if current in affected:
            continue
        affected.add(current)
        for f, info in file_graph.items():
            if f not in visited and current in info["resolved"]:
                visited.add(f)
                queue.append(f)
    affected.discard(target_rel)
    indirect = sorted(affected - set(direct))
    return direct, indirect


def handle_analyze_deps(args):
    path = args.get("path", _WORKSPACE_ROOT)
    if not os.path.exists(path):
        return f"Error: Path tidak ditemukan: {path}"

    target_file = ""
    if os.path.isfile(path):
        target_file = os.path.abspath(path)
        root_dir = _find_project_root_deps(path)
    else:
        root_dir = os.path.abspath(path)

    file_graph = get_file_graph(root_dir, force=bool(args.get("refresh", False)))
    if not file_graph:
        return "Tidak ada file yang bisa dianalisis."

    if target_file:
        target_rel = os.path.relpath(target_file, root_dir)
        if target_rel not in file_graph:
            return f"File {target_rel} tidak memiliki dependency atau tidak ditemukan."
        target_info = file_graph[target_rel]
        direct, indirect = _compute_dependants(file_graph, target_rel, transitive=True)
        lines = [f"Dependency Analysis: {target_rel}"]
        if target_info["imports"]:
            lines.append(f"\n  Imports ({len(target_info['imports'])}):")
            for imp in target_info["imports"]:
                found = " [local]" if any(target_rel in r for r in target_info["resolved"]) else " [external]"
                lines.append(f"    ← {imp}{found}")
        else:
            lines.append("\n  Imports: (none)")
        if direct:
            lines.append(f"\n  Digunakan oleh ({len(direct)} file langsung):")
            for dep in direct:
                lines.append(f"    → {dep}")
        else:
            lines.append("\n  Digunakan oleh: (none — mungkin unused)")
        if indirect:
            lines.append(f"\n  Terpengaruh transitif ({len(indirect)} file):")
            for dep in indirect[:10]:
                lines.append(f"    → {dep}")
            if len(indirect) > 10:
                lines.append(f"    ... dan {len(indirect)-10} lainnya")

        lines.append(f"\n  Impact jika diubah: {len(direct) + len(indirect)} file lain perlu dicek.")
        return "\n".join(lines)

    total_files = len(file_graph)
    circular = _find_circular_deps(file_graph)
    orphan_files = [f for f, info in file_graph.items() if not info["resolved"] and not any(
        f in i["resolved"] for i in file_graph.values())]

    stats = [
        f"Dependency Analysis: {root_dir}",
        f"  Total file: {total_files}",
        f"  File dengan imports: {sum(1 for v in file_graph.values() if v['imports'])}",
        f"  File tanpa imports: {sum(1 for v in file_graph.values() if not v['imports'])}",
        f"  Circular dependencies: {len(circular)}",
        f"  Orphan files (tidak diimport siapapun): {len(orphan_files)}",
    ]
    if circular:
        stats.append("\n  Circular dependencies:")
        for cycle in circular[:5]:
            stats.append(f"    {' → '.join(cycle)} → {cycle[0]}")
    if orphan_files:
        stats.append("\n  Orphan files:")
        for f in sorted(orphan_files)[:10]:
            stats.append(f"    {f}")
    return "\n".join(stats)


def handle_impact_analysis(args):
    path = args.get("path", "")
    if not path:
        return "Error: Parameter 'path' wajib diisi."
    if not os.path.isfile(path):
        return f"Error: File tidak ditemukan: {path}"

    path = os.path.abspath(path)
    root_dir = _find_project_root_deps(path)
    rel = os.path.relpath(path, root_dir)

    file_graph = get_file_graph(root_dir, force=bool(args.get("refresh", False)))
    if rel not in file_graph:
        return f"File {rel} tidak ditemukan di dependency graph (mungkin dalam direktori yang di-exclude atau >500KB)."

    direct, indirect = _compute_dependants(file_graph, rel, transitive=True)

    lines = [
        f"Impact Analysis: {rel}",
        f"  Root: {root_dir}",
        "",
    ]

    if direct:
        lines.append(f"  ⚡ LANGSUNG: {len(direct)} file mengimport file ini:")
        for d in direct:
            lines.append(f"     → {d}")
    else:
        lines.append("  Tidak ada file lain yang mengimport file ini secara langsung.")

    if indirect:
        lines.append(f"  ⚡ TRANSITIVE: {len(indirect)} file terpengaruh secara tidak langsung")
        for t in indirect[:10]:
            lines.append(f"     → {t}")
        if len(indirect) > 10:
            lines.append(f"     ... dan {len(indirect)-10} lainnya")

    lines.append("")
    lines.append(f"  Total file perlu dicek ulang: {len(direct) + len(indirect)}")
    symbol = (args.get("symbol") or "").strip()
    if symbol:
        lines.append("")
        lines.append(symbol_impact(root_dir, rel, symbol))
    return "\n".join(lines)


def _find_circular_deps(graph):
    cycles = []
    visited = set()
    path = []

    def dfs(node):
        if node in path:
            cycle = path[path.index(node):]
            if len(cycle) >= 2 and len(set(cycle)) == len(cycle):
                cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        for dep in graph.get(node, {}).get("resolved", []):
            if dep in graph:
                dfs(dep)
        path.pop()

    for node in graph:
        dfs(node)

    # Kanonikkan: rotasi setiap cycle agar mulai dari node terkecil, lalu dedup.
    canonical = set()
    for cycle in cycles:
        if not cycle:
            continue
        min_idx = min(range(len(cycle)), key=lambda i: cycle[i])
        canonical.add(tuple(cycle[min_idx:] + cycle[:min_idx]))
    return sorted(canonical)


def _find_project_root_deps(file_path):
    path = os.path.abspath(file_path)
    current = os.path.dirname(path)
    while current and current != "/":
        if _is_project_root(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.dirname(path)
