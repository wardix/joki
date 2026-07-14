import os, json, subprocess, threading, queue, re, time, shutil
from pathlib import Path
from joki.state import _console, _LSP_LOCK, _LSP_CLIENTS
from joki.display import _Spinner

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


def _try_install_lsp(lang):
    cmd_template = _INSTALL_COMMANDS.get(lang)
    if not cmd_template:
        return False
    name = _LANG_NAMES.get(lang, lang)
    if not shutil.which(cmd_template[0]):
        _console.print(f"[yellow]Tidak bisa install LSP {name}: '{cmd_template[0]}' tidak ditemukan.[/yellow]")
        _console.print(f"  Install manual: [bold]{' '.join(cmd_template)}[/bold]")
        return False
    _console.print(f"[yellow]LSP server untuk {name} tidak terinstall.[/yellow]")
    _console.print(f"  Install: [bold]{' '.join(cmd_template)}[/bold]")
    try:
        ans = input("  Install sekarang? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans not in ("y", "yes"):
        _console.print(f"  [dim]Skip. Jalankan '/install-lsp {lang}' kapan saja.[/dim]")
        return False
    _console.print(f"  [dim]Menginstall {name} LSP server...[/dim]")
    try:
        r = subprocess.run(cmd_template, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            _console.print(f"  [green]Berhasil install {name} LSP server![/green]")
            return True
        else:
            _console.print(f"  [red]Gagal install: {r.stderr.strip()}[/red]")
            return False
    except subprocess.TimeoutExpired:
        _console.print(f"  [red]Timeout install (120 detik)[/red]")
        return False
    except Exception as e:
        _console.print(f"  [red]Error: {e}[/red]")
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
    if uri.startswith("file://"):
        uri = uri[7:]
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
        except Exception as e:
            _console.print(f"[dim]LSP {self.lang}: Gagal spawn: {e}[/dim]")
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
            except Exception:
                self.process.kill()
            self.process = None
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
            except Exception:
                return False
        uri = self._opened_files.get(path) or _path_to_uri(path)
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
        else:
            lang_id = lang_id

        version = self._opened_files.get(path, {}).get("version", 0) + 1 if isinstance(self._opened_files.get(path), dict) else 1
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
        return self._pending_diags.get(path, [])

    def get_all_diagnostics(self):
        result = []
        for path, diags in self._pending_diags.items():
            for d in diags:
                result.append((path, d))
        return result

    def query(self, operation, file_path, symbol=None, line=None, character=None):
        file_path = os.path.abspath(file_path)
        if not self.open_file(file_path, force_reload=True):
            return f"Error: Gagal membuka file {file_path}"

        entry = self._opened_files.get(file_path)
        uri = entry["uri"] if isinstance(entry, dict) else _path_to_uri(file_path)

        if operation in ("goToDefinition", "findReferences", "hover") and (line is None or character is None):
            found_line, found_char = _find_symbol_in_file(file_path, symbol or "")
            line = found_line
            character = found_char

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
            prefix = ""
            if children:
                for c in children:
                    cname = c.get("name", "?")
                    ckind = _SYMBOL_KINDS.get(c.get("kind", 0), "?")
                    lines.append(f"  {ckind} {cname}")
            lines.append(f"{kind_name} {name} (line {line_no})")
        return "\n".join(lines) if lines else "(tidak ada simbol)"

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
        self._pending[req_id] = event

        payload = _json_rpc_encode(req_id, method, params)
        with self._write_lock:
            if self.process and self.process.stdin:
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()

        event.wait(timeout=30)
        self._pending.pop(req_id, None)
        return self._responses.pop(req_id, None)

    def _send_notify(self, method, params=None):
        payload = _json_rpc_notify(method, params)
        with self._write_lock:
            if self.process and self.process.stdin:
                try:
                    self.process.stdin.write(payload.encode())
                    self.process.stdin.flush()
                except Exception:
                    pass

    def _reader(self):
        while not self._reader_stop.is_set():
            try:
                raw = self.process.stdout.read(1)
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
                            chunk = self.process.stdout.read(need)
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
            except Exception:
                break

    def _handle_message(self, body):
        try:
            msg = json.loads(body)
        except json.JSONDecodeError:
            return
        if "id" in msg:
            self._responses[msg["id"]] = msg.get("result")
            event = self._pending.get(msg.get("id"))
            if event:
                event.set()
        elif msg.get("method") == "textDocument/publishDiagnostics":
            uri = msg.get("params", {}).get("uri", "")
            diags = msg.get("params", {}).get("diagnostics", [])
            path = _uri_to_path(uri)
            self._pending_diags[path] = diags
        elif msg.get("method") == "window/logMessage":
            pass
        elif "result" in msg:
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
    if not symbol:
        return 0, 0
    try:
        with open(file_path, "r", errors="ignore") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            idx = line.find(symbol)
            if idx != -1:
                return i, idx
    except Exception:
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
            _console.print(f"[yellow]LSP {name}: server tidak tersedia.[/yellow]")
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
        for key, client in list(_LSP_CLIENTS.items()):
            try:
                client.stop()
            except Exception:
                pass
        _LSP_CLIENTS.clear()


def _is_error_query(text):
    t = text.lower()
    return any(kw in t for kw in _TRIGGER_KEYWORDS)


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
                except Exception:
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

def handle_lsp_query(args):
    operation = args.get("operation", "")
    file_path = args.get("file_path", "")
    symbol = args.get("symbol", "")
    line = args.get("line")
    character = args.get("character")

    if not operation or not file_path:
        return "Error: 'operation' dan 'file_path' wajib diisi."

    if not os.path.exists(file_path):
        return f"Error: File tidak ditemukan: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    available = _get_available_servers()
    lang = _ext_to_lang(ext, available)
    if not lang:
        return f"Tidak ada LSP server untuk file {file_path}"

    project_dir = _find_project_root(file_path)
    client = _get_lsp_client(lang, project_dir)
    if not client:
        return f"LSP server untuk {lang} tidak tersedia atau gagal start."

    with _Spinner(f"LSP {operation}"):
        return client.query(operation, file_path, symbol, line, character)


def _find_project_root(file_path):
    path = os.path.abspath(file_path)
    for root in [path] + list(Path(path).parents):
        root_str = str(root)
        if any(os.path.exists(os.path.join(root_str, marker))
               for marker in [".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
                              "pom.xml", "build.gradle", "composer.json", "Gemfile",
                              ".project", "Makefile", "CMakeLists.txt"]):
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
            status = "[green]Tersedia[/green]" if lang_name in available else "[red]Tidak terinstall[/red]"
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
    _try_install_lsp(lang)
