import os, sys, json, re, time, threading, shlex
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import WordCompleter, PathCompleter, merge_completers

from joki.state import *
from joki.config import _get_data_dir, _get_config_path, _load_models, _MODELS, _current_model_config, _CONFIG_PATH
from joki.constants import *
from joki.display import *
from joki.llm import *
from joki.session import *
from joki.executor import *
from joki.tools.shell import _close_shell
from joki.tools.memory import _load_memory, _save_memory
from joki.plugins import _load_plugins
from joki.tools.lsp import _cleanup_lsp, get_project_diagnostics, _is_error_query, handle_install_lsp_command

_TOOL_FUNC_NAMES = {t["function"]["name"] for t in TOOLS}

_SYSTEM_PROMPT_BASE = (
    "Kamu adalah Joki — AI agent yang dibuat oleh Rahmad Budiman. Jika ditanya siapa yang membuat atau menciptakan kamu, jawab: 'Saya dibuat oleh Rahmad Budiman.'\n"
    "Aturan utama: JANGAN PERNAH BERHENTI DI TENGAH JALAN. Kerjakan task sampai tuntas "
    "dalam satu sesi — jangan minta konfirmasi, jangan ngasih laporan parsial, "
    "jangan nanya 'mau dilanjutkan?' LANJUTKAN TERUS sampai dapat hasil akhir atau error fatal.\n\n"
    "SEBELUM MENGERJAKAN APAPUN, buat Rencana Pengerjaan dulu menggunakan todo_create — "
    "rinci langkah-langkah yang akan dilakukan. Setelah satu langkah selesai, "
    "tandai dengan todo_done. Gunakan todo_show untuk cek progress.\n\n"
    "PENTING: Setelah MENYELESAIKAN semua Rencana Pengerjaan, jangan diam saja. "
    "Buat ringkasan naratif dari hasil kerja — jelaskan apa yang dilakukan "
    "dan hasilnya dalam bahasa yang mudah dipahami pengguna. Jangan hanya "
    "menampilkan output tool mentah atau isi Rencana Pengerjaan.\n\n"
    "BAHASA: Selama proses pengerjaan (thought, analysis, explanation of steps), "
    "gunakan BAHASA INGGRIS agar lebih natural. Hanya ringkasan/kesimpulan AKHIR "
    "saja yang diterjemahkan ke bahasa Indonesia.\n\n"
    "FILE SEMENTARA: Jika membuat script sebagai alat bantu (misal script Python "
    "untuk ngecek API key, parsing data, dll.), simpan di /tmp/ JANGAN di "
    "direktori kerja. Setelah tugas selesai, hapus file tersebut pakai "
    "run_command(\"rm /tmp/namafile\").\n\n"
    "Keluarkan [RENCANA] sebagai teks (2-3 baris), lalu KIRIMKAN tool_calls SEBENARNYA (fungsi) — jangan tulis deskripsi tool sebagai teks.\n"
    "PENTING: tool_calls harus dikirim sebagai struktur data fungsi, BUKAN ditulis manual sebagai teks.\n"
    "Contoh: content=\"[RENCANA] Cek MySQL\" + tool_calls=run_command(...)\n\n"

    "=== PANDUAN EDIT FILE ===\n"
    "WRITE_FILE:\n"
    "  - Parameter: path (path absolut atau relatif), content (isi file lengkap)\n"
    "  - Untuk file BARU: langsung write_file\n"
    "  - Untuk file LAMA (overwrite): tetap bisa, tapi LEBIH BAIK pakai edit_file\n\n"
    "EDIT_FILE:\n"
    "  - Parameter: path, old_text (teks yang akan diganti), new_text (teks pengganti)\n"
    "  - ATURAN WAJIB: SEBELUM edit_file, KAMU HARUS read_file DAHULU untuk melihat isi file.\n"
    "    Sistem akan MENOLAK edit_file jika file belum dibaca di sesi ini.\n"
    "  - old_text TIDAK perlu eksak — sistem punya FUZZY MATCHING otomatis:\n"
    "      * Whitespace (spasi, tab, newline) dinormalisasi\n"
    "      * Indentasi yang beda tetap cocok\n"
    "      * Teks lama langsung DIREPLACE (tidak di-comment)\n"
    "  - Sertakan minimal 3-5 baris KONTEKS di old_text agar match-nya unik.\n"
    "    old_text yang terlalu pendek (1 baris) rentan kena error 'multiple matches'.\n"
    "  - Kalau dapat error 'ditemukan N kali': tambah baris konteks di old_text, "
    "jangan cuma 1-2 baris.\n\n"
    "READ_FILE:\n"
    "  - Parameter: path, offset (baris mulai, optional), limit (jumlah baris, optional)\n"
    "  - WAJIB: setiap mau edit_file, panggil read_file DAHULU\n"
    "  - Gunakan offset & limit untuk file besar — baca per bagian\n"
    "  - Output selalu dengan nomor baris — gunakan itu untuk referensi\n\n"
    "GLOB:\n"
    "  - Cari file berdasarkan pola: glob(pattern=\"**/*.py\", path=\"src\")\n"
    "  - Support ** untuk recursive\n\n"
    "=== CONTOH ALUR EDIT YANG BENAR ===\n"
    "  1. read_file(path=\"src/main.py\") → lihat isi\n"
    "  2. edit_file(path=\"src/main.py\",\n"
    "       old_text=\"def old_function():\\n    return 1\",\n"
    "       new_text=\"def old_function():\\n    return 2\")\n"
    "  3. Kalau error: baca file lagi, copy-paste old_text yang benar\n\n"
    "CONTOH ALUR YANG SALAH:\n"
    "  - Langsung edit_file tanpa read_file → DITOLAK sistem\n"
    "  - old_text cuma 1 baris → error multiple matches\n\n"

    "KALO CODING:\n"
    "  - Tampilkan [RENCANA] struktur file dulu sebagai text\n"
    "  - Lalu write_file/file_edit sebagai tool_calls API (bukan teks)\n"
    "Contoh alur yang benar:\n"
    "  User: 'cek apakah mysql berjalan'\n"
    "  Salah: 'saya akan cek...' (berhenti)\n"
    "  Benar: run_command(\"mysqladmin ping\") → error → service_control(\"status\", \"mysql\") → "
    "run_command(\"mysqld_safe &\") → run_command(\"mysql -e 'SHOW DATABASES'\") → "
    "'Done! MySQL sudah aktif, berikut database-nya: ...'\n\n"
    "Tool yang tersedia:\n"
    "  - read_file / write_file / edit_file / search_code / list_dir / glob\n"
    "  - run_command (untuk APAPUN: psql, mongosh, apachectl, nginx, docker, git, apt, dsb. Tambahkan 'sudo ' (Linux/macOS) atau 'runas ' (Windows) di depan jika perintah butuh admin — contoh: 'sudo apt install', 'sudo systemctl restart nginx', 'runas net start mysql')\n"
    "  - db_query (mysql:// / postgres:// / mongodb:// / sqlite:/// / mssql:// / oracle:// / redis://)\n"
    "  - service_control (start/stop/restart/status)\n"
    "  - config_edit (edit + backup otomatis)\n"
    "  - package_check / web_fetch / web_search\n"
    "  - test_and_fix — jalanin script, kalo error balikin error biar bisa difix\n"
    "  - memory_store / memory_recall / memory_forget — memori jangka panjang lintas sesi\n"
    "  - screenshot — ambil screenshot layar untuk validasi visual\n"
    "  - port_scan — scan port terbuka pada target (reconnaissance)\n"
    "  - dns_enum — DNS record lookup + subdomain brute-force\n"
    "  - web_vuln_scan — cek security headers, SQLi, XSS, info server\n"
    "  - whois_lookup — cari informasi kepemilikan domain/IP\n"
    "  - ssl_check — periksa SSL/TLS certificate validity & cipher\n"
    "  - dir_bruteforce — temukan hidden paths pada web server\n"
    "  - cve_search — cari CVE berdasarkan software/service\n"
    "  - tech_detect — deteksi teknologi/stack website (framework, CMS, dsb)\n"
    "  - js_analyze — analisa JavaScript: ekstrak endpoint & hardcoded secrets\n"
    "  - api_discover — discover REST/GraphQL API endpoints dari HTML+JS\n"
    "  - source_map_check — cek eksposur source map (.map) untuk reverse engineering\n"
    "  - form_analyze — ekstrak form HTML (hidden fields, CSRF, input types)\n"
    "  - apk_analyze — analisa file APK Android (permissions, activities, manifest)\n"
    "  - binary_analyze — analisa file biner (type, strings, header, metadata)\n"
    "  - todo_create / todo_done / todo_show — buat dan kelola Rencana Pengerjaan\n\n"
    "MEMORI: Gunakan memory_store untuk menyimpan informasi penting (password, path, port, dsb.) "
    "dan memory_recall untuk mengambilnya kembali di sesi mendatang. "
    "Memori bersifat lintas sesi — apa yang disimpan hari ini bisa dipanggil besok.\n\n"
    "VALIDASI VISUAL: Setelah melakukan perubahan (misal deploy web, ganti konfigurasi), "
    "gunakan screenshot untuk mengambil bukti visual bahwa hasilnya sudah benar.\n\n"
    "AUTO-TEST & AUTO-FIX:\n"
    "  SETIAP kali Joki selesai write_file modul (Python/JS/TS/Shell/Ruby/Go/PHP), sistem akan OTOMATIS "
    "menjalankan test (python3 script.py, node script.js, dll).\n"
    "  Kalo test GAGAL, sistem akan kirim [AUTO-TEST] error ke chat dan minta diperbaiki. "
    "JANGAN BERHENTI — baca error, edit file yang bermasalah, dan sistem akan test ulang otomatis.\n"
    "  Ulangi sampai SUCCESS atau mentok 5 kali percobaan.\n\n"
    "AUTO-FIX LOOP (manual):\n"
    "  Kalo test_and_fix atau run_command return error, JANGAN BERHENTI. "
    "Baca error-nya, analisa, edit file yang bermasalah, test lagi. "
    "Ulangi sampai SUCCESS atau mentok 5 kali percobaan.\n"
    "  Contoh: test_and_fix(\"python3 script.py\") → FAILED → read_file(\"script.py\") "
    "→ edit_file(...) → test_and_fix(\"python3 script.py\") → SUCCESS"
)

def agent_loop(messages, extra=True):
    _tool_text_retries = 0
    MAX_ITERATIONS = 50
    _call_history = []

    # === LSP PRE-INJECT ===
    _lsp_injected = False
    if messages:
        last_content = messages[-1].get("content", "") if messages else ""
        if _is_error_query(last_content):
            _console.print("[dim]Mendeteksi permintaan perbaikan error — mengambil diagnostik LSP...[/dim]")
            try:
                project_dir = os.getcwd()
                diags = get_project_diagnostics(project_dir)
                if diags:
                    messages[-1]["content"] += f"\n\n{diags}"
                    _lsp_injected = True
                    _console.print(f"[dim]Diagnostik LSP berhasil dimuat.[/dim]")
            except Exception as e:
                _console.print(f"[dim]LSP pre-inject skip: {e}[/dim]")
    # === END LSP PRE-INJECT ===

    for i in range(MAX_ITERATIONS):
        if _joki_cancel.is_set():
            _console.print("[bold yellow]Dibatalkan oleh pengguna (Esc Esc).[/bold yellow]")
            return

        _console.rule("[bold cyan]JOKI[/bold cyan]")
        msg = call_llm(messages)
        messages.append(msg)

        if _joki_cancel.is_set():
            _console.print("[bold yellow]Dibatalkan oleh pengguna (Esc Esc).[/bold yellow]")
            return

        content = (msg.get("content") or "")
        if content.startswith("[CANCELLED]"):
            _console.print("[bold yellow]Dibatalkan oleh pengguna.[/bold yellow]")
            return

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                raw = tc["function"]["arguments"]
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    args = {"_raw": str(raw)}
                if not isinstance(args, dict):
                    args = {"_raw": str(args)}

                label = _TOOL_LABEL.get(name, name)
                if name == "run_command":
                    detail = args.get("cmd", "")
                elif name in ("read_file", "write_file", "edit_file", "list_dir", "config_edit"):
                    detail = args.get("path", "")
                elif name == "db_query":
                    detail = args.get("query", "")[:60]
                elif name == "web_search":
                    detail = args.get("query", "")
                elif name == "search_code":
                    detail = args.get("pattern", "")
                elif name == "service_control":
                    detail = f"{args.get('action')} {args.get('service')}"
                elif name == "package_check":
                    detail = args.get("app", "")
                elif name == "web_fetch":
                    detail = args.get("url", "")
                elif name == "test_and_fix":
                    detail = args.get("cmd", "")
                elif name in ("memory_store", "memory_recall", "memory_forget"):
                    detail = args.get("key", "")
                elif name == "screenshot":
                    detail = args.get("path", "(auto)")
                elif name == "port_scan":
                    detail = f"{args.get('target')} ports:{args.get('ports','common')}"
                elif name == "dns_enum":
                    detail = f"{args.get('domain')} {args.get('action','records')}"
                elif name == "web_vuln_scan":
                    detail = f"{args.get('url')} {args.get('checks','headers,info')}"
                elif name == "whois_lookup":
                    detail = args.get("target", "")
                elif name == "ssl_check":
                    detail = f"{args.get('host')}:{args.get('port',443)}"
                elif name == "dir_bruteforce":
                    detail = f"{args.get('url')} wordlist:{args.get('wordlist','small')}"
                elif name == "cve_search":
                    detail = args.get("query", "")
                elif name == "tech_detect":
                    detail = f"{args.get('url')} {args.get('deep','simple')}"
                elif name == "js_analyze":
                    detail = f"{args.get('url')} {args.get('extract','all')}"
                elif name == "api_discover":
                    detail = f"{args.get('url')} depth:{args.get('depth',2)}"
                elif name == "source_map_check":
                    detail = args.get("url", "")
                elif name == "form_analyze":
                    detail = args.get("url", "")
                elif name == "apk_analyze":
                    detail = args.get("path", "")
                elif name == "binary_analyze":
                    detail = args.get("path", "")
                elif name == "todo_create":
                    detail = f"{len(args.get('items', []))} items"
                elif name == "todo_done":
                    detail = f"item {args.get('indices', [])}"
                elif name == "todo_show":
                    detail = ""
                elif name in ("ui_screenshot", "ui_click", "ui_type", "ui_keypress", "ui_focus"):
                    detail = json.dumps(args)
                elif name == "usb_list":
                    detail = "USB devices"
                elif name == "serial_send":
                    detail = f"{args.get('port')}: {args.get('data','')[:60]}"
                elif name == "camera_capture":
                    detail = args.get("device", "/dev/video0")
                elif name == "sandbox_run":
                    detail = f"{args.get('interpreter','auto')} — {args.get('code','')[:80]}"
                elif name == "predict_command":
                    detail = args.get("cmd", "")[:80]
                elif name in ("audio_info", "audio_transcribe", "video_info", "video_extract"):
                    detail = args.get("path", "")
                else:
                    detail = json.dumps(args)
                _console.print(f"  [dim]\u2192 {label} {detail}[/dim]")

                if name == "write_file" and "content" in args:
                    lines = args["content"].splitlines(keepends=True)
                    digits = len(str(len(lines)))
                    for i, l in enumerate(lines):
                        print(f"      {i+1:>{digits}}: {l}", end="", flush=True)
                    if lines:
                        print()
                elif name == "edit_file":
                    ot = args.get("old_text", "")
                    nt = args.get("new_text", "")
                    if ot or nt:
                        _console.print(f"      [red]-: {ot[:80]}[/red]")
                        _console.print(f"      [green]+: {nt[:80]}[/green]")

                if _joki_cancel.is_set():
                    return

                try:
                    from joki.display import _Spinner
                    with _Spinner(f"Jalankan {name}"):
                        result = execute(name, args)
                except Exception as ex:
                    result = f"[ERROR] Exception saat mengeksekusi {name}: {ex}"
                if _joki_cancel.is_set():
                    return
                if result:
                    from joki.rich_display import print_tool_result_rich
                    print_tool_result_rich(name, args, result)
                messages.append({
                    "role": "tool",
                    "content": (result or "")[:50000],
                    "tool_call_id": tc["id"]
                })

                # === AUTO-TEST MODULE ===
                if name == "write_file" and not _joki_cancel.is_set():
                    path = args.get("path", "")
                    content = args.get("content", "")
                    ext = os.path.splitext(path)[1].lower()
                    base = os.path.basename(path)
                    _auto_test_needed = False

                    test_cfg = None
                    if ext == ".py" and ("if __name__" in content or content.strip().startswith("#!")):
                        test_cfg = ("python3", "python3")
                    elif ext == ".js":
                        test_cfg = ("node", "node")
                    elif ext == ".ts":
                        test_cfg = ("npx ts-node", "ts-node")
                    elif ext == ".sh" and content.strip().startswith("#!"):
                        test_cfg = ("bash", "bash")
                    elif ext == ".rb":
                        test_cfg = ("ruby", "ruby")
                    elif ext == ".go":
                        test_cfg = ("go run", "go")
                    elif ext == ".php":
                        test_cfg = ("php", "php")

                    if test_cfg:
                        test_cmd, _ = test_cfg
                        full_cmd = f"{test_cmd} {shlex.quote(path)}"

                        # Deteksi program interaktif/game/server — tidak cocok untuk auto-test kilat
                        _interactive_kw = ["pygame", "tkinter", "turtle", "curses",
                            "PyQt5", "PyQt6", "PySide", "gi.repository",
                            "flask", "fastapi", "bottle", "django", "aiohttp",
                            "sanic", "tornado", "uvicorn", "http.server",
                            "socketserver", "twisted", "matplotlib"]
                        _is_interactive = any(kw in content.lower() for kw in _interactive_kw)

                        if _is_interactive:
                            stream_print(f"       \u2728 Auto-test {base} dilewati \u2014 ini program interaktif/game/server yang berjalan terus-menerus")
                        else:
                            for attempt in range(5):
                                if _joki_cancel.is_set():
                                    break
                                with _Spinner(f"Auto-test {base} (percobaan {attempt+1}/5)"):
                                    rc, output, timed_out = _run_auto_test(full_cmd)
                                if rc == 0:
                                    stream_print(f"       \u2713 Auto-test {base} BERHASIL (percobaan {attempt+1})")
                                    break
                                elif timed_out:
                                    stream_print(f"       \u23F1 Auto-test {base} butuh waktu lebih lama \u2014 tapi program masih jalan, auto-test dilewati")
                                    stream_print(f"       \u2728 Program berjalan normal, hanya saja auto-test memang tidak cocok untuk program yang berjalan terus-menerus")
                                    break
                                else:
                                    stream_print(f"       \u2717 Auto-test {base} GAGAL (percobaan {attempt+1}/5)")
                                    stream_print(f"       ```\n{output[:3000]}\n       ```", delay=0.001)
                                    if attempt < 4:
                                        messages.append({
                                            "role": "user",
                                            "content": f"[AUTO-TEST] Modul {path} gagal test (percobaan {attempt+1}/5).\nPerintah: {full_cmd}\nError:\n{output[:4000]}\n\nPERBAIKI file ini sekarang dan jangan berhenti sampai test berhasil."
                                        })
                                        _auto_test_needed = True
                                        break
                                    else:
                                        stream_print(f"       Auto-test {base} GAGAL setelah 5 percobaan.")
                    if _auto_test_needed:
                        break

                # === STUCK DETECTION ===
                for tc in msg.get("tool_calls", []):
                    name = tc["function"]["name"]
                    raw = tc["function"]["arguments"]
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                    except (json.JSONDecodeError, TypeError):
                        args = {"_raw": str(raw)}
                    if not isinstance(args, dict):
                        args = {"_raw": str(args)}
                    # Buat hash args untuk deteksi duplikasi
                    sorted_args = sorted(args.items(), key=lambda x: x[0])
                    args_key = str([(k, str(v)[:80]) for k, v in sorted_args])
                    _call_history.append((name, args_key))

                if len(_call_history) >= 3:
                    last3 = _call_history[-3:]
                    if all(c[0] == last3[0][0] and c[1] == last3[0][1] for c in last3):
                        stream_print("[yellow]⚠ Terdeteksi loop: tool yang sama dipanggil 3x berturut-turut dengan argumen yang sama. Hentikan.[/yellow]")
                        break

        else:
            content = (msg.get("content") or "")
            if content and any(re.search(rf'\b{re.escape(name)}\s*\(', content) for name in _TOOL_FUNC_NAMES):
                _tool_text_retries += 1
                if _tool_text_retries <= 2:
                    messages.append({
                        "role": "user",
                        "content": "Jangan tulis tool sebagai teks. KIRIMKAN tool_calls yang SEBENARNYA — jangan ditulis manual."
                    })
                    continue
            if not content.strip():
                messages.append({
                    "role": "user",
                    "content": "Respons kamu kosong. Berikan respons atau panggil tool yang sesuai. Jangan diam saja — kerjakan task-nya."
                })
                continue
            if content:
                stream_print(content)
            _console.rule(style="dim")
            return
    stream_print(f"\n[INFO] Max iterations reached ({MAX_ITERATIONS}). Task mungkin terlalu kompleks. Gunakan task yang lebih spesifik atau ulangi dengan kata kunci 'lanjutkan'.")
    _console.rule(style="cyan")


# ============================================================
# SESSION MANAGEMENT
# ============================================================
LOG_DIR = os.path.join(_get_data_dir(), "logs")

def _build_system_prompt():
    base = _SYSTEM_PROMPT_BASE
    memories = _load_memory()
    if memories:
        items = "\n".join(f"  - {k}: {v[:120]}" for k, v in memories.items())
        base += f"\n\nMemori tersimpan ({len(memories)}):\n{items}\n\nGunakan memory_recall untuk detail, memory_store untuk menyimpan info baru."
    return base

def _check_update():
    try:
        joki_dir = os.path.dirname(os.path.abspath(__file__))
        local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=joki_dir, capture_output=True, text=True).stdout.strip()
        remote = subprocess.run(["git", "ls-remote", "origin", "HEAD"], cwd=joki_dir, capture_output=True, text=True).stdout.split()[0]
        if local and remote and local != remote:
            _console.print("[dim]Update tersedia! Jalankan: python joki.py --update[/dim]")
    except Exception:
        _console.print("[dim]Warning: Gagal check update (bukan di git repo)[/dim]")

def _run_auto_test(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout + r.stderr, False
    except subprocess.TimeoutExpired:
        return -1, "", True
    except Exception as e:
        return -1, str(e), False

def main():
    if "--version" in sys.argv:
        print(f"Joki v{__version__}")
        sys.exit(0)

    if "--update" in sys.argv:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run(["git", "pull", "origin", "main"])
        print("Updated! Restart Joki untuk menggunakan versi terbaru.")
        sys.exit(0)

    _load_plugins()
    _check_update()

    os.system("clear" if os.name == "posix" else "cls")
    global _CURRENT_SESSION, _current_model_config, _MODELS, _exhausted_keys
    _exhausted_keys.clear()
    args = sys.argv[1:]
    target_dir = None
    user_input = ""

    if args:
        first = os.path.expanduser(args[0])
        if os.path.isdir(first):
            target_dir = first
            os.chdir(first)
            rest = args[1:]
            user_input = " ".join(rest)
        else:
            user_input = " ".join(args)

    if target_dir:
        cwd = os.path.abspath(target_dir)
        _console.print(f"\n[cyan]\u2192 Working directory: {cwd}[/cyan]\n")

    if user_input:
        ts_name = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
        _CURRENT_SESSION = f"session_{ts_name}"
        messages = [{"role": "system", "content": _build_system_prompt()}]
        _console.rule("[bold yellow]USER[/bold yellow]")
        _console.print(Markdown(user_input))
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)
        save_session(messages)
        _console.print(f"[dim]Percakapan tersimpan: logs/{_CURRENT_SESSION}.log[/dim]")
        _close_shell()
        _cleanup_lsp()
        return

    ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
    _CURRENT_SESSION = f"session_{ts}"

    _console.print()
    _console.rule("[bold]JOKI[/bold]", style="cyan")
    _console.print(f"  Session: [cyan]{_CURRENT_SESSION}[/cyan]", style="dim")
    _console.print("  [bold]/model[/bold] — ganti model  |  [bold]/install-lsp[/bold] — install LSP  |  [bold]/sesi[/bold] [bold]/lihat[/bold] [bold]/baru[/bold] [bold]/keluar[/bold]", style="dim")
    _console.rule(style="dim")

    messages = [{"role": "system", "content": _build_system_prompt()}]

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    history_path = os.path.join(_get_data_dir(), "history")

    cmd_completer = WordCompleter(['/model', '/install-lsp', '/sesi', '/lihat', '/baru', '/keluar', '/sessions', '/view', '/new', '/exit', '/reload', '/reset_quota'])
    path_completer = PathCompleter()
    combined_completer = merge_completers([cmd_completer, path_completer])

    session = PromptSession(
        key_bindings=bindings,
        history=FileHistory(history_path),
        completer=combined_completer,
        bottom_toolbar=HTML('<gray>[Alt+Enter] atau [Esc+Enter] untuk baris baru</gray>')
    )

    while True:
        try:
            user_input = session.prompt(HTML('<cyan>joki</cyan><gray>></gray> '))
        except (EOFError, KeyboardInterrupt):
            print()
            save_session(messages)
            _console.print(f"[dim]Percakapan tersimpan: logs/{_CURRENT_SESSION}.log[/dim]")
            _close_shell()
            _cleanup_lsp()
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.strip().split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            now = __import__("datetime").datetime.now()

            if cmd in ("/sessions", "/sesi"):
                out, files = list_sessions()
                print(out)

            elif cmd in ("/view", "/lihat"):
                if not arg:
                    print("  Pemakaian: /lihat <nama_session_atau_nomor>")
                    print("  Gunakan /sesi untuk lihat daftar.")
                    continue
                if arg.isdigit():
                    out, files = list_sessions()
                    idx = int(arg) - 1
                    if 0 <= idx < len(files):
                        arg = files[idx].replace(".json", "")
                    else:
                        print(f"  Nomor tidak valid (1-{len(files)}).")
                        continue
                print(view_session_history(arg))

            elif cmd in ("/exit", "/quit", "/keluar"):
                save_session(messages)
                _console.print(f"[dim]Percakapan tersimpan: logs/{_CURRENT_SESSION}.log[/dim]")
                _close_shell()
                _cleanup_lsp()
                break

            elif cmd in ("/new", "/baru"):
                if len(messages) > 1:
                    save_session(messages)
                    _console.print(f"[dim]Previous session saved: logs/{_CURRENT_SESSION}.log[/dim]")
                _exhausted_keys.clear()
                ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
                _CURRENT_SESSION = f"session_{ts}"
                messages = [{"role": "system", "content": _build_system_prompt()}]
                _console.print(f"[cyan]New session started: {_CURRENT_SESSION}[/cyan]")

            elif cmd == "/model":
                sub = arg.strip().lower()
                if not sub:
                    mc = _current_model_config
                    keys = mc.get("api_keys") or [mc.get("api_key", "")]
                    total = len(keys)
                    exhausted = sum(1 for k in keys if k in _exhausted_keys)
                    active = total - exhausted
                    _console.print(f"[bold]Model aktif:[/bold] {mc['name']} ({mc['model']})")
                    _console.print(f"  Provider: {mc['provider']} | {mc['base_url']}")
                    _console.print(f"  API Keys: {active}/{total} available [red]({exhausted} exhausted)[/red]" if exhausted else f"  API Keys: {total}")
                    if mc.get("fallback"):
                        _console.print(f"  Fallback: {mc['fallback']} — {_MODELS[mc['fallback']]['name']}")
                    _console.print(f"[dim]Model tersedia (edit config.json untuk menambah):[/dim]")
                    for key, m in _MODELS.items():
                        marker = " [green]<-- aktif[/green]" if m["model"] == mc["model"] else ""
                        kcount = len(m.get("api_keys") or [m.get("api_key", "")])
                        key_info = f" ({kcount} keys)" if kcount > 1 else ""
                        _console.print(f"    /model {key}  — {m['name']} ({m['model']}){key_info}{marker}")
                    _console.print(f"  Config file: [underline]{_CONFIG_PATH}[/underline]")
                    continue
                if sub in _MODELS:
                    cfg = dict(_MODELS[sub])
                    keys = cfg.get("api_keys") or [cfg.get("api_key", "")]
                    if cfg.get("provider") in ("openai", "google") and not any(keys):
                        _console.print(f"[yellow]Peringatan: API key untuk {sub} kosong. Isi 'api_keys' di config.json[/yellow]")
                    _current_model_config = cfg
                    _console.print(f"[green]Model diganti: {cfg['name']} ({cfg['model']})[/green]")
                else:
                    matches = [k for k, v in _MODELS.items() if sub in k or sub in v["model"]]
                    if matches:
                        print(f"  Maksud Anda: {', '.join(f'/model {m}' for m in matches)}")
                    else:
                        print(f"  Model '{sub}' tidak dikenal. Lihat daftar: /model")

            elif cmd == "/reset_quota":
                _exhausted_keys.clear()
                _console.print(f"[green]Quota exhausted state direset. Semua API key dianggap available kembali.[/green]")

            elif cmd == "/reload":
                _MODELS = _load_models()
                default_model = next((v for v in _MODELS.values() if v.get("default")), next(iter(_MODELS.values())))
                _current_model_config = dict(default_model)
                _console.print(f"[green]Config reloaded dari {_CONFIG_PATH} ({len(_MODELS)} model)[/green]")

            elif cmd == "/install-lsp":
                handle_install_lsp_command(arg)

            else:
                print(f"  Unknown command: {cmd}")
            continue

        _console.rule("[bold yellow]USER[/bold yellow]")
        _console.print(Markdown(user_input))
        messages.append({"role": "user", "content": user_input})
        agent_loop(messages)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDibatalkan.")
