import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from rich.markup import escape

from joki.display import _color_error, _color_ok, _color_warn, _Spinner
from joki.state import _LSP_CLIENTS, _LSP_LOCK, _console, _is_project_root

_BUILTIN_SERVERS = {
    "javascript":  {"command": ["typescript-language-server", "--stdio"], "ext": [".js", ".jsx", ".mjs", ".cjs"]},
    "typescript":  {"command": ["typescript-language-server", "--stdio"], "ext": [".ts", ".tsx", ".mts", ".cts"]},
    "python":      {"command": ["pyright-langserver", "--stdio"], "ext": [".py", ".pyi"]},
    "go":          {"command": ["gopls"], "ext": [".go"]},
    "rust":        {"command": ["rust-analyzer"], "ext": [".rs"]},
    "c_cpp":       {"command": ["clangd"], "ext": [".c", ".cpp", ".h", ".hpp", ".cxx", ".hxx", ".cc", ".cxx"]},
    "java":        {"command": ["jdtls"], "ext": [".java"]},
    "kotlin":      {"command": ["kotlin-language-server"], "ext": [".kt", ".kts"]},
    "php":         {"command": ["intelephense", "--stdio"], "ext": [".php"]},
    "ruby":        {"command": ["solargraph", "stdio"], "ext": [".rb"]},
    "csharp":      {"command": ["csharp-ls"], "ext": [".cs"]},
    "lua":         {"command": ["lua-language-server"], "ext": [".lua"]},
    "bash":        {"command": ["bash-language-server", "start"], "ext": [".sh", ".bash", ".zsh"]},
    "yaml":        {"command": ["yaml-language-server", "--stdio"], "ext": [".yaml", ".yml"]},
    "html":        {"command": ["vscode-html-language-server", "--stdio"], "ext": [".html", ".htm"]},
    "css":         {"command": ["vscode-css-language-server", "--stdio"], "ext": [".css", ".scss", ".less"]},
    "swift":       {"command": ["sourcekit-lsp"], "ext": [".swift"]},
}

_RENAME_DISABLED_LANGS = {"c_cpp", "lua", "bash", "yaml", "html", "css", "swift"}

_TRIGGER_KEYWORDS = [
    "error", "bug", "fix", "perbaiki", "masalah", "gagal", "rusak",
    "salah", "tidak jalan", "crash", "exception", "broken", "typo",
    "warning", "debug", "memperbaiki", "betulin", "kenapa", "kenapa",
    "troubleshoot", "issue", "problem", "compile error", "runtime error",
    "syntax error", "type error", "reference error", "undefined",
]

_DIAGNOSTIC_SEVERITY = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}

_INSTALL_COMMANDS = {
    "javascript":  ["npm", "install", "-g", "typescript-language-server"],
    "typescript":  ["npm", "install", "-g", "typescript-language-server"],
    "python":      ["npm", "install", "-g", "pyright"],
    "go":          ["go", "install", "golang.org/x/tools/gopls@latest"],
    "rust":        ["rustup", "component", "add", "rust-analyzer"],
    "c_cpp":       None,
    "java":        None,
    "kotlin":      None,
    "php":         ["npm", "install", "-g", "intelephense"],
    "ruby":        ["gem", "install", "solargraph"],
    "csharp":      ["dotnet", "tool", "install", "-g", "csharp-ls"],
    "lua":         None,
    "bash":        ["npm", "install", "-g", "bash-language-server"],
    "yaml":        ["npm", "install", "-g", "yaml-language-server"],
    "html":        ["npm", "install", "-g", "vscode-langservers-extracted"],
    "css":         ["npm", "install", "-g", "vscode-langservers-extracted"],
    "swift":       None,
}

_LANG_NAMES = {
    "javascript": "JavaScript", "typescript": "TypeScript", "python": "Python",
    "go": "Go", "rust": "Rust", "c_cpp": "C/C++", "java": "Java",
    "kotlin": "Kotlin", "php": "PHP", "ruby": "Ruby", "csharp": "C#",
    "lua": "Lua", "bash": "Bash", "yaml": "YAML", "html": "HTML",
    "css": "CSS", "swift": "Swift",
}


_MANUAL_INSTALL_HINTS = {
    "c_cpp": "sudo apt install clangd",
    "java": "Install jdtls: https://github.com/eclipse-jdtls/eclipse.jdt.ls",
    "kotlin": "Install kotlin-language-server: https://github.com/fwcd/kotlin-language-server",
    "lua": "Install lua-language-server: https://github.com/LuaLS/lua-language-server",
    "swift": "Install sourcekit-lsp (bagian dari Swift toolchain): https://www.swift.org/install/",
}

_PERMISSION_ERROR_RE = re.compile(
    r"EACCES|EPERM|permission denied|operation not permitted|"
    r"need root|require root|must be run as root|as root/administrator|"
    r"try running .* as root|a password is required",
    re.IGNORECASE,
)


def _is_permission_error(text):
    """True bila output install mengindikasikan kurang hak akses (butuh sudo)."""
    if not text:
        return False
    return bool(_PERMISSION_ERROR_RE.search(text))


def _run_install_cmd(cmd_template):
    """Jalankan perintah install non-interaktif (anti-hang).

    - Selalu pakai stdin=DEVNULL agar proses anak TIDAK menunggu input
      (penyebab 'macet' 120 detik saat butuh password/prompt).
    - Env non-interaktif agar apt/npm tidak bertanya.
    - Return CompletedProcess.
    """
    env = dict(os.environ)
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    env.setdefault("NEEDRESTART_MODE", "a")
    env["npm_config_update_notifier"] = "false"
    return subprocess.run(
        cmd_template,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def _run_install_with_sudo(cmd_template, password):
    """Ulangi install via sudo non-interaktif (pakai password cache).

    Memakai `sudo -S` (baca password dari stdin pipe), BUKAN sudo interaktif
    yang menunggu di /dev/tty dan bikin 'macet'. Return CompletedProcess
    atau None bila sudo tak tersedia.
    """
    if not shutil.which("sudo"):
        return None
    env = dict(os.environ)
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    env.setdefault("NEEDRESTART_MODE", "a")
    env["npm_config_update_notifier"] = "false"
    if password in (None, "__ROOT__"):
        # Sudah root: tak perlu sudo, atau user membatalkan.
        if password is None:
            return None
        return subprocess.run(
            cmd_template,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    sudo_cmd = ["sudo", "-S", "--"] + list(cmd_template)
    try:
        return subprocess.run(
            sudo_cmd,
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            stdin=subprocess.PIPE,
            env=env,
        )
    except Exception:  # noqa: BLE001
        return None


def _try_install_lsp(lang, auto=False):
    cmd_template = _INSTALL_COMMANDS.get(lang)
    if not cmd_template:
        manual = _MANUAL_INSTALL_HINTS.get(lang, "")
        name = _LANG_NAMES.get(lang, lang)
        _console.print(f"[{_color_warn()}]LSP server untuk {name} tidak bisa diinstall otomatis.[/{_color_warn()}]")
        if manual:
            _console.print(f"  Install manual: [bold]{manual}[/bold]")
        return False
    name = _LANG_NAMES.get(lang, lang)
    if not shutil.which(cmd_template[0]):
        _console.print(f"[{_color_warn()}]Tidak bisa install LSP {name}: '{cmd_template[0]}' tidak ditemukan.[/{_color_warn()}]")
        _console.print(f"  Install manual: [bold]{' '.join(cmd_template)}[/bold]")
        return False
    _console.print(f"[{_color_warn()}]LSP server untuk {name} tidak terinstall.[/{_color_warn()}]")
    _console.print(f"  Install: [bold]{' '.join(cmd_template)}[/bold]")
    if not auto:
        try:
            ans = input("  Install sekarang? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans not in ("y", "yes"):
            _console.print(f"  [dim]Skip. Jalankan '/install-lsp {lang}' kapan saja.[/dim]")
            return False
    _console.print(f"  [dim]Menginstall {name} LSP server...[/dim]")
    try:
        r = _run_install_cmd(cmd_template)
        if r.returncode == 0:
            _console.print(f"  [{_color_ok()}]Berhasil install {name} LSP server![/{_color_ok()}]")
            return True
        combined = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        if _is_permission_error(combined):
            # Gagal karena hak akses (kasus umum: npm -g ke /usr/local).
            # Jangan hang menunggu prompt interaktif — minta password sekali
            # via _prompt_sudo lalu ulangi dengan `sudo -S` non-interaktif.
            _console.print(f"  [{_color_warn()}]Butuh hak administrator (sudo) untuk install ke direktori global.[/{_color_warn()}]")
            try:
                from joki.utils import _prompt_sudo as _ask_sudo
            except Exception:  # noqa: BLE001
                _ask_sudo = None  # type: ignore[assignment]
            password = _ask_sudo() if _ask_sudo else None
            if password is None:
                _console.print(f"  [dim]Dibatalkan. Alternatif tanpa sudo: install manual atau ubah prefix npm ke ~/.npm-global.[/dim]")
                _console.print(f"  [dim]Manual: sudo {' '.join(cmd_template)}[/dim]")
                return False
            r2 = _run_install_with_sudo(cmd_template, password)
            if r2 is not None and r2.returncode == 0:
                _console.print(f"  [{_color_ok()}]Berhasil install {name} LSP server (via sudo)![/{_color_ok()}]")
                return True
            err2 = ((r2.stdout if r2 else "") or "") + "\n" + ((r2.stderr if r2 else "") or "")
            err2 = err2.strip()[:500] if err2.strip() else combined[:500]
            _console.print(f"  [{_color_error()}]Gagal install via sudo: {escape(err2)}[/{_color_error()}]")
            _console.print(f"  [dim]Manual: sudo {' '.join(cmd_template)}[/dim]")
            return False
        else:
            _console.print(f"  [{_color_error()}]Gagal install: {escape((r.stderr or r.stdout or '').strip()[:500])}[/{_color_error()}]")
            return False
    except subprocess.TimeoutExpired:
        _console.print(f"  [{_color_error()}]Timeout install (120 detik)[/{_color_error()}]")
        return False
    except Exception as e:  # noqa: BLE001
        _console.print(f"  [{_color_error()}]Error: {escape(str(e))}[/{_color_error()}]")
        return False


def _detect_available_servers(user_servers=None):
    merged = {}
    all_defs = dict(_BUILTIN_SERVERS)
    if user_servers:
        for lang, cfg in user_servers.items():
            if cfg.get("disabled"):
                all_defs.pop(lang, None)
                continue
            if lang in all_defs and "command" not in cfg:
                continue
            if lang in all_defs:
                all_defs[lang] = {**all_defs[lang], **cfg}
            else:
                all_defs[lang] = cfg
    for lang, cfg in all_defs.items():
        cmd = cfg["command"]
        exe = shutil.which(cmd[0])
        if exe:
            merged[lang] = {**cfg, "command": [exe] + cmd[1:]}
    return merged


def _ext_to_lang(ext, available):
    ext = ext.lower()
    for lang, cfg in available.items():
        exts = cfg.get("ext")
        if exts and ext in exts:
            return lang
    return None


def _path_to_uri(path):
    path = os.path.abspath(path)
    return "file://" + ("/" + path if not path.startswith("/") else path)


def _uri_to_path(uri):
    uri = uri.removeprefix("file://")
    return uri


def _json_rpc_encode(msg_id, method, params=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    payload = json.dumps(body)
    return f"Content-Length: {len(payload)}\r\n\r\n{payload}"


def _json_rpc_notify(method, params=None):
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    payload = json.dumps(body)
    return f"Content-Length: {len(payload)}\r\n\r\n{payload}"


class LspClient:
    def __init__(self, lang, command, project_dir):
        self.lang = lang
        self.command = command
        self.project_dir = os.path.abspath(project_dir)
        self.process = None
        self._reader_thread = None
        self._write_lock = threading.Lock()
        self._data_lock = threading.Lock()  # protects _pending, _responses, _pending_diags from reader thread race
        self._req_id = 0
        self._pending = {}
        self._responses = {}
        self._pending_diags = {}
        self._reader_stop = threading.Event()
        self._ready = threading.Event()
        self._opened_files = {}
        self._buf = b""

    def start(self):
        if self.process is not None:
            return True
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self.project_dir,
                start_new_session=True,
            )
        except FileNotFoundError:
            return False
        except Exception as e:  # noqa: BLE001
            _console.print(f"[dim]LSP {self.lang}: Gagal spawn: {escape(str(e))}[/dim]")
            return False

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

        root_uri = _path_to_uri(self.project_dir)
        res = self._send_request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "hover": {"dynamicRegistration": True},
                    "documentSymbol": {"dynamicRegistration": True},
                    "diagnostics": {"dynamicRegistration": True},
                },
                "workspace": {"symbol": {"dynamicRegistration": True}},
            },
            "workspaceFolders": [{"uri": root_uri, "name": os.path.basename(self.project_dir)}],
        })
        if res is not None:
            self._ready.set()

        if not self._ready.wait(timeout=15):
            _console.print(f"[dim]LSP {self.lang}: Timeout initialize[/dim]")
            self.stop()
            return False

        self._send_notify("initialized")
        _console.print(f"[dim]LSP {self.lang}: Siap ({self.command[0]})[/dim]")
        return True

    def stop(self):
        self._send_notify("exit", None)
        time.sleep(0.1)
        self._reader_stop.set()
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:  # noqa: BLE001
                self.process.kill()
            self.process = None
        with self._data_lock:
            self._opened_files.clear()
            self._pending.clear()
            self._responses.clear()

    def open_file(self, path, content=None, force_reload=False):
        path = os.path.abspath(path)
        if path in self._opened_files and not force_reload:
            return True
        if content is None:
            try:
                with open(path, "r", errors="ignore") as f:
                    content = f.read()
            except Exception:  # noqa: BLE001
                return False
        existing = self._opened_files.get(path)
        if isinstance(existing, dict):
            uri = existing.get("uri") or _path_to_uri(path)
        else:
            uri = existing or _path_to_uri(path)
        lang_id = self.lang
        if lang_id == "javascript":
            lang_id = "javascript"
        elif lang_id == "typescript":
            lang_id = "typescript"
        elif lang_id == "python":
            lang_id = "python"
        elif lang_id == "go":
            lang_id = "go"
        elif lang_id == "rust":
            lang_id = "rust"
        elif lang_id == "c_cpp":
            lang_id = "cpp" if path.endswith((".cpp", ".hpp", ".cxx", ".hxx", ".cc")) else "c"
        elif lang_id == "bash":
            lang_id = "shellscript"

        prev_version = existing.get("version", 0) if isinstance(existing, dict) else 0
        version = prev_version + 1
        if path in self._opened_files:
            self._send_notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            })
        else:
            self._send_notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": lang_id,
                    "version": version,
                    "text": content,
                }
            })
        self._opened_files[path] = {"uri": uri, "version": version}
        return True

    def close_file(self, path):
        path = os.path.abspath(path)
        entry = self._opened_files.pop(path, None)
        if entry:
            uri = entry["uri"] if isinstance(entry, dict) else entry
            self._send_notify("textDocument/didClose", {
                "textDocument": {"uri": uri}
            })

    def get_file_diagnostics(self, path):
        path = os.path.abspath(path)
        with self._data_lock:
            return list(self._pending_diags.get(path, []))

    def get_all_diagnostics(self):
        result = []
        with self._data_lock:
            for path, diags in list(self._pending_diags.items()):
                for d in diags:
                    result.append((path, d))
        return result

    def query(self, operation, file_path, symbol=None, line=None, character=None, new_name=None):
        file_path = os.path.abspath(file_path)
        if not self.open_file(file_path, force_reload=True):
            return f"Error: Gagal membuka file {file_path}"

        entry = self._opened_files.get(file_path)
        if isinstance(entry, dict) and isinstance(entry.get("uri"), str):
            uri = entry["uri"]
        else:
            uri = _path_to_uri(file_path)

        if operation in ("goToDefinition", "findReferences", "hover", "rename") and (line is None or character is None):
            found_line, found_char = _find_symbol_in_file(file_path, symbol or "")
            line = found_line if line is None else line
            character = found_char if character is None else character

        if operation == "goToDefinition":
            return self._query_definition(uri, line or 0, character or 0)
        elif operation == "findReferences":
            return self._query_references(uri, line or 0, character or 0)
        elif operation == "hover":
            return self._query_hover(uri, line or 0, character or 0)
        elif operation == "documentSymbol":
            return self._query_document_symbol(uri)
        elif operation == "workspaceSymbol":
            return self._query_workspace_symbol(symbol or file_path)
        elif operation == "rename":
            if not new_name:
                return "Error: Parameter 'newName' wajib untuk operasi rename."
            return self._query_rename(uri, line or 0, character or 0, new_name)
        elif operation == "codeActions":
            if line is None or character is None:
                return "Error: Parameter 'line' dan 'character' wajib untuk codeActions."
            return self._query_code_actions(uri, line, character)
        elif operation == "format":
            return self._query_format(uri)
        else:
            return f"Error: Operasi LSP tidak dikenal: {operation}"

    def _query_definition(self, uri, line, character):
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return "(tidak ditemukan)"
        locations = result if isinstance(result, list) else [result]
        if not locations:
            return "(tidak ditemukan)"
        lines = []
        for loc in locations:
            if isinstance(loc, dict) and "uri" in loc and "range" in loc:
                r = loc["range"]
                fpath = _uri_to_path(loc["uri"])
                lines.append(f"{fpath}:{r['start']['line']+1}:{r['start']['character']+1}")
            elif isinstance(loc, dict) and "targetUri" in loc:
                fpath = _uri_to_path(loc["targetUri"])
                lines.append(f"{fpath}")
        return "\n".join(lines) if lines else "(tidak ditemukan)"

    def _query_references(self, uri, line, character):
        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": False},
        })
        if not result:
            return "(tidak ada referensi)"
        if not isinstance(result, list):
            return "(tidak ada referensi)"
        lines = []
        for ref in result:
            if "uri" in ref and "range" in ref:
                r = ref["range"]
                fpath = _uri_to_path(ref["uri"])
                lines.append(f"{fpath}:{r['start']['line']+1}:{r['start']['character']+1}")
        return "\n".join(lines) if lines else "(tidak ada referensi)"

    def _query_hover(self, uri, line, character):
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return "(tidak ada informasi)"
        if isinstance(result, dict):
            contents = result.get("contents", "")
            if isinstance(contents, str):
                return contents
            if isinstance(contents, list):
                return "\n".join(str(c) for c in contents)
            if isinstance(contents, dict):
                return contents.get("value", str(contents))
        return str(result)

    def _query_document_symbol(self, uri):
        result = self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        if not result:
            return "(tidak ada simbol)"
        if not isinstance(result, list):
            result = [result]
        lines = []
        for sym in result:
            name = sym.get("name", "?")
            kind = sym.get("kind", 0)
            kind_name = _SYMBOL_KINDS.get(kind, f"kind_{kind}")
            r = sym.get("selectionRange", sym.get("range", {}))
            rng = r.get("start", {}) if isinstance(r, dict) else {}
            line_no = rng.get("line", 0) + 1
            children = sym.get("children", [])
            if children:
                for c in children:
                    cname = c.get("name", "?")
                    ckind = _SYMBOL_KINDS.get(c.get("kind", 0), "?")
                    lines.append(f"  {ckind} {cname}")
            lines.append(f"{kind_name} {name} (line {line_no})")
        return "\n".join(lines) if lines else "(tidak ada simbol)"

    def _query_rename(self, uri, line, character, new_name):
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "newName": new_name,
        }
        result = self._send_request("textDocument/rename", params)
        if not result:
            return "(rename tidak menghasilkan perubahan)"
        total_edits = 0
        affected_files = []
        doc_changes = result.get("documentChanges")
        if isinstance(doc_changes, list):
            # Pyright & server modern: WorkspaceEdit.documentChanges
            for doc_change in doc_changes:
                if not isinstance(doc_change, dict):
                    continue
                if doc_change.get("kind") and doc_change.get("kind") != "edit":
                    continue  # create/rename/delete file ops — skip aman
                fpath = _uri_to_path(doc_change.get("textDocument", {}).get("uri", ""))
                if not fpath:
                    continue
                n, err = self._apply_text_edits(fpath, doc_change.get("edits", []))
                if err:
                    return err
                total_edits += n
                affected_files.append(f"{fpath} ({n} changes)")
        else:
            for doc_uri, edits in (result.get("changes", {}) or {}).items():
                fpath = _uri_to_path(doc_uri)
                n, err = self._apply_text_edits(fpath, edits)
                if err:
                    return err
                total_edits += n
                affected_files.append(f"{fpath} ({n} changes)")
        if total_edits == 0:
            return "(tidak ada perubahan — simbol mungkin tidak ditemukan)"
        summary = f"Rename selesai: {total_edits} perubahan di {len(affected_files)} file"
        for f in affected_files:
            summary += f"\n  ✓ {f}"
        return summary

    def _apply_text_edits(self, fpath, edits):
        """Terapkan daftar edit LSP ke file (dari bawah ke atas agar offset
        tidak bergeser). Return (n_applied, error)."""
        try:
            with open(fpath, "r") as f:
                content = f.read()
            lines = content.splitlines(True)
            ordered = sorted(
                edits,
                key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]),
                reverse=True,
            )
            for edit in ordered:
                rng = edit.get("range", {})
                start = rng.get("start", {})
                new_text = edit.get("newText", "")
                s_line, s_char = start.get("line", 0), start.get("character", 0)
                end = rng.get("end", {})
                e_line, e_char = end.get("line", 0), end.get("character", 0)
                if s_line == e_line:
                    line_text = lines[s_line]
                    lines[s_line] = line_text[:s_char] + new_text + line_text[e_char:]
                else:
                    lines[s_line] = lines[s_line][:s_char] + new_text
                    for l in range(s_line + 1, e_line + 1):
                        lines[l] = ""
            with open(fpath, "w") as f:
                f.writelines(lines)
            return len(ordered), None
        except Exception as ex:  # noqa: BLE001
            return 0, f"Error menulis perubahan ke {fpath}: {ex}"

    def _query_code_actions(self, uri, line, character):
        with self._data_lock:
            diags = list(self._pending_diags.get(_uri_to_path(uri), []))
        context = {
            "diagnostics": diags,
            "only": ["quickfix", "refactor", "refactor.extract", "source.organizeImports"],
        }
        params = {
            "textDocument": {"uri": uri},
            "range": {"start": {"line": line, "character": character},
                      "end": {"line": line + 1, "character": 0}},
            "context": context,
        }
        result = self._send_request("textDocument/codeAction", params)
        if not result:
            return "(tidak ada code action tersedia)"
        if not isinstance(result, list):
            result = [result]
        lines = []
        for i, action in enumerate(result[:20]):
            title = action.get("title", "?")
            kind = action.get("kind", "")
            lines.append(f"{i+1}. [{kind}] {title}")
        if lines:
            lines.insert(0, f"{len(result)} code action(s) tersedia. Jalankan via executeCodeAction dengan index.")
        return "\n".join(lines) if lines else "(tidak ada code action)"

    def _query_format(self, uri):
        result = self._send_request("textDocument/formatting", {
            "textDocument": {"uri": uri},
            "options": {"tabSize": 4, "insertSpaces": True},
        })
        if not result:
            return "(tidak ada perubahan formatting)"
        path = _uri_to_path(uri)
        try:
            with open(path, "r") as f:
                content = f.read()
            lines = content.splitlines(True)
            for edit in sorted(result, key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]), reverse=True):
                rng = edit["range"]
                s_line, s_char = rng["start"]["line"], rng["start"]["character"]
                e_line, e_char = rng["end"]["line"], rng["end"]["character"]
                new_text = edit.get("newText", "")
                if s_line == e_line:
                    old_line = lines[s_line]
                    lines[s_line] = old_line[:s_char] + new_text + old_line[e_char:]
                else:
                    lines[s_line] = lines[s_line][:s_char] + new_text
                    for l in range(s_line + 1, e_line + 1):
                        lines[l] = ""
            with open(path, "w") as f:
                f.writelines(lines)
            return f"File diformat: {path} ({len(result)} perubahan)"
        except Exception as ex:  # noqa: BLE001
            return f"Error formatting: {ex}"

    def _query_workspace_symbol(self, query):
        result = self._send_request("workspace/symbol", {
            "query": query,
        })
        if not result:
            return "(tidak ditemukan)"
        if not isinstance(result, list):
            result = [result]
        lines = []
        for sym in result[:30]:
            name = sym.get("name", "?")
            kind = _SYMBOL_KINDS.get(sym.get("kind", 0), "?")
            loc = sym.get("location", {})
            if isinstance(loc, dict) and "uri" in loc:
                r = loc.get("range", {}).get("start", {})
                fpath = _uri_to_path(loc["uri"])
                line_no = r.get("line", 0) + 1
                lines.append(f"{kind} {name} → {fpath}:{line_no}")
            else:
                lines.append(f"{kind} {name}")
        return "\n".join(lines) if lines else "(tidak ditemukan)"

    def _send_request(self, method, params=None):
        self._req_id += 1
        req_id = self._req_id
        event = threading.Event()
        with self._data_lock:
            self._pending[req_id] = event

        payload = _json_rpc_encode(req_id, method, params)
        with self._write_lock:
            if self.process and self.process.stdin:
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()

        event.wait(timeout=30)
        with self._data_lock:
            self._pending.pop(req_id, None)
            return self._responses.pop(req_id, None)

    def _send_notify(self, method, params=None):
        payload = _json_rpc_notify(method, params)
        with self._write_lock:
            if self.process and self.process.stdin:
                try:
                    self.process.stdin.write(payload.encode())
                    self.process.stdin.flush()
                except Exception:  # noqa: BLE001, S110
                    pass

    def _reader(self):
        while not self._reader_stop.is_set():
            try:
                if not self.process or not self.process.stdout:
                    break
                raw = self.process.stdout.read(1)  # type: ignore[union-attr]
                if not raw:
                    break
                self._buf += raw
                if b"\r\n\r\n" in self._buf:
                    header, rest = self._buf.split(b"\r\n\r\n", 1)
                    cl_match = re.search(rb"Content-Length:\s*(\d+)", header)
                    if cl_match:
                        length = int(cl_match.group(1))
                        need = length - len(rest)
                        while need > 0 and not self._reader_stop.is_set():
                            if not self.process or not self.process.stdout:
                                break
                            chunk = self.process.stdout.read(need)  # type: ignore[union-attr]
                            if not chunk:
                                break
                            rest += chunk
                            need = length - len(rest)
                        if len(rest) >= length:
                            body = rest[:length]
                            self._buf = rest[length:]
                            self._handle_message(body)
                        else:
                            self._buf = header + b"\r\n\r\n" + rest
                    else:
                        self._buf = b""
            except Exception:  # noqa: BLE001
                break

    def _handle_message(self, body):
        try:
            msg = json.loads(body)
        except json.JSONDecodeError:
            return
        if "id" in msg:
            with self._data_lock:
                self._responses[msg["id"]] = msg.get("result")
                event = self._pending.get(msg.get("id"))
            if event:
                event.set()
        elif msg.get("method") == "textDocument/publishDiagnostics":
            uri = msg.get("params", {}).get("uri", "")
            diags = msg.get("params", {}).get("diagnostics", [])
            path = _uri_to_path(uri)
            with self._data_lock:
                self._pending_diags[path] = diags
        elif msg.get("method") == "window/logMessage" or "result" in msg:
            pass


_SYMBOL_KINDS = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}


def _find_symbol_in_file(file_path, symbol):
    """Cari posisi simbol di file: prioritaskan baris definisi (def/class/dll),
    lalu kemunculan whole-word pertama, terakhir substring (kompatibilitas).

    Return (line, character) 0-indexed; (0, 0) bila tidak ketemu.
    """
    if not symbol:
        return 0, 0
    try:
        with open(file_path, "r", errors="ignore") as f:
            lines = f.readlines()
        word = re.compile(r"\b" + re.escape(symbol) + r"\b")
        defin = re.compile(
            r"^\s*(?:async\s+def|def|class|function|func|fn|struct|trait|impl|enum|"
            r"interface|type|const|let|var)\s+" + re.escape(symbol) + r"\b"
        )
        for i, line in enumerate(lines):
            if defin.search(line):
                m = word.search(line)
                return i, m.start() if m else 0
        for i, line in enumerate(lines):
            m = word.search(line)
            if m:
                return i, m.start()
        for i, line in enumerate(lines):
            idx = line.find(symbol)
            if idx != -1:
                return i, idx
    except Exception:  # noqa: BLE001, S110
        pass
    return 0, 0


# ==================== MANAGER ====================

def _get_available_servers():
    from joki.config import _get_lsp_config
    user_cfg = _get_lsp_config()
    return _detect_available_servers(user_cfg.get("servers"))


def _get_lsp_client(lang, project_dir):
    with _LSP_LOCK:
        key = (lang, os.path.abspath(project_dir))
        client = _LSP_CLIENTS.get(key)
        if client and client.process and client.process.poll() is None:
            return client
        available = _get_available_servers()
        cfg = available.get(lang)
        if not cfg:
            name = _LANG_NAMES.get(lang, lang)
            _console.print(f"[{_color_warn()}]LSP {name}: server tidak tersedia.[/{_color_warn()}]")
            if _try_install_lsp(lang):
                available = _get_available_servers()
                cfg = available.get(lang)
            if not cfg:
                _console.print(f"  [dim]LSP untuk {name} tidak bisa digunakan. Jalankan '/install-lsp {lang}' kapan saja.[/dim]")
                return None
        client = LspClient(lang, cfg["command"], project_dir)
        if not client.start():
            return None
        _LSP_CLIENTS[key] = client
        return client


def _cleanup_lsp():
    with _LSP_LOCK:
        clients = list(_LSP_CLIENTS.items())
        _LSP_CLIENTS.clear()
    for key, client in clients:
        try:
            client.stop()
        except Exception:  # noqa: BLE001, S110
            pass


def _is_error_query(text):
    t = text.lower()
    return any(kw in t for kw in _TRIGGER_KEYWORDS)


def get_file_diagnostics_wait(file_path, timeout=10):
    """Buka file di server LSP yang sesuai, tunggu diagnostik, kembalikan list.

    Dipakai untuk validasi pasca-edit bahasa yang butuh konteks server
    (TS/JSX, C/C++, PHP). Return None bila tak ada server yang cocok /
    file tak ada / server gagal start. Return [] bila timeout tanpa
    diagnostik (artinya: tidak diketahui, BUKAN jaminan bersih).
    """
    if not file_path or not os.path.isfile(file_path):
        return None
    ext = os.path.splitext(file_path)[1].lower()
    available = _get_available_servers()
    lang = _ext_to_lang(ext, available)
    if not lang:
        return None
    project_dir = _find_project_root(file_path)
    client = _get_lsp_client(lang, project_dir)
    if not client:
        return None
    abspath = os.path.abspath(file_path)
    try:
        client.open_file(abspath, force_reload=True)
    except Exception:  # noqa: BLE001, S110
        pass
    deadline = time.time() + max(1, timeout)
    while time.time() < deadline:
        try:
            with client._data_lock:
                if abspath in client._pending_diags:
                    return list(client._pending_diags[abspath])
        except Exception:  # noqa: BLE001
            break
        time.sleep(0.5)
    return []


def get_project_diagnostics(project_dir):
    project_dir = os.path.abspath(project_dir)
    available = _get_available_servers()
    if not available:
        return ""

    files_by_lang = {}
    for root, dirs, fnames in os.walk(project_dir):
        dn = os.path.basename(root)
        if dn.startswith(".") or dn == "node_modules" or dn == "__pycache__" or dn == "venv" or dn == ".git":
            dirs[:] = []
            continue
        for fname in fnames:
            ext = os.path.splitext(fname)[1].lower()
            lang = _ext_to_lang(ext, available)
            if lang:
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getsize(fpath) > 500_000:
                        continue
                except Exception:  # noqa: BLE001, S112
                    continue
                files_by_lang.setdefault(lang, []).append(fpath)

    if not files_by_lang:
        return ""

    all_diags = []
    total_files = sum(len(v) for v in files_by_lang.values())
    max_files = 50
    if total_files > max_files:
        for lang in files_by_lang:
            files_by_lang[lang] = files_by_lang[lang][:max(len(files_by_lang[lang]) * max_files // total_files, 5)]

    for lang, files in files_by_lang.items():
        client = _get_lsp_client(lang, project_dir)
        if not client:
            continue
        for fpath in files:
            client.open_file(fpath)
            time.sleep(0.1)
            diags = client.get_file_diagnostics(fpath)
            for d in diags:
                all_diags.append((fpath, d))

    if not all_diags:
        return ""

    errors = [(p, d) for p, d in all_diags if d.get("severity", 1) <= 2]
    errors.sort(key=lambda x: x[1].get("severity", 1))

    max_diags = 100
    errors = errors[:max_diags]

    lines = ["=== LSP Diagnostics ==="]
    for fpath, d in errors:
        r = d.get("range", {}).get("start", {})
        line = r.get("line", 0) + 1
        sev = _DIAGNOSTIC_SEVERITY.get(d.get("severity"), "Unknown")
        msg = d.get("message", "").split("\n")[0][:150]
        frel = os.path.relpath(fpath, project_dir)
        lines.append(f"  {frel}:{line}  {sev}: {msg}")

    return "\n".join(lines)


# ==================== TOOL HANDLER ====================

_RENAME_PREOPEN_EXCLUDE = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", "target", ".next", "vendor", "site-packages",
}
_RENAME_PREOPEN_MAX = 50
_RENAME_PREOPEN_MAX_BYTES = 500_000


def _preopen_project_files(client, project_dir, ext, extra_files=(), limit=_RENAME_PREOPEN_MAX):
    """Buka file se-ekstensi di proyek agar server LSP mengindeksnya.

    Rename lintas file hanya jalan bila file pengimport ikut terbuka/diindeks.
    `extra_files` (mis. importers dari dep-graph) diprioritaskan dibuka dulu,
    lalu dilengkapi walk direktori sampai `limit`. Dibatasi ukuran file dan
    direktori exclude agar tidak berat di repo besar.

    Return (opened, total): jumlah dibuka vs total file se-ekstensi yang
    ditemukan — untuk pelaporan cakupan yang jujur.
    """
    opened = 0
    total = 0
    seen = set()

    def _open(fp):
        nonlocal opened
        ap = os.path.abspath(fp)
        if ap in seen:
            return True
        seen.add(ap)
        try:
            if os.path.getsize(ap) > _RENAME_PREOPEN_MAX_BYTES:
                return False
        except OSError:
            return False
        try:
            client.open_file(ap)
        except Exception:  # noqa: BLE001, S110
            pass
        opened += 1
        return True

    for fp in extra_files or []:
        if opened >= limit:
            break
        if not (os.path.isfile(fp) and fp.lower().endswith(ext)):
            continue
        total += 1
        _open(fp)  # tak pernah raise (semua I/O di dalamnya di-guard)
    try:
        for root, dirs, fnames in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in _RENAME_PREOPEN_EXCLUDE and not d.startswith(".")]
            for fn in sorted(fnames):
                if not fn.lower().endswith(ext):
                    continue
                ap = os.path.abspath(os.path.join(root, fn))
                if ap in seen:
                    continue  # sudah dihitung via extra_files
                total += 1
                if opened >= limit:
                    continue
                _open(ap)
    except Exception:  # noqa: BLE001, S110
        pass
    return opened, total


def _rename_importers(project_dir, file_path, symbol=""):
    """Cari file pengimport target via dep-graph + referensi simbol via LSP.

    File-level (siapa mengimport modul ini) digabung dengan symbol-level
    (di mana simbol ini dipakai) agar preopen mencakup keduanya. Best-effort,
    return list abspath (mungkin kosong). Tidak pernah raise."""
    found = set()
    try:
        from joki.tools.deps import find_symbol_references, get_file_graph
        graph = get_file_graph(project_dir)
        rel = os.path.relpath(os.path.abspath(file_path), os.path.abspath(project_dir))
        for f, info in graph.items():
            if rel in (info.get("resolved") or []):
                found.add(os.path.join(os.path.abspath(project_dir), f))
        if symbol:
            for r, _ln, _col in find_symbol_references(project_dir, rel, symbol):
                found.add(os.path.join(os.path.abspath(project_dir), r))
    except Exception:  # noqa: BLE001, S110
        pass
    return sorted(found)


def handle_lsp_query(args):
    operation = args.get("operation", "")
    file_path = args.get("file_path", "")
    symbol = args.get("symbol", "")
    line = args.get("line")
    character = args.get("character")
    new_name = args.get("newName", "")

    if not operation or not file_path:
        return "Error: 'operation' dan 'file_path' wajib diisi."

    if not os.path.exists(file_path):
        return f"Error: File tidak ditemukan: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    available = _get_available_servers()
    lang = _ext_to_lang(ext, available)
    if not lang:
        return f"Tidak ada LSP server untuk file {file_path}"

    if operation == "rename" and lang in _RENAME_DISABLED_LANGS:
        return f"LSP rename tidak didukung untuk {_LANG_NAMES.get(lang, lang)}."

    project_dir = _find_project_root(file_path)
    client = _get_lsp_client(lang, project_dir)
    if not client:
        return f"LSP server untuk {lang} tidak tersedia atau gagal start."

    if operation == "rename":
        # Pre-open file seproyek agar rename lintas file terindeks,
        # lalu beri jeda singkat agar server selesai memprosesnya.
        # Importers dari dep-graph + referensi simbol diprioritaskan
        # (penting di repo besar yang melebihi batas preopen).
        importers = _rename_importers(project_dir, file_path, symbol=symbol)
        opened, total = _preopen_project_files(client, project_dir, ext, extra_files=importers)
        time.sleep(3)

    with _Spinner(f"LSP {operation}"):
        result = client.query(operation, file_path, symbol, line, character, new_name)

    if operation == "rename":
        # Pelaporan cakupan yang jujur: rename hanya seandal file yang
        # sempat dibuka. Sisanya WAJIB disapu via search_code + test.
        if total > opened:
            result = (result or "") + (
                f"\n[CAKUPAN] {opened}/{total} file {ext} dibuka untuk rename; "
                f"{total - opened} di luar jangkauan (batas {_RENAME_PREOPEN_MAX}). "
                "Sapu sisa via search_code(pattern=nama_lama) + jalankan test."
            )
        else:
            result = (result or "") + f"\n[CAKUPAN] {opened}/{total} file {ext} dibuka untuk rename."
    return result


def _find_project_root(file_path):
    path = os.path.abspath(file_path)
    for root in [path] + list(Path(path).parents):
        root_str = str(root)
        if _is_project_root(root_str):
            return root_str
    return os.path.dirname(path)


# ==================== INSTALL LSP COMMAND ====================

def handle_install_lsp_command(args):
    lang = args.strip().lower() if args else ""
    if not lang:
        _console.print("[bold]LSP Servers:[/bold]")
        available = _get_available_servers()
        for lang_name, cfg in sorted(_BUILTIN_SERVERS.items()):
            name = _LANG_NAMES.get(lang_name, lang_name)
            exts = ", ".join(cfg["ext"])
            status = f"[{_color_ok()}]Tersedia[/{_color_ok()}]" if lang_name in available else f"[{_color_error()}]Tidak terinstall[/{_color_error()}]"
            install_cmd = " ".join(_INSTALL_COMMANDS.get(lang_name, [])) if _INSTALL_COMMANDS.get(lang_name) else "(manual)"
            _console.print(f"  {name:12} {status:20} {exts}")
            if lang_name not in available and _INSTALL_COMMANDS.get(lang_name):
                _console.print(f"               Install: [dim]{install_cmd}[/dim]")
        _console.print("\nGunakan [bold]/install-lsp <nama>[/bold] untuk install, misal: /install-lsp python")
        return
    if lang not in _BUILTIN_SERVERS:
        matches = [k for k in _BUILTIN_SERVERS if lang in k]
        if matches:
            _console.print(f"Maksud Anda: {', '.join(f'/install-lsp {m}' for m in matches)}")
        else:
            _console.print(f"LSP server '{lang}' tidak dikenal.")
        return
    name = _LANG_NAMES.get(lang, lang)
    if lang in _get_available_servers():
        _console.print(f"[{_color_ok()}]LSP server {name} sudah terinstall.[/{_color_ok()}]")
        return
    _try_install_lsp(lang, auto=True)
