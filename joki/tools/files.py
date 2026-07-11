import os, math, subprocess, re, shutil
from difflib import unified_diff
from joki.state import *
from joki.utils import *
from joki.display import _numbered

_PAGE_SIZE = 100

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".scala",
    ".c", ".h", ".cpp", ".hpp", ".cxx", ".cc", ".cs", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".mm", ".dart", ".lua",
    ".sh", ".bash", ".zsh", ".pl", ".pm", ".r", ".m", ".jl",
    ".sql", ".css", ".scss", ".less", ".sass", ".vue", ".svelte",
    ".go", ".zig", ".nim", "ex", ".exs",
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
    except Exception:
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
    if not os.path.isfile(path):
        return f"Error: File tidak ditemukan: {path}. Cek path dengan list_dir/glob atau buat file dulu dengan write_file."
    _READ_FILES.add(os.path.abspath(path))
    with open(path) as f:
        lines = f.readlines()
    total = len(lines)

    offset = args.get("offset", None)
    limit = args.get("limit", None)

    if offset is not None and limit is not None:
        if offset < 1:
            offset = 1
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
                        result += f"\n--- SELESAI ---"
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

def handle_edit_file(args):
    path = args.get("path", "")
    if not os.path.isfile(path):
        return f"Error: file tidak ditemukan: {path}. Cek path dengan list_dir/glob."

    abs_path = os.path.abspath(path)
    if abs_path not in _READ_FILES:
        return (f"Error: WAJIB baca file dulu sebelum edit! "
                f"Panggil read_file(path=\"{path}\") untuk melihat isi file, "
                f"lalu copy-paste old_text yang tepat dari output read_file.")

    with open(path) as f:
        old = f.read()

    ot = args.get("old_text", "")
    nt = args.get("new_text", "")

    if not ot:
        return (f"Error: 'old_text' tidak boleh kosong. "
                f"Gunakan write_file untuk overwrite file, "
                f"atau berikan old_text yang valid.")

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
    backup_path = os.path.join(BACKUP_DIR, os.path.basename(path) + f".{os.getpid()}.bak")
    shutil.copy2(path, backup_path)

    final = old.replace(match_text, nt, 1)

    with open(path, "w") as f:
        f.write(final)

    diff = unified_diff(old.splitlines(keepends=True), final.splitlines(keepends=True), fromfile=path, tofile=path)
    diff_str = "".join(diff)

    label = "Fuzzy-matched" if fuzzy else "Edited"
    msg = f"{label}: {path} (backup: {backup_path})"
    if fuzzy:
        preview = match_text[:80].replace('\n', '\\n')
        msg += f"\nActual match: \"{preview}\""
    if diff_str:
        msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
    return msg


def handle_undo_edit(args):
    backup_path = args.get("backup_path", "")
    original_path = args.get("path", "")
    if not backup_path or not os.path.exists(backup_path):
        return "Error: backup file tidak ditemukan. Path: " + backup_path
    if not original_path:
        for f in os.listdir(BACKUP_DIR):
            if f.endswith(".bak"):
                original_path = os.path.join(os.path.dirname(backup_path), f.replace(f".{os.getpid()}.bak", ""))
                break
    if original_path and os.path.exists(backup_path):
        shutil.copy2(backup_path, original_path)
        os.remove(backup_path)
        return f"Undo berhasil: {original_path} dikembalikan ke backup."
    return f"Error: Tidak bisa undo. Backup ada di {backup_path}"


def handle_search_code(args):
    path = args.get("path", ".")
    if not os.path.isdir(path):
        return f"Error: Directory tidak ditemukan: {path}. Cek path dengan list_dir/glob."
    cmd = ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
           "--include=*.html", "--include=*.css", "--include=*.json",
           "--include=*.yaml", "--include=*.yml", "--include=*.md",
           "--include=*.conf", "--include=*.cfg", "--include=*.ini",
           args["pattern"], path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = result.stdout or ""
    if result.returncode != 0 and not output:
        return f"(not found: '{args['pattern']}' in {path})"
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


def handle_glob(args):
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    if not pattern:
        return "Error: pattern is required. Contoh: '**/*.py'"
    import glob as _glob
    full_pattern = os.path.join(path, pattern) if path != "." else pattern
    matches = sorted(_glob.glob(full_pattern, recursive=True))
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
