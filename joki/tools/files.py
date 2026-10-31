import ast
import glob as _glob_mod
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from difflib import unified_diff

from joki.display import _numbered
from joki.state import *
from joki.utils import *

_BACKUP_MANIFEST = {}
_manifest_path = os.path.join(BACKUP_DIR, "manifest.json")
try:
    with open(_manifest_path) as _mf:
        _BACKUP_MANIFEST = json.load(_mf)
except Exception:  # noqa: BLE001
    _BACKUP_MANIFEST = {}

def _save_manifest():
    """Atomic write manifest: write ke temp, lalu rename."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', dir=BACKUP_DIR, delete=False, suffix='.tmp'
        ) as tmp:
            json.dump(_BACKUP_MANIFEST, tmp)
        os.replace(tmp.name, _manifest_path)
    except Exception:  # noqa: BLE001, S110
        pass

_BACKUP_MAX_AGE_DAYS = 7

def _file_signature(path):
    """Hash konten file (None bila tidak bisa dibaca)."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None

def _mark_file_changed(abs_path):
    """Tandai file berubah: invalidasi cache & baca-konteks, catat untuk notifikasi."""
    _file_cache.pop(abs_path, None)
    _read_in_context.pop(abs_path, None)
    _file_change_log.add(abs_path)

def _head_tail(text, head_chars, tail_chars):
    """Potong teks panjang jadi head+tail (ekor sering berisi kesimpulan/
    error). Duplikat kecil dari cli agar tak import cycle."""
    text = str(text or "")
    if len(text) <= head_chars + tail_chars:
        return text
    hidden = len(text) - head_chars - tail_chars
    return (text[:head_chars]
            + f"\n... [DIPOTONG: {hidden} karakter tengah disembunyikan — head+tail ditampilkan] ...\n"
            + text[len(text) - tail_chars:])


def _cache_file_metadata(abs_path, signature):
    try:
        _file_cache[abs_path] = {
            "hash": signature,
            "mtime": os.path.getmtime(abs_path),
        }
    except OSError:
        _file_cache[abs_path] = {"hash": signature, "mtime": 0}

def _cleanup_old_backups():
    """Hapus file backup di BACKUP_DIR yang lebih tua dari 7 hari."""
    try:
        if not os.path.isdir(BACKUP_DIR):
            return
        cutoff = time.time() - _BACKUP_MAX_AGE_DAYS * 86400
        removed = False
        for name in os.listdir(BACKUP_DIR):
            if not name.endswith(".bak"):
                continue
            path = os.path.join(BACKUP_DIR, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    _BACKUP_MANIFEST.pop(name, None)
                    removed = True
            except OSError:
                pass
        if removed:
            _save_manifest()
    except Exception:  # noqa: BLE001, S110
        pass

_PAGE_SIZE = 100

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".scala",
    ".c", ".h", ".cpp", ".hpp", ".cxx", ".cc", ".cs", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".mm", ".dart", ".lua",
    ".sh", ".bash", ".zsh", ".pl", ".pm", ".r", ".jl",
    ".sql", ".css", ".scss", ".less", ".sass", ".vue", ".svelte",
    ".zig", ".nim", "ex", ".exs",
    ".html", ".htm", ".xhtml", ".xml", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
}

_CODE_PATTERNS = re.compile(
    r'^\s*(def |function |func |sub |class |trait |impl |interface |enum |struct |module |public |private |protected |'
    r'export |const .*=>|const .*\(|let .*\(|var .*\(|async |'
    r'defn |defrecord |defprotocol |defmulti |defmethod |'
    r'--\[\[|function\s+\w+\s*\(|'
    r'\w+\s*=\s*(def|fn)\s|'
    r'^\s*import\s|^\s*from\s|^\s*package\s|^\s*namespace\s|'
    r'^\s*#\s*include|^\s*using\s|^\s*require\s)',
    re.MULTILINE
)

_INDENT_RE = re.compile(r'^(\s*)\S')


def _is_code_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _CODE_EXTENSIONS:
        return True
    try:
        with open(path, errors="ignore") as f:
            head = f.read(4096)
        return bool(_CODE_PATTERNS.search(head)) if head else False
    except Exception:  # noqa: BLE001
        return False


def _find_logical_chunks(lines, min_size=30, max_size=200):
    chunks = []
    start = 0
    prev_indent = 0

    for i, line in enumerate(lines):
        indent_match = _INDENT_RE.match(line)
        indent = len(indent_match.group(1)) if indent_match else 0

        is_def = bool(_CODE_PATTERNS.match(line))
        is_blank = not line.strip()

        if i - start >= max_size:
            chunks.append((start, i))
            start = i
            prev_indent = indent
            continue

        if is_def and i - start >= min_size and indent <= prev_indent:
            chunks.append((start, i))
            start = i

        if not is_blank:
            prev_indent = indent

    if start < len(lines):
        chunks.append((start, len(lines)))

    return chunks


def handle_read_file(args):
    path = args.get("path") or args.get("file_path", "")
    force_full = args.get("force_full", False)

    if not os.path.isfile(path):
        return f"Error: File tidak ditemukan: {path}. Cek path dengan list_dir/glob atau buat file dulu dengan write_file."

    abs_path = os.path.abspath(path)
    _READ_FILES.add(abs_path)
    sig = _file_signature(path)
    if sig:
        _cache_file_metadata(abs_path, sig)
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    total = len(lines)

    offset = args.get("offset", None)
    limit = args.get("limit", None)

    if offset is not None and limit is not None:
        offset = max(offset, 1)
        end = offset - 1 + limit
        selected = lines[offset - 1 : end]
        return _numbered("".join(selected))

    is_code = _is_code_file(path)

    if offset is not None:
        if is_code:
            chunks = _find_logical_chunks(lines)
            for cstart, cend in chunks:
                chunk_start_1 = cstart + 1
                chunk_end_1 = cend
                if chunk_start_1 <= offset <= chunk_end_1:
                    selected = lines[cstart:cend]
                    result = _numbered("".join(selected))
                    if cend < total:
                        result += f"\n--- Fungsi/blok selanjutnya dimulai di baris {cend+1} — gunakan offset={cend+1} untuk lanjut ---"
                    else:
                        result += "\n--- SELESAI ---"
                    return result
        limit = _PAGE_SIZE
        end = offset - 1 + limit
        selected = lines[offset - 1 : end]
        total_pages = math.ceil(total / _PAGE_SIZE)
        current_page = (offset - 1) // _PAGE_SIZE + 1
        result = _numbered("".join(selected))
        next_offset = offset + len(selected) if end < total else None
        if next_offset:
            result += f"\n--- Halaman {current_page}/{total_pages} ({len(selected)} baris) — gunakan offset={next_offset} untuk lanjut ---"
        else:
            result += f"\n--- Halaman {current_page}/{total_pages} ({len(selected)} baris) — SELESAI ---"
        return result

    if force_full:
        return "*" + _numbered("".join(lines))

    if is_code:
        chunks = _find_logical_chunks(lines)
        if len(chunks) <= 1:
            return _numbered("".join(lines))
        chunk_start, chunk_end = chunks[0]
        selected = lines[chunk_start:chunk_end]
        result = _numbered("".join(selected))
        total_chunks = len(chunks)
        result += f"\n--- Blok 1/{total_chunks} ({chunk_end - chunk_start} baris dari {total}) — gunakan offset={chunk_end + 1} untuk lanjut ---"
        return result

    if total > _PAGE_SIZE:
        total_pages = math.ceil(total / _PAGE_SIZE)
        selected = lines[:_PAGE_SIZE]
        result = _numbered("".join(selected))
        result += f"\n--- Halaman 1/{total_pages} ({_PAGE_SIZE} baris dari {total}) — gunakan offset={_PAGE_SIZE+1} untuk lanjut ---"
        return result

    return _numbered("".join(lines))


def handle_write_file(args):
    path = args.get("path", "")
    new = args.get("content", "")
    if _is_protected_path(path):
        return (f"Error: {path} adalah file sistem yang dilindungi. "
                f"Gunakan config_edit untuk mengubah file konfigurasi "
                f"(masih bisa untuk config service non-identitas), "
                f"atau konfirmasi ke user apakah yakin ingin mengubah file sistem.")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    diff_str = ""
    backup_path = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            old = f.read()
        # Samakan dengan edit_file: backup sebelum overwrite agar bisa undo.
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            backup_name = hashlib.md5(os.path.abspath(path).encode()).hexdigest() + ".bak"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(path, backup_path)
            _BACKUP_MANIFEST[backup_name] = os.path.abspath(path)
            _save_manifest()
        except OSError:
            backup_path = ""
        if old != new:
            diff = unified_diff(
                old.splitlines(
                    keepends=True), new.splitlines(
                    keepends=True), fromfile=path, tofile=path)
            diff_str = "".join(diff)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(new)
    # Validasi sintaks ringan (fail-open): beri warning, jangan rollback total
    # agar write file baru non-kode tidak terblokir.
    try:
        _syn_err = _validate_syntax(path, new)
    except Exception:
        _syn_err = None
    _mark_file_changed(os.path.abspath(path))
    msg = f"Written: {path} ({len(new)} bytes)"
    if backup_path:
        msg += f" (backup: {backup_path})"
    if diff_str:
        msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
    if _syn_err:
        msg += f"\n[WARNING SINTAKS] {_syn_err[:300]}"
    return msg


def _normalize_ws(s):
    return re.sub(r'\s+', ' ', s).strip()

def _strip_all_ws(s):
    return re.sub(r'\s+', '', s)

def _match_sequences(old_lines, norm_lines, norm_ot_lines):
    matches = []
    for i in range(len(old_lines) - len(norm_ot_lines) + 1):
        if all(norm_lines[i + j] == norm_ot_lines[j] for j in range(len(norm_ot_lines))):
            matches.append(i)
    if len(matches) == 1:
        start = matches[0]
        end = start + len(norm_ot_lines)
        return '\n'.join(old_lines[start:end]), 1
    if len(matches) > 1:
        return None, len(matches)
    return None, 0

def _fuzzy_match(ot, old):
    old_lines = old.split('\n')
    norm_lines = [_normalize_ws(l) for l in old_lines]
    flat_lines = [_strip_all_ws(l) for l in old_lines]
    ot_lines = ot.split('\n')
    norm_ot_lines = [_normalize_ws(l) for l in ot_lines]
    flat_ot_lines = [_strip_all_ws(l) for l in ot_lines]

    # Filter trailing/leading empty lines in OT (common LLM mistake)
    while norm_ot_lines and not norm_ot_lines[-1]:
        norm_ot_lines.pop()
        ot_lines.pop()
        flat_ot_lines.pop()
    while norm_ot_lines and not norm_ot_lines[0]:
        norm_ot_lines.pop(0)
        ot_lines.pop(0)
        flat_ot_lines.pop(0)

    if not norm_ot_lines:
        return None, 0

    result = None
    is_multi = len(norm_ot_lines) > 1

    if is_multi:
        result = _match_sequences(old_lines, norm_lines, norm_ot_lines)
        if result[1] == 0:
            result = _match_sequences(old_lines, flat_lines, flat_ot_lines)
    else:
        indices = [i for i, nl in enumerate(norm_lines) if nl == norm_ot_lines[0]]
        if len(indices) == 1:
            result = old_lines[indices[0]], 1
        elif len(indices) == 0:
            indices2 = [i for i, fl in enumerate(flat_lines) if fl == flat_ot_lines[0]]
            if len(indices2) == 1:
                result = old_lines[indices2[0]], 1
            elif len(indices2) > 1:
                result = None, len(indices2)
        else:
            result = None, len(indices)

    return result if result else (None, 0)

def _run_syntax_cmd(cmd):
    """Jalankan validator sintaks eksternal.

    Return None bila OK, toolchain tak ada, atau timeout (skip aman —
    jangan rollback atas dasar validator yang tidak bisa jalan).
    Return pesan error hanya bila validator jalan dan exit != 0.
    """
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode == 0:
        return None
    err = (r.stderr or r.stdout or "").strip().splitlines()
    detail = err[0][:300] if err else f"exit {r.returncode}"
    return f"{cmd[0]} error: {detail}"


def _run_capture(cmd, cwd=None):
    """Jalankan command, kembalikan (returncode, output) atau (None, '') bila
    toolchain tak ada / timeout (skip aman)."""
    if not shutil.which(cmd[0]):
        return None, ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return r.returncode, ((r.stderr or "") + "\n" + (r.stdout or ""))


_SUMMARY_LINE_RE = re.compile(r"^\d+\s+(error|warning)|^error:\s*aborting", re.IGNORECASE)


def _classify_diagnostics(output, skip_re):
    """Pisahkan error resolusi-dependensi (skip) dari error sintaks (fail).

    Pola ini perlu karena validator file-tunggal tidak punya konteks proyek:
    import valid pun dilaporkan hilang. Baris ringkasan ('1 error',
    'error: aborting ...') diabaikan — hanya diagnostik per lokasi dihitung.
    Return None bila tidak ada error atau semua error hanya soal resolusi;
    kembalikan baris error nyata pertama.
    """
    errs = [ln.strip() for ln in (output or "").splitlines()
            if "error" in ln.lower() and not _SUMMARY_LINE_RE.search(ln.strip())]
    real = [ln for ln in errs if not skip_re.search(ln)]
    if not real:
        return None
    return real[0][:300]


_JAVA_SKIP_RE = re.compile(r"does not exist|cannot find symbol|cannot access", re.IGNORECASE)
_JAVA_TYPE_RE = re.compile(
    r"public\s+(?:final\s+|abstract\s+|sealed\s+)*(?:class|interface|enum|record)\s+(\w+)"
)
_RUST_SKIP_RE = re.compile(r"E043[23]|E0463")
_TS_SKIP_RE = re.compile(r"TS2307|TS2792|TS2688|TS2305|TS7016")


def _validate_java(content):
    """Validasi sintaks Java via javac (file temp dinamai sesuai public class,
    karena javac mewajibkannya). Error resolusi-dependensi → skip."""
    m = _JAVA_TYPE_RE.search(content)
    fname = (m.group(1) if m else "TmpValidate") + ".java"
    tmpdir = ""
    try:
        tmpdir = tempfile.mkdtemp(prefix="joki_javac_")
        fp = os.path.join(tmpdir, fname)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        rc, out = _run_capture(["javac", "-proc:none", "-d", tmpdir, fp])
        if rc is None:
            return None
        if rc == 0:
            return None
        err = _classify_diagnostics(out, _JAVA_SKIP_RE)
        return f"javac error: {err}" if err else None
    except OSError as e:
        return f"tidak bisa validasi: {e}"
    finally:
        if tmpdir:
            import shutil as _shutil
            _shutil.rmtree(tmpdir, ignore_errors=True)


def _validate_rust(content):
    """Validasi sintaks Rust via rustc --emit=metadata. Error unresolved
    import/crate (E0432/E0433/E0463) → skip, bukan fail."""
    tmp = ""
    out_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".rs", delete=False) as tf:
            tf.write(content)
            tmp = tf.name
        out_path = tmp + ".rmeta"
        rc, out = _run_capture([
            "rustc", "--edition", "2021", "--crate-type", "lib",
            "--emit=metadata", "-o", out_path, tmp,
        ])
        if rc is None or rc == 0:
            return None
        err = _classify_diagnostics(out, _RUST_SKIP_RE)
        return f"rustc error: {err}" if err else None
    except OSError as e:
        return f"tidak bisa validasi: {e}"
    finally:
        for p in (tmp, out_path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


def _ts_command():
    """Kembalikan base command tsc bila tersedia (biner atau via npx proyek),
    atau None. npx --no-install gagal cepat bila typescript tak terinstall
    (tidak mengunduh apa pun)."""
    if shutil.which("tsc"):
        return ["tsc"]
    if shutil.which("npx"):
        return ["npx", "--no-install", "tsc"]
    return None


def _validate_ts(suffix, content):
    """Validasi sintaks TypeScript via tsc --noEmit. Error modul-tak-
    terresolve (TS2307/dll) → skip; error lain (mayoritas TS1xxx sintaks)
    → fail. Skip total bila toolchain tak ada."""
    base = _ts_command()
    if not base:
        return None
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as tf:
            tf.write(content)
            tmp = tf.name
        cmd = base + ["--noEmit", "--skipLibCheck", "--target", "esnext",
                      "--module", "esnext", tmp]
        if suffix == ".tsx":
            cmd[1:1] = ["--jsx", "preserve"]
        rc, out = _run_capture(cmd)
        if rc is None or rc == 0:
            return None
        low = (out or "").lower()
        # npx gagal (typescript tak terinstall di proyek / tak boleh unduh)
        # → bukan error sintaks, skip.
        if "could not determine executable" in low or "npm error" in low or "npx canceled" in low:
            return None
        err = _classify_diagnostics(out, _TS_SKIP_RE)
        return f"tsc error: {err}" if err else None
    except OSError as e:
        return f"tidak bisa validasi: {e}"
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _validate_with_temp(suffix, content, cmd_base):
    """Validasi string konten via tool eksternal lewat file temp bervolume sama."""
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as tf:
            tf.write(content)
            tmp = tf.name
        return _run_syntax_cmd(cmd_base + [tmp])
    except OSError as e:
        return f"tidak bisa validasi: {e}"
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _validate_syntax(path, content=None):
    """Validasi sintaks ringan pasca-edit. Return None bila OK atau tidak ada
    validator yang bisa jalan, pesan error bila sintaks rusak.

    Python/JSON/YAML/TOML: parser in-process. JS/PHP/Ruby/Go/Shell: toolchain
    sistem (diskip bila tak terinstall). Bahasa lain (TS/Java/C#/Rust/dll):
    butuh konteks proyek — diskip, serahkan ke lsp_query/run_linter.
    Caller wajib rollback bila return pesan error.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if content is None:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            text = content
    except OSError as e:
        return f"tidak bisa dibaca untuk validasi: {e}"
    try:
        if ext == ".py":
            ast.parse(text)
        elif ext == ".json":
            if text.strip():
                json.loads(text)
        elif ext in (".yaml", ".yml"):
            try:
                import yaml as _yaml
            except ImportError:
                return None
            try:
                _yaml.safe_load(text)
            except _yaml.YAMLError as e:
                return f"{ext} syntax error: {e}"
        elif ext == ".toml":
            try:
                import tomllib as _tomllib
            except ImportError:
                return None
            if text.strip():
                _tomllib.loads(text)
        elif ext in (".js", ".mjs", ".cjs"):
            return _validate_with_temp(ext, text, ["node", "--check"])
        elif ext == ".php":
            return _validate_with_temp(ext, text, ["php", "-l"])
        elif ext == ".rb":
            return _validate_with_temp(ext, text, ["ruby", "-c"])
        elif ext == ".go":
            return _validate_with_temp(ext, text, ["gofmt", "-e"])
        elif ext in (".sh", ".bash"):
            return _validate_with_temp(".sh", text, ["bash", "-n"])
        elif ext == ".java":
            return _validate_java(text)
        elif ext == ".rs":
            return _validate_rust(text)
        elif ext in (".ts", ".tsx"):
            return _validate_ts(ext, text)
        return None
    except (SyntaxError, ValueError) as e:
        return f"{ext or 'file'} syntax error: {e}"


def handle_edit_file(args):
    path = args.get("path", "")
    if not os.path.isfile(path):
        return f"Error: file tidak ditemukan: {path}. Cek path dengan list_dir/glob."

    abs_path = os.path.abspath(path)
    if abs_path not in _READ_FILES:
        return (f"Error: WAJIB baca file dulu sebelum edit! "
                f"Panggil read_file(path=\"{path}\") untuk melihat isi file, "
                f"lalu copy-paste old_text yang tepat dari output read_file.")
    if _is_protected_path(path):
        return (f"Error: {path} adalah file sistem yang dilindungi. "
                f"Gunakan config_edit untuk mengubah file konfigurasi "
                f"(masih bisa untuk config service non-identitas), "
                f"atau konfirmasi ke user apakah yakin ingin mengubah file sistem.")

    _cleanup_old_backups()
    with open(path) as f:
        old = f.read() or ""

    ot = args.get("old_text", "")
    nt = args.get("new_text", "")

    if not ot:
        return ("Error: 'old_text' tidak boleh kosong. "
                "Gunakan write_file untuk overwrite file, "
                "atau berikan old_text yang valid.")

    match_text = ot
    count = old.count(ot)
    fuzzy = False

    if count == 0:
        match_text, count = _fuzzy_match(ot, old)
        if count == 1:
            fuzzy = True

    if count == 0:
        return (f"Error: 'old_text' tidak ditemukan di {path}.\n"
                f"Gunakan read_file untuk melihat isi file, "
                f"lalu copy-paste teks yang tepat sebagai old_text.")
    if count > 1:
        return (f"Error: 'old_text' ditemukan {count} kali di {path}. "
                f"Sertakan lebih banyak baris konteks di old_text agar match-nya unik. "
                f"Hanya boleh ada 1 match yang tepat.")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_name = hashlib.md5(path.encode()).hexdigest() + ".bak"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(path, backup_path)

    _BACKUP_MANIFEST[backup_name] = path
    _save_manifest()

    final = old.replace(match_text, nt, 1)

    # === Validasi atomik: tolak hasil yang merusak sintaks, rollback otomatis ===
    syntax_err = _validate_syntax(path, final)
    if syntax_err:
        try:
            shutil.copy2(backup_path, path)
        except OSError:
            pass
        _mark_file_changed(os.path.abspath(path))
        return (f"Error: hasil edit merusak sintaks — perubahan DIBATALKAN (rollback ke backup).\n"
                f"{syntax_err}\nBackup: {backup_path}\n"
                f"Perbaiki old_text/new_text lalu coba lagi.")

    with open(path, "w", encoding='utf-8', errors='replace') as f:
        f.write(final)
    _mark_file_changed(os.path.abspath(path))

    diff = unified_diff(old.splitlines(keepends=True), final.splitlines(keepends=True), fromfile=path, tofile=path)
    diff_str = "".join(diff)

    label = "Fuzzy-matched" if fuzzy else "Edited"
    msg = f"{label}: {path} (backup: {backup_path})"
    if fuzzy:
        preview = match_text[:80].replace('\n', '\\n')
        msg += f"\nActual match: \"{preview}\""
    # Peringatan konteks tipis untuk file besar — tidak memblokir (backward-compat),
    # tapi mencegah edit nyasar saat refactor.
    try:
        total_lines = old.count("\n") + 1
        ot_lines = ot.strip().count("\n") + 1
        if total_lines > 200 and ot_lines < 3:
            msg += (f"\n[WARNING] old_text hanya {ot_lines} baris untuk file {total_lines} baris. "
                    f"Untuk refactor besar, gunakan min 3-5 baris konteks unik agar tidak salah match.")
    except Exception:  # noqa: BLE001, S110
        pass
    if diff_str:
        msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
    msg += "\n[SINTAKS OK] Validasi pasca-edit lolos."
    msg += _auto_verify_edited(path)
    return msg


_TEST_COMPANION_PATTERNS = {
    ".py": ["test_{base}.py", "{base}_test.py"],
    ".php": ["{base}Test.php", "test_{base}.php"],
    ".js": ["{base}.test.js", "{base}.spec.js"],
    ".ts": ["{base}.test.ts", "{base}.spec.ts"],
    ".jsx": ["{base}.test.jsx", "{base}.spec.jsx"],
    ".tsx": ["{base}.test.tsx", "{base}.spec.tsx"],
}

_TEST_SEARCH_DIRS = (".", "tests", "test", "__tests__", "../tests")


def _find_companion_test(path):
    """Cari SATU file test pendamping untuk path kode. Return abspath atau None.

    Hanya bila tepat satu kandidat (hindari ambiguitas). Mencakup pola umum
    pytest/PHPUnit/Jest di direktori file, tests/, test/, __tests__/, ../tests/.
    """
    ext = os.path.splitext(path)[1].lower()
    patterns = _TEST_COMPANION_PATTERNS.get(ext)
    if not patterns:
        return None
    base = os.path.splitext(os.path.basename(path))[0]
    if base.startswith(("test_", "test-")) or base.endswith(("_test", "-test", ".test", ".spec", "Test")):
        return None  # path-nya sendiri file test — jangan uji diri sendiri
    bases = {base}
    if base:
        bases.add(base[0].upper() + base[1:])  # FooTest.php ala PHPUnit
    file_dir = os.path.dirname(os.path.abspath(path))
    found = []
    for d in _TEST_SEARCH_DIRS:
        search_dir = os.path.normpath(os.path.join(file_dir, d))
        if not os.path.isdir(search_dir):
            continue
        for pat in patterns:
            for b in bases:
                cand = os.path.join(search_dir, pat.format(base=b))
                if os.path.isfile(cand):
                    found.append(os.path.abspath(cand))
    uniq = sorted(set(found))
    return uniq[0] if len(uniq) == 1 else None


def _run_companion_test(test_path):
    """Jalankan SATU file test pendamping (bounded). Return string hasil atau None
    bila tak ada test/runner yang cocok. Tidak pernah raise."""
    ext = os.path.splitext(test_path)[1].lower()
    workdir = os.path.dirname(test_path)
    cmd = None
    if ext == ".py":
        import sys as _sys
        cmd = [_sys.executable, "-m", "pytest", test_path, "-x", "-q"]
    elif ext == ".php":
        import shutil as _shutil
        if not _shutil.which("phpunit"):
            return None
        cmd = ["phpunit", "--no-coverage", test_path]
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        import shutil as _shutil
        if not _shutil.which("npx"):
            return None
        root = workdir
        for _ in range(4):
            if any(os.path.exists(os.path.join(root, m)) for m in
                   ("jest.config.js", "jest.config.ts", "jest.config.json", "vitest.config.js", "vitest.config.ts")):
                break
            parent = os.path.dirname(root)
            if parent == root:
                root = ""
                break
            root = parent
        else:
            root = ""
        if not root:
            return None  # tanpa config test, jangan panggil npx (cegah unduhan jaringan)
        cmd = ["npx", "--no-install", "jest", test_path, "--no-coverage", "--runInBand"]
        workdir = root
    if not cmd:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False, cwd=workdir)
    except (OSError, subprocess.TimeoutExpired):
        return f"[VERIFY test] TIMEOUT/tidak bisa jalan untuk {os.path.basename(test_path)}"
    output = ((r.stdout or "") + (r.stderr or "")).strip()
    status = "LOLOS" if r.returncode == 0 else f"GAGAL (exit {r.returncode})"
    return f"[VERIFY test] {os.path.basename(test_path)}: {status}\n{_head_tail(output, 1800, 1200)}"


def _auto_verify_edited(path):
    """Verifikasi otomatis pasca-edit: linter file + test pendamping.

    Best-effort dan bounded — tidak pernah raise, tidak pernah menggagalkan
    edit yang sudah sukses. Return string (kosong bila tak ada yang bisa jalan).
    """
    parts = []
    try:
        from joki.tools.lint import _get_linter_for_file, _run_linter
        lang, cfg = _get_linter_for_file(path)
        if cfg:
            lint_out = _run_linter(lang, cfg, [path], False)
            parts.append(f"[VERIFY lint]\n{_head_tail(lint_out, 1200, 800)}")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        companion = _find_companion_test(path)
        if companion:
            test_out = _run_companion_test(companion)
            if test_out:
                parts.append(test_out)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        lsp_section = _lsp_verify_edited(path)
        if lsp_section:
            parts.append(lsp_section)
    except Exception:  # noqa: BLE001, S110
        pass
    if not parts:
        return ""
    return "\n" + "\n".join(parts)


# Bahasa yang layak diverifikasi via diagnostik LSP: yang validator
# file-tunggalnya tidak menjangkau (TS/JSX tanpa tsc, C/C++ tanpa
# compile_commands) atau yang butuh konteks proyek. Bahasa yang sudah
# divalidasi cepat (py/json/js/php-sh...) TIDAK masuk sini agar tidak
# menambah latensi detik per edit tanpa nilai tambah.
_LSP_VERIFY_LANGS = {"typescript", "c_cpp", "php"}

_LSP_SKIP_PATTERNS = {
    # TS: modul tak ter-resolve (proyek tak dikenal server) → skip.
    # 'Cannot find name' SENGAJA tidak di-skip: itu sinyal typo riil.
    "typescript": re.compile(r"Cannot find module|Could not find a declaration file|Cannot find type definition file"),
    # clangd: header tak ditemukan → skip; sisanya (termasuk undeclared
    # identifier — sinyal miss-callerRename) → fail.
    "c_cpp": re.compile(r"file not found|command line option"),
    # intelephense: tipe/fungsi/konstanta lintas file → skip; variabel/
    # method/property (biasanya lokal) → fail.
    "php": re.compile(r"Undefined (type|function|constant)\b"),
}


def _classify_lsp_diags(diags, lang):
    """Filter diagnostik LSP severity Error: buang yang soal resolusi.

    Return list pesan error nyata (dibatasi 5, ≤150 char). Kosong = bersih
    atau hanya masalah resolusi yang wajar tanpa konteks proyek.
    """
    skip_re = _LSP_SKIP_PATTERNS.get(lang)
    out = []
    for d in diags or []:
        if not isinstance(d, dict):
            continue
        if d.get("severity", 1) != 1:
            continue
        msg = (d.get("message") or "").split("\n")[0][:150]
        if not msg:
            continue
        if skip_re and skip_re.search(msg):
            continue
        rng = d.get("range") or {}
        start = rng.get("start") or {}
        out.append(f"baris {start.get('line', 0) + 1}: {msg}")
        if len(out) >= 5:
            break
    return out


def _lsp_verify_edited(path, timeout=10):
    """Minta diagnostik LSP untuk file yang baru diubah. Return section
    string atau ''. Advisory (tidak me-rollback): LSP lambat (detik) dan
    bergantung konteks proyek — cocok sebagai sinyal, bukan vonis."""
    try:
        from joki.tools import lsp as _lsp_mod
        available = _lsp_mod._get_available_servers()
        lang = _lsp_mod._ext_to_lang(os.path.splitext(path)[1].lower(), available)
    except Exception:  # noqa: BLE001
        return ""
    if not lang or lang not in _LSP_VERIFY_LANGS:
        return ""
    try:
        diags = _lsp_mod.get_file_diagnostics_wait(path, timeout=timeout)
    except Exception:  # noqa: BLE001
        return ""
    if diags is None:
        return ""
    errors = _classify_lsp_diags(diags, lang)
    if not errors:
        return f"[VERIFY lsp:{lang}] bersih (0 error)."
    return f"[VERIFY lsp:{lang}] {len(errors)} error:\n" + "\n".join(f"  - {e}" for e in errors)


def handle_undo_edit(args):
    backup_path = args.get("backup_path", "")
    original_path = args.get("path", "")
    if not backup_path or not os.path.exists(backup_path):
        return "Error: backup file tidak ditemukan. Path: " + backup_path
    if not original_path:
        backup_name = os.path.basename(backup_path)
        original_path = _BACKUP_MANIFEST.get(backup_name, "")
        if not original_path:
            return "Error: path asli tidak diketahui. Sertakan parameter 'path'."
    if original_path and os.path.exists(backup_path) and os.path.exists(original_path):
        shutil.copy2(backup_path, original_path)
        os.remove(backup_path)
        _BACKUP_MANIFEST.pop(os.path.basename(backup_path), None)
        _save_manifest()
        _mark_file_changed(os.path.abspath(original_path))
        return f"Undo berhasil: {original_path} dikembalikan ke backup."
    return f"Error: Tidak bisa undo. Backup ada di {backup_path}"


_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def handle_rename_symbol(args):
    """Rename simbol lintas file secara aman: impact check + LSP rename.

    Fallback aman: bila LSP tidak tersedia, kembalikan impact + panduan
    search/edit manual — TIDAK auto-edit semua file agar tidak merusak kode.
    """
    file_path = args.get("file_path") or args.get("path", "")
    old_name = args.get("old_name") or args.get("symbol", "")
    new_name = args.get("new_name") or args.get("newName", "")
    line = args.get("line")
    character = args.get("character")

    if not file_path or not new_name:
        return "Error: 'file_path' dan 'new_name' wajib diisi."
    if not old_name:
        return "Error: 'old_name' (atau 'symbol') wajib diisi."
    if not os.path.isfile(file_path):
        return f"Error: file tidak ditemukan: {file_path}."
    if not _IDENT_RE.match(old_name) or not _IDENT_RE.match(new_name):
        return f"Error: nama simbol tidak valid: '{old_name}' -> '{new_name}'. Harus identifier."
    if old_name == new_name:
        return "Error: old_name dan new_name sama — tidak ada yang di-rename."
    if _is_protected_path(file_path):
        return f"Error: {file_path} adalah file sistem yang dilindungi."
    # Fail-fast: pastikan simbol memang ada di file (whole-word) sebelum
    # memanggil LSP — lebih jelas daripada "rename tidak menghasilkan perubahan".
    try:
        with open(file_path, encoding="utf-8", errors="replace") as _f:
            _content = _f.read()
    except OSError:
        return f"Error: tidak bisa membaca file: {file_path}."
    if not re.search(r"\b" + re.escape(old_name) + r"\b", _content):
        return (f"Error: simbol '{old_name}' tidak ditemukan di {file_path}. "
                f"Cek ejaan dengan lsp_query(documentSymbol) atau search_code.")

    # 1. Impact check (best-effort, jangan gagalkan rename bila deps error)
    impact_text = ""
    try:
        from joki.tools.deps import handle_impact_analysis as _impact
        impact_text = _impact({"path": os.path.abspath(file_path)})
    except Exception as e:  # noqa: BLE001
        impact_text = f"(impact check skip: {e})"

    # 2. Delegasi ke LSP rename
    try:
        from joki.tools.lsp import handle_lsp_query as _lsp
        lsp_args = {
            "operation": "rename",
            "file_path": os.path.abspath(file_path),
            "symbol": old_name,
            "newName": new_name,
        }
        if isinstance(line, int):
            lsp_args["line"] = line
        if isinstance(character, int):
            lsp_args["character"] = character
        lsp_result = _lsp(lsp_args)
    except Exception as e:  # noqa: BLE001
        lsp_result = f"Error: LSP rename gagal: {e}"

    _mark_file_changed(os.path.abspath(file_path))

    low = (lsp_result or "").lower()
    lsp_unavailable = (
        not lsp_result
        or low.startswith("error")
        or "tidak ada lsp" in low
        or "tidak tersedia" in low
        or "tidak didukung" in low
        or "tidak ditemukan" in low
    )
    if lsp_unavailable:
        return (
            f"[RENAME DITUNDA — LSP tidak tersedia]\n"
            f"{impact_text}\n\n"
            f"LSP result: {lsp_result}\n\n"
            f"Panduan aman:\n"
            f"1. lsp_query(findReferences, file_path='{file_path}', symbol='{old_name}') bila server tersedia.\n"
            f"2. search_code(pattern='{old_name}') untuk daftar kandidat.\n"
            f"3. read_file + edit_file per file dengan old_text unik 3-5 baris.\n"
            f"4. run_linter + run_tests setelah semua file diubah."
        )
    return (
        f"[RENAME]\n{impact_text}\n\n"
        f"LSP rename '{old_name}' -> '{new_name}':\n{lsp_result}\n"
        f"{_auto_verify_edited(os.path.abspath(file_path))}\n\n"
        f"Verifikasi: baca ulang file terpengaruh + run_tests bila perlu."
    )


def handle_search_code(args):
    path = args.get("path", ".")
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern wajib diisi."
    if not os.path.isdir(path) and not os.path.isfile(path):
        return f"Error: Directory tidak ditemukan: {path}. Cek path dengan list_dir/glob."
    # Prefer ripgrep: hormati .gitignore, jauh lebih cepat & lengkap
    # dibanding grep --include terbatas. Fallback ke grep bila rg tak ada.
    output = ""
    try:
        import shutil as _shutil
        if _shutil.which("rg"):
            cmd = [
                "rg", "-n", "--no-heading", "--color", "never",
                "-g", "!.git/**", "-g", "!node_modules/**",
                "-g", "!venv/**", "-g", "!.venv/**",
                "-g", "!__pycache__/**", "-g", "!dist/**",
                "-g", "!build/**", "-g", "!target/**",
                "-e", pattern, "--", path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            output = result.stdout or ""
        else:
            cmd = ["grep", "-rn",
                   "--include=*.py", "--include=*.pyi",
                   "--include=*.js", "--include=*.jsx", "--include=*.mjs", "--include=*.cjs",
                   "--include=*.ts", "--include=*.tsx", "--include=*.mts", "--include=*.cts",
                   "--include=*.go", "--include=*.php", "--include=*.rs",
                   "--include=*.rb", "--include=*.java", "--include=*.kt",
                   "--include=*.cs", "--include=*.swift", "--include=*.lua",
                   "--include=*.sh", "--include=*.bash", "--include=*.zsh",
                   "--include=*.html", "--include=*.css", "--include=*.scss",
                   "--include=*.vue", "--include=*.svelte",
                   "--include=*.json", "--include=*.yaml", "--include=*.yml",
                   "--include=*.toml", "--include=*.md",
                   "--include=*.conf", "--include=*.cfg", "--include=*.ini",
                   "--exclude-dir=.git", "--exclude-dir=node_modules",
                   "--exclude-dir=venv", "--exclude-dir=.venv",
                   "--exclude-dir=__pycache__", "--exclude-dir=dist",
                   "--exclude-dir=build", "--exclude-dir=target",
                   "-e", pattern, path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            output = result.stdout or ""
    except Exception as e:  # noqa: BLE001
        return f"Error search_code: {e}"
    if not output:
        return f"(not found: '{pattern}' in {path})"
    # Batasi output agar tidak meledakkan konteks LLM saat refactor besar.
    # Head+tail: 120 baris awal + 80 baris akhir (total 200, sama seperti dulu).
    lines = output.splitlines()
    _MAX_LINES = 200
    if len(lines) > _MAX_LINES:
        shown = "\n".join(lines[:120] + lines[len(lines) - 80:])
        return (f"{shown}\n... ({len(lines) - _MAX_LINES} baris tengah dipotong, head+tail ditampilkan — "
                f"persempit pattern atau path untuk hasil lengkap)")
    if len(output) > 50000:
        return _head_tail(output, 30000, 20000) + "\n... (output dipotong 50KB — persempit pattern/path)"
    return output


def handle_list_dir(args):
    path = args.get("path", ".")
    if not os.path.isdir(path):
        return f"Error: Directory tidak ditemukan: {path}. Gunakan pwd/run_command untuk cek direktori aktif."
    items = os.listdir(path)
    lines = []
    for item in sorted(items):
        full = os.path.join(path, item)
        label = "DIR" if os.path.isdir(full) else "   "
        lines.append(f"{label} {item}")
    return "\n".join(lines)


_GLOB_TIMEOUT_S = 8  # batas detik pencarian glob (anti-hang)


def handle_glob(args):
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    if not pattern:
        return "Error: pattern is required. Contoh: '**/*.py'"

    # Guard 1: tolak pencarian dari root filesystem — dulu bisa menjelajah
    # seluruh disk dan menggantungkan agent (kejadian nyata: glob("**/x", "/")).
    base = os.path.realpath(path)
    abs_prefix = ""
    if pattern.startswith(os.sep):
        abs_prefix = os.path.realpath(pattern.split("*")[0].rstrip("/") or os.sep)
    if base == os.sep or abs_prefix == os.sep:
        return (
            "Error: glob dari root '/' tidak diizinkan (terlalu besar & bisa "
            "menggantung). Pakai subdirektori spesifik, mis. path='.' atau "
            "path='/home/user/proyek'."
        )

    full_pattern = os.path.join(path, pattern) if path != "." else pattern

    # Guard 2: batas waktu — hentikan pencarian yang kelamaan.
    holder = {}

    def _do_glob():
        try:
            holder["matches"] = sorted(_glob_mod.glob(full_pattern, recursive=True))
        except Exception as e:  # noqa: BLE001
            holder["error"] = e

    worker = threading.Thread(target=_do_glob, daemon=True)
    worker.start()
    worker.join(timeout=_GLOB_TIMEOUT_S)
    if worker.is_alive():
        return (
            f"Error: glob timeout >{_GLOB_TIMEOUT_S}s (pencarian terlalu besar/"
            "dalam). Persempit path/pattern."
        )
    if "error" in holder:
        return f"Error: glob gagal: {holder['error']}"
    matches = holder.get("matches") or []
    if not matches:
        return f"(no matches for '{pattern}' in {path})"
    result = []
    for m in matches:
        label = "DIR" if os.path.isdir(m) else "   "
        result.append(f"{label} {m}")
    return "\n".join(result)


def handle_config_edit(args):
    path = args.get("path", "")
    if not os.path.exists(path):
        return f"Error: file not found: {path}"
    if _is_protected_identity_path(path):
        return (f"Error: {path} adalah file identitas/sistem yang sangat sensitif "
                f"(mis. shadow, passwd, sudoers, SSH key). Joki tidak mengizinkan "
                f"pengubahan file ini. Lakukan perubahan manual oleh admin.")

    _cleanup_old_backups()
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
    _mark_file_changed(os.path.abspath(path))

    return f"Backup saved: {backup_path}\nEdited: {directive} → {set_value}"
