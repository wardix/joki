#!/usr/bin/env python3
"""
Joki — AI agentic CLI.
Bisa akses file, shell, database (MySQL/PostgreSQL/MongoDB), service systemd, konfigurasi aplikasi.
Jalankan: python joki.py "task"
   atau: python joki.py (mode interaktif)
   atau: python joki.py /path/ke/folder "task" (langsung masuk folder)
"""

import json, httpx, sys, subprocess, os, re, shutil, shlex, time, threading, socket, ssl, select, getpass
try:
    import termios, tty
    _HAS_TTY = True
except ImportError:
    _HAS_TTY = False
from pathlib import Path
from difflib import unified_diff
from datetime import datetime
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich import box
from duckduckgo_search import DDGS
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

# ============================================================
# CONFIG
# ============================================================
BACKUP_DIR = "/tmp/agent_backups"
_CURRENT_SESSION = None
_joki_cancel = threading.Event()
_exhausted_keys = set()  # api key strings yang quota-nya habis (di-reset tiap sesi)
_SUDO_PASSWORD = None    # cached sudo password (di-reset tiap sesi)
_PERSISTENT_SHELL = None # persistent shell process
_SHELL_LOCK = threading.Lock()

# ============================================================
# PERSISTENT SHELL SESSION
# ============================================================
def _get_shell():
    """Start or return the persistent shell process (bash)."""
    global _PERSISTENT_SHELL
    if _PERSISTENT_SHELL is not None:
        poll = _PERSISTENT_SHELL.poll()
        if poll is None:
            return _PERSISTENT_SHELL
        _PERSISTENT_SHELL = None
    try:
        _PERSISTENT_SHELL = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0
        )
    except FileNotFoundError:
        try:
            _PERSISTENT_SHELL = subprocess.Popen(
                ["sh"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )
        except Exception:
            return None
    return _PERSISTENT_SHELL

def _close_shell():
    """Kill the persistent shell process if running."""
    global _PERSISTENT_SHELL
    if _PERSISTENT_SHELL is not None:
        try:
            _PERSISTENT_SHELL.terminate()
            _PERSISTENT_SHELL.wait(timeout=3)
        except Exception:
            try:
                _PERSISTENT_SHELL.kill()
            except Exception:
                pass
        _PERSISTENT_SHELL = None

def _shell_execute(cmd, timeout=60):
    """Execute command in the persistent shell.
    Returns (stdout+stderr) string.
    """
    shell = _get_shell()
    if shell is None:
        return "[ERROR] Tidak bisa memulai persistent shell."

    end_marker = f"__SHELL_END_{os.getpid()}_{time.time_ns()}__"

    full_cmd = f" ( {cmd} ) 2>&1; echo '{end_marker}'"

    with _SHELL_LOCK:
        try:
            shell.stdin.write(full_cmd + "\n")
            shell.stdin.flush()
        except Exception as e:
            _close_shell()
            return f"[ERROR] Gagal menulis ke shell: {e}"

        output = []
        start = time.time()
        while True:
            if _joki_cancel.is_set():
                return "[CANCELLED]"
            elapsed = time.time() - start
            if elapsed > timeout:
                _close_shell()
                return f"[ERROR] Command timeout ({timeout}s). Shell di-restart."
            try:
                line = shell.stdout.readline()
                if not line:
                    _close_shell()
                    return "[ERROR] Shell process mati."
                if line.strip() == end_marker:
                    break
                output.append(line)
            except (Exception, KeyboardInterrupt):
                _close_shell()
                return "[ERROR] Gagal membaca output shell."

    return "".join(output).rstrip("\n")

# ============================================================
# MULTI-MODEL SUPPORT
# ============================================================
def _get_data_dir():
    """Return stable data directory: ~/.local/share/joki/"""
    return os.path.join(os.path.expanduser("~"), ".local", "share", "joki")

def _get_config_path():
    """Return config path: same directory as joki.py"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_CONFIG_PATH = Path(_get_config_path())

_DEFAULT_MODELS = {
    "gemma4": {
        "name": "Gemma 4 (31B lokal)",
        "base_url": "http://localhost:11434",
        "model": "gemma4:31b",
        "api_keys": [""],
        "provider": "ollama",
        "fallback": "",
    },
    "deepseek": {
        "name": "DeepSeek V4 Flash",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key": "",
        "provider": "openai",
    },
}

def _load_models():
    """Load model configs from config.json, fallback to _DEFAULT_MODELS.

    Normalizes each model so it always has an `api_keys` list
    (migrates legacy `api_key` string into the list).
    Auto-create config.json with template if not exists.
    """
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            models = data.get("models", {})
            if models:
                for k, m in models.items():
                    if "api_keys" not in m or not isinstance(m["api_keys"], list):
                        old = m.pop("api_key", "")
                        m["api_keys"] = [old] if old else []
                    
                    if not m.get("api_keys") or not m["api_keys"][0]:
                        env_key = f"JOKI_{k.upper()}_KEY"
                        if "openrouter" in m.get("base_url", "").lower():
                            env_key = "JOKI_OPENROUTER_KEY"
                        val = os.environ.get(env_key, "")
                        if val:
                            m["api_keys"] = [val]
                return models
        except Exception:
            pass
    raw = dict(_DEFAULT_MODELS)
    for k, m in raw.items():
        if "api_keys" not in m or not isinstance(m["api_keys"], list):
            old = m.pop("api_key", "")
            m["api_keys"] = [old] if old else []
            
        if not m.get("api_keys") or not m["api_keys"][0]:
            env_key = f"JOKI_{k.upper()}_KEY"
            if "openrouter" in m.get("base_url", "").lower():
                env_key = "JOKI_OPENROUTER_KEY"
            val = os.environ.get(env_key, "")
            if val:
                m["api_keys"] = [val]
                
    _auto_create_config()
    return raw

def _auto_create_config():
    """Create ~/.config/joki/config.json with template if it doesn't exist."""
    try:
        template = {
            "models": {
                "gemma4": {
                    "name": "Gemma 4 (31B Cloud)",
                    "base_url": "https://ollama.com/v1",
                    "model": "gemma4:31b-cloud",
                    "api_keys": [""],
                    "provider": "openai",
                    "fallback": "gemini",
                    "default": True
                },
                "gemini": {
                    "name": "Gemini 3 Flash Preview (OpenRouter)",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "google/gemini-3-flash-preview",
                    "api_keys": [""],
                    "provider": "openai",
                    "default": False
                }
            }
        }
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(template, indent=2))
    except Exception:
        pass

_MODELS = _load_models()

default_model = next((v for v in _MODELS.values() if v.get("default")), next(iter(_MODELS.values())))
_current_model_config = dict(default_model)

# ============================================================
# TOOL DEFINITIONS
# ============================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read content of an existing file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute file path"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "Full file content"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Search and replace text in an existing file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact text to find"},
                    "new_text": {"type": "string", "description": "Replacement text"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run any shell command. Gunakan ini untuk: psql, mongosh, apachectl, nginx, docker, git, apt, systemctl, dsb. PENTING: untuk perintah yang butuh admin/root, WAJIB tambahkan prefix 'sudo ' (Linux/macOS) atau 'runas ' (Windows). Contoh: 'sudo apt update', 'sudo systemctl restart nginx', 'runas net start mysql'.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string", "description": "Shell command"}},
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for pattern across files (regex supported)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search (default: current)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories inside a path",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "Execute query terhadap database. Auto-detect jenis database dari connection string. Support: mysql, postgres, mongodb, sqlite, mssql, oracle, redis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL/NoSQL query"},
                    "connection": {
                        "type": "string",
                        "description": "Connection string. Contoh:\n  mysql://root@localhost/mydb\n  postgres://user:pass@localhost:5432/db\n  mongodb://localhost:27017/mydb\n  sqlite:///path/to/file.db\n  mssql://sa:pass@host:1433/db\n  oracle://user:pass@host:1521/servicename\n  redis://localhost:6379"
                    }
                },
                "required": ["query", "connection"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "service_control",
            "description": "Manage systemd service: start, stop, restart, enable, disable, status",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "restart", "reload", "enable", "disable", "status"],
                        "description": "Tindakan terhadap service"
                    },
                    "service": {"type": "string", "description": "Nama service (contoh: apache2, nginx, postgresql, mysql, mongod)"}
                },
                "required": ["action", "service"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "config_edit",
            "description": "Edit konfigurasi aplikasi. Backup otomatis sebelum mengubah. Bisa edit file konfigurasi Apache, Nginx, SSH, dsb.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path ke file konfigurasi"},
                    "directive": {"type": "string", "description": "Directive/key yang mau dicari (opsional). Contoh: 'ServerName', 'listen', 'max_connections'"},
                    "set_value": {"type": "string", "description": "Nilai baru. Kosongkan untuk hanya baca nilai saat ini."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "package_check",
            "description": "Check apakah suatu aplikasi/command terinstall di system",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "Nama aplikasi atau command. Contoh: psql, mongosh, apache2, nginx, docker, node, python3"}
                },
                "required": ["app"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch content from a URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Complete URL (https://...)"},
                    "format": {"type": "string", "enum": ["markdown", "text"], "description": "Output format (default: markdown)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web menggunakan DuckDuckGo. Dapatkan informasi terkini dari internet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Jumlah hasil maksimal (default: 5)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "test_and_fix",
            "description": "Jalankan command, kalo gagal auto-fix dengan baca error dan coba perbaiki. Gunakan untuk testing script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run"},
                    "file_to_fix": {"type": "string", "description": "Path file yang perlu diperbaiki kalo error (opsional)"}
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_store",
            "description": "Simpan informasi penting ke memori jangka panjang (lintas sesi). Contoh: password database, path config, port service, dsb.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Nama unik untuk informasi ini"},
                    "value": {"type": "string", "description": "Informasi yang ingin disimpan"}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": "Ambil informasi yang tersimpan di memori jangka panjang. Biarkan key kosong untuk lihat semua memori.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Nama informasi yang ingin diambil (opsional)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": "Hapus informasi dari memori jangka panjang.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Nama informasi yang ingin dihapus"}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Ambil screenshot layar penuh. Gunakan untuk validasi visual hasil kerja (cek tampilan web, error visual, dsb.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path untuk menyimpan screenshot (opsional, default: /tmp/joki_screenshot_<timestamp>.png)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "port_scan",
            "description": "Scan port terbuka pada target. Gunakan untuk penetration testing — cek service apa saja yang berjalan, deteksi port tidak aman yang terbuka.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address atau hostname target (contoh: 192.168.1.1, scanme.nmap.org)"},
                    "ports": {"type": "string", "description": "Range port (contoh: '22,80,443', '1-1000', 'common'). Default: common ports (1-1024 + service umum)"},
                    "scan_type": {"type": "string", "enum": ["tcp", "syn", "udp", "quick"], "description": "Tipe scan. 'tcp'=TCP connect, 'syn'=SYN stealth (butuh root), 'quick'=port terkenal saja. Default: quick"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dns_enum",
            "description": "DNS enumeration: lookup A, AAAA, MX, NS, TXT, CNAME records, dan subdomain brute-force. Untuk penetration testing dan reconnaissance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain target (contoh: example.com)"},
                    "action": {"type": "string", "enum": ["records", "subdomains", "all"], "description": "'records'=DNS records standar, 'subdomains'=bruteforce subdomain, 'all'=keduanya. Default: records"}
                },
                "required": ["domain"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_vuln_scan",
            "description": "Web vulnerability scan: cek security headers, SQL injection (basic), XSS refleksi, directory traversal, informasi server. Untuk penetration testing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target lengkap (contoh: https://example.com)"},
                    "checks": {"type": "string", "description": "Jenis cek: 'headers' (security headers), 'sqli' (SQL injection basic), 'xss' (XSS refleksi), 'info' (informasi server), 'all'. Default: headers,info"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "whois_lookup",
            "description": "WHOIS lookup untuk mendapatkan informasi kepemilikan domain, registrar, tanggal registrasi/expired, name server. Untuk reconnaissance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Domain atau IP target (contoh: example.com, 8.8.8.8)"}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssl_check",
            "description": "Periksa SSL/TLS certificate: validitas, issuer, expiry, cipher, protocol version, sertifikat chain. Deteksi misconfiguration dan vulnerability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Hostname target (contoh: example.com)"},
                    "port": {"type": "integer", "description": "Port (default: 443)"}
                },
                "required": ["host"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dir_bruteforce",
            "description": "Bruteforce directory/file pada web server menggunakan wordlist. Temukan hidden paths, admin panel, backup file, dsb. Untuk penetration testing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL (contoh: https://example.com)"},
                    "wordlist": {"type": "string", "description": "Path ke wordlist atau ukuran wordlist: 'small' (100 paths), 'medium' (1000), 'large' (5000). Default: small"},
                    "extensions": {"type": "string", "description": "Ekstensi file yang dicari (contoh: 'php,txt,zip,bak'). Default: tidak ada"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cve_search",
            "description": "Cari CVE (Common Vulnerabilities and Exposures) berdasarkan software/service. Dapatkan info kerentanan yang diketahui untuk target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword pencarian (contoh: 'apache 2.4.49', 'nginx 1.20', 'openssh 8.9')"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tech_detect",
            "description": "Deteksi technology stack website: framework, CMS, web server, library, analytics, CDN. Analisa header HTTP, HTML meta, cookie, dan URL pattern. Untuk reverse engineering dan footprinting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target lengkap (contoh: https://example.com)"},
                    "deep": {"type": "string", "enum": ["simple", "deep"], "description": "'simple'=header+cookie saja, 'deep'=analisa HTML+JS juga. Default: simple"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "js_analyze",
            "description": "Analisa JavaScript file: ekstrak endpoint API, keyword sensitif, hardcoded credentials, domain, fungsi tersembunyi. Untuk reverse engineering aplikasi web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL langsung ke file JS, atau URL halaman web (akan cari semua <script src=...>)"},
                    "extract": {"type": "string", "enum": ["endpoints", "secrets", "all"], "description": "'endpoints'=API path/URL saja, 'secrets'=key/token/password, 'all'=keduanya. Default: all"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "api_discover",
            "description": "Discover API endpoints dari halaman web: analisa fetch/XHR calls, form actions, link patterns, dan common API path patterns. Untuk reverse engineering REST/GraphQL API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target (contoh: https://example.com)"},
                    "depth": {"type": "integer", "description": "Kedalaman: 1=halaman utama saja, 2=+JS files, 3=+subpages. Default: 2"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "source_map_check",
            "description": "Periksa apakah web server mengekspos source map (.map) file. Source map bisa membuka kode sumber asli (minified → original) untuk reverse engineering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target (contoh: https://example.com)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "form_analyze",
            "description": "Ekstrak dan analisa form HTML: field tersembunyi, CSRF token, autocomplete, input type, action endpoint. Untuk reverse engineering alur aplikasi web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target (contoh: https://example.com/login)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apk_analyze",
            "description": "Analisa file APK Android: package name, version, permissions, activities, services, receivers, providers. Untuk reverse engineering aplikasi mobile.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file .apk"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "binary_analyze",
            "description": "Analisa file biner/executable: deteksi file type, arsitektur, strings menarik, metadata. Untuk reverse engineering aplikasi native.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file biner"},
                    "strings_min": {"type": "integer", "description": "Minimum string length untuk ekstraksi strings (default: 6)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_create",
            "description": "Buat TODO list untuk task yang akan dikerjakan. Panggil di awal sebelum mulai mengerjakan sesuatu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Daftar item TODO (masing-masing berupa string langkah)"
                    }
                },
                "required": ["items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_done",
            "description": "Tandai satu atau lebih item TODO sebagai selesai.",
            "parameters": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Nomor index item yang selesai (1-based)"
                    }
                },
                "required": ["indices"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_show",
            "description": "Tampilkan TODO list saat ini.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_screenshot",
            "description": "Ambil screenshot layar penuh atau area tertentu. Gunakan xdotool + import (ImageMagick).",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "Area screenshot: 'full' (layar penuh), atau 'x,y,w,h' (area spesifik). Default: full"
                    },
                    "path": {
                        "type": "string",
                        "description": "Path untuk menyimpan screenshot (default: /tmp/joki_ui_screen.png)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_click",
            "description": "Klik mouse di koordinat layar tertentu atau klik kiri/kanan. Gunakan xdotool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Koordinat X"},
                    "y": {"type": "integer", "description": "Koordinat Y"},
                    "button": {
                        "type": "string",
                        "enum": ["left", "middle", "right"],
                        "description": "Tombol mouse (default: left)"
                    },
                    "click_count": {
                        "type": "integer",
                        "description": "Jumlah klik (1=single, 2=double). Default: 1"
                    }
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_type",
            "description": "Ketik teks di elemen yang sedang aktif/fokus. Gunakan xdotool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Teks yang akan diketik"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_keypress",
            "description": "Tekan tombol keyboard atau kombinasi. Contoh: 'Return', 'ctrl+c', 'alt+F4', 'Super+d'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Tombol atau kombinasi tombol. Contoh: 'Return', 'ctrl+c', 'alt+F4', 'Super+d', 'Tab'"}
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_focus",
            "description": "Fokuskan window berdasarkan title. Cari window yang title-nya mengandung teks tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Judul window (akan dicocokkan sebagian/substring)"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "usb_list",
            "description": "Daftar semua perangkat USB yang terhubung ke sistem. Gunakan lsusb.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verbose": {
                        "type": "boolean",
                        "description": "Tampilkan detail verbose (default: false)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serial_send",
            "description": "Kirim data ke port serial/USB. Gunakan untuk komunikasi dengan Arduino, modem, atau perangkat serial lainnya.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "Port serial (contoh: /dev/ttyUSB0, /dev/ttyACM0, COM3)"},
                    "data": {"type": "string", "description": "Data yang akan dikirim"},
                    "baud": {"type": "integer", "description": "Baud rate (default: 9600)"},
                    "read_timeout": {"type": "number", "description": "Timeout baca response dalam detik (default: 2)"}
                },
                "required": ["port", "data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "camera_capture",
            "description": "Ambil gambar dari webcam/kamera. Gunakan fswebcam atau ffmpeg.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string", "description": "Device kamera (default: /dev/video0)"},
                    "path": {"type": "string", "description": "Path output (default: /tmp/joki_cam.jpg)"},
                    "resolution": {"type": "string", "description": "Resolusi (contoh: 640x480). Default: 640x480"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_run",
            "description": "Jalankan perintah/kode dalam lingkungan terisolasi (sandbox). Berguna untuk testing kode yang berpotensi berbahaya. Otomatis pindah ke direktori temp dan timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Perintah atau kode yang akan dijalankan di sandbox"},
                    "interpreter": {
                        "type": "string",
                        "enum": ["auto", "bash", "python3", "node", "sh"],
                        "description": "Interpreter yang digunakan. 'auto' akan mendeteksi dari shebang atau ekstensi. Default: auto"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout dalam detik (default: 15, max: 60)"
                    },
                    "files": {
                        "type": "string",
                        "description": "File pendukung yang perlu dibuat di sandbox (format: 'path1=content1|path2=content2')"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "predict_command",
            "description": "Analisa sebuah perintah shell dan prediksi efek/dampaknya tanpa menjalankan. Cek: write/delete/dangerous patterns, package install, network, service changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Perintah shell yang akan dianalisa"}
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "audio_info",
            "description": "Ambil metadata file audio: duration, codec, sample rate, channels, bitrate. Gunakan ffprobe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file audio (mp3, wav, flac, ogg, m4a, dsb)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "audio_transcribe",
            "description": "Transkripsi audio ke teks. Gunakan whisper (openai-whisper) atau speech_recognition. Deteksi bahasa otomatis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file audio (mp3, wav, m4a, dsb)"},
                    "model": {
                        "type": "string",
                        "enum": ["tiny", "base", "small", "medium", "large"],
                        "description": "Ukuran model whisper. 'tiny'=cepat, 'large'=akurat. Default: base"
                    },
                    "language": {
                        "type": "string",
                        "description": "Kode bahasa (default: auto-detect). Contoh: 'id' untuk Indonesia, 'en' untuk Inggris"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "video_info",
            "description": "Ambil metadata file video: duration, codec, resolution, fps, bitrate, streams. Gunakan ffprobe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file video (mp4, avi, mkv, mov, dsb)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "video_extract",
            "description": "Ekstrak frame/thumbnail dari file video. Bisa extract frame per detik, per interval, atau screenshot di timestamp tertentu. Gunakan ffmpeg.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file video"},
                    "mode": {
                        "type": "string",
                        "enum": ["thumbnail", "frames", "timestamp"],
                        "description": "'thumbnail'=1 frame的代表, 'frames'=ekstrak per detik, 'timestamp'=frame di detik tertentu"
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Direktori output (default: /tmp/joki_video_extract/)"
                    },
                    "timestamp": {
                        "type": "number",
                        "description": "Timestamp dalam detik (hanya untuk mode 'timestamp')"
                    },
                    "fps": {
                        "type": "number",
                        "description": "Frame per detik untuk mode 'frames' (default: 1)"
                    }
                },
                "required": ["path", "mode"]
            }
        }
    }
]

# ============================================================
# HELPER: auto-detect database type & execute
# ============================================================
def _parse_connection(conn_str):
    """Parse connection string: mysql://..., postgres://..., mongodb://..., sqlite:///..."""
    sqlite_match = re.match(r"sqlite:///(.+)", conn_str)
    if sqlite_match:
        return ("sqlite", "", "", "", "", sqlite_match.group(1))

    match = re.match(r"(\w+)://(?:([^:@]+)(?::([^@]+))?@)?([^:/]+)(?::(\d+))?(?:/(.+))?", conn_str)
    if not match:
        raise ValueError(f"Invalid connection string: {conn_str}")
    scheme, user, password, host, port, database = match.groups()
    return scheme, user or "root", password or "", host or "localhost", port, database or ""

def _run_db_query(scheme, query, user, password, host, port, database):
    scheme = scheme.lower()

    if scheme in ("mysql", "mariadb"):
        env = os.environ.copy()
        if password:
            env["MYSQL_PWD"] = password
        cmd = ["mysql", f"-u{user}"]
        if password:
            cmd.append(f"-p{password}")
        cmd.extend([f"-h{host}", f"-P{port or 3306}", database, "-e", query])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        return r.stdout or r.stderr

    elif scheme in ("postgres", "postgresql", "pgsql"):
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        cmd = ["psql", f"-h{host}", f"-p{port or 5432}", f"-U{user}", f"-d{database}", "-c", query, "-t"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        return r.stdout or r.stderr

    elif scheme == "mongodb":
        cmd = ["mongosh", f"{host}:{port or 27017}/{database}"]
        if user:
            cmd.extend(["-u", user, "-p", password, "--authenticationDatabase", "admin"])
        cmd.extend(["--quiet", "--eval", query])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme == "sqlite":
        if not os.path.isfile(database):
            database = os.path.expanduser(database)
        cmd = ["sqlite3", database, query]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme in ("mssql", "sqlserver"):
        cmd = ["sqlcmd", "-S", f"{host},{port or 1433}", "-U", user]
        if password:
            cmd.extend(["-P", password])
        else:
            cmd.append("-E")
        cmd.extend(["-d", database, "-Q", query, "-W"])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme in ("oracle", "oracledb"):
        pw_part = f"{user}/{password}@" if password else f"{user}/"
        cmd = ["sqlplus", "-S", f"{pw_part}{host}:{port or 1521}/{database}"]
        r = subprocess.run(cmd, input=query, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    elif scheme == "redis":
        cmd = ["redis-cli", "-h", host, "-p", str(port or 6379)]
        if password:
            cmd.extend(["-a", password])
        cmd.extend(shlex.split(query))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr

    else:
        dbs = "mysql, postgres, mongodb, sqlite, mssql, oracle, redis"
        return f"Unsupported database: {scheme}. Supported: {dbs}."

# ============================================================
# LONG-TERM MEMORY (per-session)
# ============================================================
def _memory_path(name=None):
    name = name or _CURRENT_SESSION or "default"
    return os.path.join(SESSION_DIR, "memories", f"{name}.json")

def _load_memory(name=None):
    path = _memory_path(name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _save_memory(data, name=None):
    path = _memory_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ============================================================
# TODO LIST
# ============================================================
def _todo_path():
    return os.path.join(SESSION_DIR, "todos", f"{_CURRENT_SESSION or 'default'}.json")

def _load_todo():
    path = _todo_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def _save_todo(items):
    path = _todo_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(items, f, indent=2)

# ============================================================
# TOOL EXECUTOR
# ============================================================
def _numbered(text):
    lines = text.splitlines(keepends=True)
    digits = len(str(len(lines)))
    return "".join(f"{i+1:>{digits}}: {l}" for i, l in enumerate(lines))

def _is_admin():
    """Check if current process has admin/root privileges."""
    if os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            return False
    else:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return True

def _prompt_sudo():
    """Prompt user for admin password and cache it for the session.
    Returns the password string, or '__ROOT__' if already admin, or None on cancel.
    """
    global _SUDO_PASSWORD
    if _SUDO_PASSWORD is not None:
        return _SUDO_PASSWORD

    if _is_admin():
        _SUDO_PASSWORD = "__ROOT__"
        return _SUDO_PASSWORD

    try:
        _console.print()
        if os.name == 'nt':
            _console.print("[yellow]Autentikasi administrator Windows diperlukan:[/yellow]")
            _SUDO_PASSWORD = getpass.getpass("  Password Administrator: ")
            r = subprocess.run(
                f'runas /user:Administrator "cmd /c echo authenticated" 2>&1',
                shell=True, input=_SUDO_PASSWORD + "\n",
                capture_output=True, text=True, timeout=10
            )
            err_upper = (r.stdout + r.stderr).upper()
            if "LOGON FAILURE" in err_upper or "1326" in err_upper or "PASSWORD OR USERNAME" in err_upper:
                _console.print("[red]  Password salah![/red]")
                _SUDO_PASSWORD = None
                return _prompt_sudo()
            _console.print("[green]  Autentikasi berhasil.[/green]")
        else:
            _console.print("[yellow]Autentikasi administrator (sudo) diperlukan:[/yellow]")
            _SUDO_PASSWORD = getpass.getpass("  Password: ")
            r = subprocess.run(
                ["sudo", "-S", "-v"],
                input=_SUDO_PASSWORD + "\n",
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                _console.print("[red]  Password salah![/red]")
                _SUDO_PASSWORD = None
                return _prompt_sudo()
            _console.print("[green]  Autentikasi berhasil.[/green]")
        return _SUDO_PASSWORD
    except (EOFError, KeyboardInterrupt):
        _console.print("\n[yellow]  Autentikasi dibatalkan.[/yellow]")
        _SUDO_PASSWORD = None
        return None
    except Exception:
        _SUDO_PASSWORD = None
        return None

def _run_elevated(cmd, password):
    """Run command with admin/root privileges using cached password."""
    if os.name == 'nt':
        if password == "__ROOT__":
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        else:
            return subprocess.run(
                f'runas /user:Administrator "cmd /c {cmd}"',
                shell=True, input=password + "\n",
                capture_output=True, text=True, timeout=60
            )
    else:
        return subprocess.run(
            f"sudo -S {cmd}",
            shell=True, input=password + "\n",
            capture_output=True, text=True, timeout=60
        )

def execute(name, args):
    try:
        if name == "read_file":
            with open(args["path"]) as f:
                return _numbered(f.read())

        elif name == "write_file":
            path = args["path"]
            new = args["content"]
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            diff_str = ""
            if os.path.exists(path):
                with open(path) as f:
                    old = f.read()
                if old != new:
                    diff = unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=path, tofile=path)
                    diff_str = "".join(diff)
            with open(path, "w") as f:
                f.write(new)
            msg = f"Written: {path} ({len(new)} bytes)"
            if diff_str:
                msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
            return msg

        elif name == "edit_file":
            with open(args["path"]) as f:
                old = f.read()
            ot = args["old_text"]
            if not ot:
                new = args["new_text"] + old
            else:
                if ot not in old:
                    return f"Error: 'old_text' not found in {args['path']}"
                new = old.replace(ot, args["new_text"])
            diff = unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=args["path"], tofile=args["path"])
            with open(args["path"], "w") as f:
                f.write(new)
            msg = f"Edited: {args['path']}"
            diff_str = "".join(diff)
            if diff_str:
                msg += f"\n--- DIFF ---\n{diff_str}--- END DIFF ---"
            return msg

        elif name == "run_command":
            cmd = args["cmd"].strip()
            sudo_password = None
            actual_cmd = cmd
            use_sudo = False

            if cmd.startswith("sudo ") or (os.name == 'nt' and cmd.startswith("runas ")):
                use_sudo = True
                sudo_password = _prompt_sudo()
                if sudo_password:
                    prefix = "sudo " if cmd.startswith("sudo ") else "runas "
                    actual_cmd = cmd[len(prefix):].lstrip()

            if use_sudo and sudo_password:
                with _Spinner("Menjalankan perintah"):
                    result = _run_elevated(actual_cmd, sudo_password)
                output = result.stdout + result.stderr
                return output or "(no output)"
            else:
                with _Spinner("Menjalankan perintah"):
                    output = _shell_execute(cmd)
                    return output or "(no output)"

        elif name == "search_code":
            cmd = ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
                   "--include=*.html", "--include=*.css", "--include=*.json",
                   "--include=*.yaml", "--include=*.yml", "--include=*.md",
                   "--include=*.conf", "--include=*.cfg", "--include=*.ini",
                   args["pattern"], args.get("path", ".")]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout or "(not found)"

        elif name == "list_dir":
            items = os.listdir(args["path"])
            lines = []
            for item in sorted(items):
                full = os.path.join(args["path"], item)
                label = "DIR" if os.path.isdir(full) else "   "
                lines.append(f"{label} {item}")
            return "\n".join(lines)

        elif name == "db_query":
            scheme, user, password, host, port, database = _parse_connection(args["connection"])
            with _Spinner("Query database"):
                return _run_db_query(scheme, args["query"], user, password, host, port, database)

        elif name == "service_control":
            svc = args["service"]
            act = args["action"]
            is_macos = sys.platform == 'darwin'
            if act == "status":
                with _Spinner(f"{act} {svc}"):
                    if os.name == 'nt':
                        r = subprocess.run(
                            f"sc query {svc}", shell=True,
                            capture_output=True, text=True, timeout=30
                        )
                    elif is_macos:
                        r = subprocess.run(
                            f"launchctl list | grep -i {svc} || launchctl print system/{svc} 2>/dev/null || echo 'Service {svc} tidak ditemukan'",
                            shell=True, capture_output=True, text=True, timeout=30
                        )
                    else:
                        r = subprocess.run(
                            f"systemctl status {svc} --no-pager -l", shell=True,
                            capture_output=True, text=True, timeout=30
                        )
            else:
                sudo_password = _prompt_sudo()
                if os.name == 'nt':
                    actual_cmd = f"net {act} {svc}"
                elif is_macos:
                    if act == "enable":
                        actual_cmd = f"launchctl load -w /System/Library/LaunchDaemons/{svc}.plist 2>/dev/null || launchctl enable system/{svc}"
                    elif act == "disable":
                        actual_cmd = f"launchctl unload -w /System/Library/LaunchDaemons/{svc}.plist 2>/dev/null || launchctl disable system/{svc}"
                    elif act == "restart":
                        actual_cmd = f"launchctl kickstart -k system/{svc} 2>/dev/null || (launchctl stop {svc} 2>/dev/null; sleep 1; launchctl start {svc} 2>/dev/null)"
                    else:
                        actual_cmd = f"launchctl {act} {svc}"
                else:
                    actual_cmd = f"systemctl {act} {svc}"
                if sudo_password:
                    with _Spinner(f"{act} {svc}"):
                        r = _run_elevated(actual_cmd, sudo_password)
                else:
                    cmd = f"sudo {actual_cmd}" if sudo_password != "__ROOT__" else actual_cmd
                    with _Spinner(f"{act} {svc}"):
                        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return (r.stdout or r.stderr).strip() or f"OK: {act} {svc}"

        elif name == "config_edit":
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

        elif name == "package_check":
            app = args["app"]
            # Check via which, dpkg, rpm, etc.
            checks = [
                f"which {app} 2>/dev/null",
                f"command -v {app} 2>/dev/null",
                f"dpkg -l {app} 2>/dev/null | grep '^ii'",
                f"rpm -q {app} 2>/dev/null"
            ]
            for c in checks:
                r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=5)
                if r.stdout.strip():
                    return f"INSTALLED: {r.stdout.strip()}"
            return f"NOT INSTALLED: {app} tidak ditemukan di system"

        elif name == "web_fetch":
            with _Spinner("Mengambil konten web"):
                r = httpx.get(args["url"], timeout=30, follow_redirects=True)
                r.raise_for_status()
            return r.text

        elif name == "web_search":
            with _Spinner("Mencari di web"):
                results = DDGS().text(args["query"], max_results=args.get("max_results", 5))
            if not results:
                return "(no results)"
            lines = []
            for r in results:
                lines.append(f"- {r['title']}\n  {r['href']}\n  {r['body']}")
            return "\n\n".join(lines)

        elif name == "test_and_fix":
            try:
                with _Spinner("Mengetes"):
                    r = subprocess.run(args["cmd"], shell=True, capture_output=True, text=True, timeout=60)
                output = r.stdout + r.stderr
                if r.returncode != 0:
                    return f"FAILED (exit code {r.returncode})\n{output}"
                return f"SUCCESS\n{output}"
            except subprocess.TimeoutExpired:
                return "FAILED (timeout)"

        elif name == "memory_store":
            mem = _load_memory()
            mem[args["key"]] = args["value"]
            _save_memory(mem)
            return f"Memory saved: {args['key']}"

        elif name == "memory_recall":
            mem = _load_memory()
            key = args.get("key", "")
            if key:
                if key in mem:
                    return f"{key}: {mem[key]}"
                return f"Memory '{key}' not found"
            if not mem:
                return "(no memories stored)"
            lines = [f"  {k}: {v[:100]}{'...' if len(v) > 100 else ''}" for k, v in mem.items()]
            return f"Memori tersimpan ({len(mem)}):\n" + "\n".join(lines)

        elif name == "memory_forget":
            mem = _load_memory()
            if args["key"] in mem:
                del mem[args["key"]]
                _save_memory(mem)
                return f"Memory forgotten: {args['key']}"
            return f"Memory '{args['key']}' not found"

        elif name == "screenshot":
            path = args.get("path", f"/tmp/joki_screenshot_{int(time.time())}.png")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            cmds = [
                f"scrot '{path}'",
                f"import -window root '{path}'",
                f"gnome-screenshot -f '{path}'"
            ]
            for cmd in cmds:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    size = os.path.getsize(path)
                    return f"Screenshot saved: {path} ({size} bytes)"
            return "Error: gagal mengambil screenshot. Install scrot: sudo apt install scrot"

        elif name == "port_scan":
            target = args["target"]
            port_str = args.get("ports", "common")
            scan_type = args.get("scan_type", "quick")
            results = []

            common_ports = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445,
                           993, 995, 1433, 1521, 2049, 2082, 2083, 3306, 3389, 5432,
                           5900, 5984, 6379, 8080, 8443, 9000, 9090, 27017]

            if port_str == "common":
                ports = common_ports
            elif port_str == "1-1000":
                ports = list(range(1, 1001))
            elif port_str == "1-1024":
                ports = list(range(1, 1025))
            else:
                ports = []
                for part in port_str.split(","):
                    part = part.strip()
                    if "-" in part:
                        a, b = part.split("-", 1)
                        ports.extend(range(int(a), int(b) + 1))
                    else:
                        ports.append(int(part))

            if scan_type == "quick":
                ports = [p for p in ports if p in common_ports] or ports[:50]

            with _Spinner(f"Scanning {target} ({len(ports)} ports)"):
                for port in ports:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    r = sock.connect_ex((target, port))
                    if r == 0:
                        try:
                            service = socket.getservbyport(port)
                        except:
                            service = "unknown"
                        results.append(f"  PORT {port:>5}/tcp  OPEN  {service}")
                    sock.close()

            if not results:
                return f"[PORTS] No open ports found on {target} (scanned {len(ports)} ports)"
            return f"[PORTS] Open ports on {target} ({len(results)} open of {len(ports)} scanned):\n" + "\n".join(results)

        elif name == "dns_enum":
            domain = args["domain"]
            action = args.get("action", "records")
            output = []

            if action in ("records", "all"):
                record_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]
                for rtype in record_types:
                    r = subprocess.run(
                        ["dig", "+short", domain, rtype],
                        capture_output=True, text=True, timeout=15
                    )
                    if r.stdout.strip():
                        output.append(f"  {rtype} Records:")
                        for line in r.stdout.strip().splitlines():
                            output.append(f"    {line}")
                if not output:
                    output.append("  (no DNS records found via dig)")

            if action in ("subdomains", "all"):
                common_subdomains = [
                    "www", "mail", "ftp", "admin", "blog", "webmail", "pop3",
                    "smtp", "api", "dev", "test", "staging", "vpn", "remote",
                    "portal", "cpanel", "whm", "mysql", "backup", "proxy",
                    "cdn", "static", "img", "docs", "wiki", "git", "jenkins",
                    "jira", "confluence", "grafana", "prometheus", "monitor",
                    "ns1", "ns2", "ns3", "mx", "chat", "help", "support",
                    "status", "app", "beta", "demo", "shop", "store", "ssl",
                    "cloud", "web", "server", "db", "redis", "mongo"
                ]
                output.append(f"\n  Subdomain brute-force ({len(common_subdomains)}):")
                found = 0
                for sd in common_subdomains:
                    sd_target = f"{sd}.{domain}"
                    try:
                        r = subprocess.run(
                            ["dig", "+short", sd_target, "A"],
                            capture_output=True, text=True, timeout=3
                        )
                        if r.stdout.strip():
                            output.append(f"    {sd_target} -> {r.stdout.strip()}")
                            found += 1
                    except:
                        pass
                output.append(f"  Found {found} subdomains")

            return f"[DNS] Enumeration for {domain}:\n" + "\n".join(output)

        elif name == "web_vuln_scan":
            url = args["url"].rstrip("/")
            checks = args.get("checks", "headers,info")
            output = []

            try:
                r = httpx.get(url, timeout=15, follow_redirects=True, verify=False)
            except Exception as e:
                return f"[WEB_VULN] Error accessing {url}: {e}"

            output.append(f"  URL: {url}")
            output.append(f"  Status: {r.status_code}")
            output.append(f"  Server: {r.headers.get('Server', 'N/A')}")
            output.append(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")
            output.append(f"  Content-Length: {len(r.content)} bytes")

            if "headers" in checks or "all" in checks:
                output.append("\n  [Security Headers]")
                sec_headers = {
                    "Strict-Transport-Security": "HSTS (HttpOnly)",
                    "Content-Security-Policy": "CSP",
                    "X-Frame-Options": "Clickjacking protection",
                    "X-Content-Type-Options": "MIME-sniffing protection",
                    "X-XSS-Protection": "XSS protection",
                    "Referrer-Policy": "Referrer policy",
                    "Permissions-Policy": "Permissions policy",
                    "Set-Cookie": "Cookie flags (HttpOnly/Secure)",
                }
                for hdr, desc in sec_headers.items():
                    val = r.headers.get(hdr, "MISSING")
                    marker = "\033[31mMISSING\033[0m" if val == "MISSING" else "\033[32mPRESENT\033[0m"
                    output.append(f"    {marker} {desc} ({hdr})")
                    if val != "MISSING":
                        output.append(f"      Value: {val[:100]}")

            if "info" in checks or "all" in checks:
                output.append("\n  [Server Information]")
                via = r.headers.get("Via", "")
                cf_ray = r.headers.get("CF-RAY", "")
                powered = r.headers.get("X-Powered-By", "")
                asp = r.headers.get("X-AspNet-Version", "")
                runtime = r.headers.get("X-Runtime", "")
                for hdr, label in [(via, "Via"), (cf_ray, "CF-RAY"),
                                   (powered, "X-Powered-By"), (asp, "X-AspNet-Version"),
                                   (runtime, "X-Runtime")]:
                    if hdr:
                        output.append(f"    {label}: {hdr}")

            if "sqli" in checks or "all" in checks:
                output.append("\n  [SQL Injection Test]")
                sqli_payloads = [
                    ("'", "single quote"),
                    ("' OR '1'='1", "OR true"),
                    ("' UNION SELECT 1--", "UNION"),
                    ("1' AND 1=1--", "AND true"),
                    ("1' AND 1=2--", "AND false"),
                ]
                import urllib.parse
                for payload, desc in sqli_payloads:
                    try:
                        encoded = urllib.parse.quote(payload)
                        test_url = f"{url}?id={encoded}"
                        rr = httpx.get(test_url, timeout=10, verify=False)
                        if rr.status_code == 200:
                            import html
                            body_lower = rr.text.lower()
                            sqli_indicators = ["sql", "mysql", "syntax", "uncaught",
                                               "odbc", "exception", "warning", "db_",
                                               "column", "rowCount", "oracle", "postgre"]
                            if any(ind in body_lower for ind in sqli_indicators):
                                output.append(f"    \033[31mSUSPECT SQLi\033[0m (payload: {desc})")
                            else:
                                output.append(f"    OK (payload: {desc})")
                        else:
                            output.append(f"    {rr.status_code} (payload: {desc})")
                    except:
                        output.append(f"    Error (payload: {desc})")

            if "xss" in checks or "all" in checks:
                output.append("\n  [XSS Reflection Test]")
                xss_payloads = [
                    "<script>alert(1)</script>",
                    "<img src=x onerror=alert(1)>",
                    "\"><script>alert(1)</script>",
                ]
                import urllib.parse, html
                for payload in xss_payloads:
                    try:
                        encoded = urllib.parse.quote(payload)
                        test_url = f"{url}?q={encoded}"
                        rr = httpx.get(test_url, timeout=10, verify=False)
                        if html.unescape(payload) in rr.text or payload in rr.text:
                            output.append(f"    \033[31mSUSPECT XSS\033[0m (payload reflected)")
                        else:
                            output.append(f"    No reflection (payload: {payload[:30]})")
                    except:
                        output.append(f"    Error (payload: {payload[:30]})")

            return f"[WEB_VULN] Scan result for {url}:\n" + "\n".join(output)

        elif name == "whois_lookup":
            target = args["target"]
            with _Spinner(f"WHOIS lookup {target}"):
                r = subprocess.run(
                    ["whois", target],
                    capture_output=True, text=True, timeout=30
                )
            output = r.stdout or r.stderr
            if not output:
                return f"  No WHOIS data for {target} (install whois: sudo apt install whois)"
            lines = output.splitlines()
            important = []
            keywords = ["domain", "registrar", "registrant", "admin", "creation date",
                        "expir", "name server", "status", "org", "organization", "email",
                        "phone", "address", "country", "referral", "whois", "inetnum",
                        "netname", "descr", "role", "nic-hdl", "mnt-by", "source"]
            for line in lines:
                if any(k.lower() in line.lower() for k in keywords):
                    important.append(f"  {line.strip()}")
            if important:
                return f"[WHOIS] {target}:\n" + "\n".join(important[:40])
            return f"[WHOIS] {target}:\n" + "\n".join(f"  {l}" for l in lines[:30])

        elif name == "ssl_check":
            host = args["host"]
            port = int(args.get("port", 443))
            output = []

            try:
                ctx = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=10) as sock:
                    with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                        cert = ssock.getpeercert()
                        output.append(f"  Host: {host}:{port}")
                        output.append(f"  Protocol: {ssock.version()}")

                        if cert:
                            output.append(f"  Subject: {dict(cert['subject'][0]).get('commonName', 'N/A')}")
                            output.append(f"  Issuer: {dict(cert['issuer'][0]).get('organizationName', 'N/A')}")
                            output.append(f"  Serial: {cert.get('serialNumber', 'N/A')}")
                            output.append(f"  Valid From: {cert.get('notBefore', 'N/A')}")
                            output.append(f"  Valid Until: {cert.get('notAfter', 'N/A')}")

                            import datetime
                            not_after = cert.get('notAfter', '')
                            if not_after:
                                try:
                                    exp = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                                    remaining = (exp - datetime.datetime.now()).days
                                    if remaining < 0:
                                        output.append(f"  \033[31mEXPIRED ({abs(remaining)} days ago)\033[0m")
                                    elif remaining < 30:
                                        output.append(f"  \033[33mExpiring soon: {remaining} days\033[0m")
                                    else:
                                        output.append(f"  \033[32mValid: {remaining} days remaining\033[0m")
                                except:
                                    pass

                            san = cert.get('subjectAltName', [])
                            if san:
                                domains = [v for k, v in san if k == 'DNS']
                                output.append(f"  SAN: {', '.join(domains[:5])}{'...' if len(domains) > 5 else ''}")
                        else:
                            output.append("  No certificate returned")
            except ssl.SSLError as e:
                output.append(f"  SSL Error: {e}")
            except Exception as e:
                output.append(f"  Connection Error: {e}")

            if not output:
                return f"[SSL] No response from {host}:{port}"
            return f"[SSL] Certificate check for {host}:{port}\n" + "\n".join(output)

        elif name == "dir_bruteforce":
            url = args["url"].rstrip("/")
            wordlist_size = args.get("wordlist", "small")
            extensions = args.get("extensions", "")
            ext_list = [f".{e.strip()}" for e in extensions.split(",") if e.strip()] if extensions else []

            wordlists = {
                "small": ["admin", "login", "wp-admin", "backup", "config", "db", "sql",
                          "admin.php", "login.php", "config.php", ".env", "wp-config.php",
                          "robots.txt", "sitemap.xml", "index.php", "index.html", "test",
                          "api", "v1", "v2", "static", "assets", "uploads", "images",
                          "css", "js", "private", "secret", "hidden", "tmp", "temp",
                          "logs", "error_log", "phpinfo.php", "info.php", "shell.php",
                          "cmd.php", "upload.php", "download.php", "cgi-bin", "cron",
                          "setup", "install", "readme.html", "license.txt"],
                "medium": [],  # Will use small + more
                "large": []
            }

            if wordlist_size == "medium":
                wordlists["medium"] = wordlists["small"] + [
                    "app", "src", "lib", "include", "inc", "modules", "plugins",
                    "themes", "templates", "cache", "data", "dump", "export",
                    "import", "manager", "panel", "dashboard", "user", "users",
                    "member", "members", "account", "register", "signup", "forgot",
                    "reset", "password", "profile", "edit", "settings", "preferences",
                    "ajax", "rest", "graphql", "ws", "websocket", "soap", "xmlrpc",
                    "rss", "feed", "atom", "json", "csv", "txt", "xml", "pdf",
                    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "pdf",
                ]

            if wordlist_size == "large":
                wordlists["large"] = wordlists["medium"] + [
                    "0", "1", "2", "3", "a", "b", "c", "d", "e", "f", "g", "h",
                    "old", "new", "bak", "copy", "original", "latest", "final",
                    "working", "dev", "development", "staging", "prod", "production",
                    "local", "live", "master", "main", "release", "patch", "hotfix",
                    "docker", "docker-compose.yml", "Dockerfile", "Makefile",
                    "package.json", "composer.json", "pom.xml", "build.gradle",
                    "Procfile", "requirements.txt", "Gemfile", "Podfile",
                    ".gitignore", ".htaccess", ".htpasswd", ".svn", ".DS_Store",
                    "Thumbs.db", "crossdomain.xml", "clientaccesspolicy.xml",
                    "web.config", "application.properties", "log4j.properties",
                    "struts.xml", "web.xml", "index.jsp", "default.aspx",
                ]

            paths = wordlists.get(wordlist_size, wordlists["small"])

            found = []
            with _Spinner(f"Bruteforcing {url} ({len(paths)} paths)"):
                for path in paths:
                    test_url = f"{url}/{path}"
                    try:
                        rr = httpx.get(test_url, timeout=5, verify=False)
                        if rr.status_code in (200, 201, 204, 301, 302, 307, 308, 401, 403):
                            size = len(rr.content)
                            found.append(f"  {rr.status_code:>3}  {size:>8}b  {test_url}")
                    except:
                        pass

                    if ext_list:
                        for ext in ext_list:
                            test_url_ext = f"{url}/{path}{ext}"
                            try:
                                rr = httpx.get(test_url_ext, timeout=5, verify=False)
                                if rr.status_code in (200, 201, 204, 301, 302, 307, 308, 401, 403):
                                    size = len(rr.content)
                                    found.append(f"  {rr.status_code:>3}  {size:>8}b  {test_url_ext}")
                            except:
                                pass

            if not found:
                return f"[DIRBRUTE] No paths found on {url} ({len(paths)} tested)"
            return f"[DIRBRUTE] Found {len(found)} paths on {url}:\n" + "\n".join(found)

        elif name == "cve_search":
            query = args["query"]
            with _Spinner(f"Searching CVEs for {query}"):
                try:
                    search_url = f"https://cve.circl.lu/api/search/{query.replace(' ', '/')}"
                    r = httpx.get(search_url, timeout=20, follow_redirects=True, verify=False)
                    if r.status_code == 200:
                        data = r.json()
                    else:
                        data = None
                except:
                    data = None

            output = []
            if data and isinstance(data, list):
                cves = data[:15]
                for cve in cves:
                    cve_id = cve.get("id", "N/A")
                    summary = cve.get("summary", "")[:200]
                    cvss = cve.get("cvss_score", "N/A")
                    severity = cve.get("severity", "")
                    output.append(f"  {cve_id} (CVSS: {cvss} {severity})")
                    output.append(f"    {summary}")
                    output.append("")
                if not output:
                    output.append(f"  No CVEs found for '{query}'")
            else:
                output.append(f"  CIRCL API unavailable, searching via web...")
                try:
                    results = DDGS().text(f"CVE {query}", max_results=5)
                    if results:
                        for r in results:
                            output.append(f"  {r['title']}")
                            output.append(f"    {r['href']}")
                            output.append(f"    {r['body'][:200]}")
                            output.append("")
                    else:
                        output.append(f"  No results found for '{query}'")
                except:
                    output.append(f"  Error searching for '{query}'")

            return f"[CVE] Results for '{query}':\n" + "\n".join(output)

        elif name == "tech_detect":
            url = args["url"].rstrip("/")
            deep = args.get("deep", "simple")
            output = []
            tech = {}

            try:
                r = httpx.get(url, timeout=15, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            except Exception as e:
                return f"[TECH] Error accessing {url}: {e}"

            output.append(f"  URL: {url}")
            output.append(f"  Status: {r.status_code}")
            output.append(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")

            output.append("\n  [HTTP Headers]")
            interesting_headers = [
                "Server", "X-Powered-By", "X-Generator", "X-Drupal-Cache",
                "X-Drupal-Dynamic-Cache", "X-Varnish", "X-Cache", "X-Cache-Hits",
                "CF-RAY", "X-Server-Powered-By", "X-AspNet-Version", "X-Runtime",
                "Via", "X-Proxy-Cache", "X-Served-By", "X-CMS", "X-Version",
                "Access-Control-Allow-Origin", "X-Frame-Options",
                "X-Content-Type-Options", "Strict-Transport-Security"
            ]
            for h in interesting_headers:
                val = r.headers.get(h)
                if val:
                    output.append(f"    {h}: {val}")

            output.append("\n  [Cookies]")
            for cookie in r.cookies:
                name = cookie.name
                output.append(f"    {name}")

            if deep == "deep":
                html = r.text.lower()

                detectors = {
                    "WordPress": ["wp-content", "wp-includes", "wp-json", "wordpress"],
                    "Drupal": ["drupal", "drupal.js", "sites/default"],
                    "Joomla": ["joomla", "com_content", "com_users"],
                    "Laravel": ["laravel", "csrf-token", "livewire"],
                    "Django": ["csrfmiddlewaretoken", "django", "__admin"],
                    "Ruby on Rails": ["rails", "csrf-param", "authenticity_token"],
                    "React": ["react", "react-dom", "__NEXT_DATA__", "nextjs", "next/js"],
                    "Vue.js": ["vue", "vuejs", "v-bind", "v-model", "vue-router"],
                    "Angular": ["ng-app", "ng-controller", "angular", "ng-version"],
                    "jQuery": ["jquery", "$.fn", "jquery-"],
                    "Bootstrap": ["bootstrap", "bootstrap-", "bs-"],
                    "Tailwind": ["tailwind", "tailwindcss"],
                    "Alpine.js": ["alpinejs", "x-data", "x-init", "x-on"],
                    "HTMX": ["htmx", "hx-get", "hx-post", "hx-trigger"],
                    "PHP": [".php", "php-session"],
                    "ASP.NET": ["__viewstate", "__eventvalidation", "asp.net", "aspnet"],
                    "Java": ["javax.faces", "jsf", "spring", "struts"],
                    "Nginx": ["nginx", "nginx/"],
                    "Apache": ["apache/", "apache", ".htaccess"],
                    "Cloudflare": ["cloudflare", "cf-ray", "__cfduid"],
                    "Google Analytics": ["gtag", "ga.js", "analytics.js", "google-analytics"],
                    "Facebook Pixel": ["fbq(", "facebook pixel", "connect.facebook"],
                    "Hotjar": ["hotjar", "hj("],
                    "Intercom": ["intercom", "intercom-script"],
                    "Stripe": ["stripe", "pk_live", "sk_live"],
                    "Google Maps": ["maps.google", "google.maps", "maps.googleapis"],
                    "reCAPTCHA": ["recaptcha", "g-recaptcha"],
                    "Disqus": ["disqus", "disqus_thread"],
                    "Algolia": ["algolia", "algoliasearch"],
                    "Sentry": ["sentry", "raven-"],
                    "New Relic": ["newrelic", "nr-"],
                }

                output.append(f"\n  [Detected Technologies]")
                for name, sigs in sorted(detectors.items()):
                    for sig in sigs:
                        if sig in html or sig in r.text.lower():
                            tech[name] = tech.get(name, 0) + 1
                            break
                if tech:
                    for name in sorted(tech, key=lambda k: -tech[k]):
                        output.append(f"    {name}")
                else:
                    output.append(f"    (no specific tech detected)")

                output.append(f"\n  [HTML Analysis]")
                title_match = re.search(r'<title[^>]*>(.*?)</title>', r.text, re.IGNORECASE | re.DOTALL)
                if title_match:
                    output.append(f"    Title: {title_match.group(1).strip()[:100]}")
                desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', r.text, re.IGNORECASE)
                if desc_match:
                    output.append(f"    Meta Desc: {desc_match.group(1)[:120]}")
                script_count = len(re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', r.text, re.IGNORECASE))
                css_count = len(re.findall(r'<link[^>]+href=["\']([^"\']+\.css)["\']', r.text, re.IGNORECASE))
                output.append(f"    External JS: {script_count}")
                output.append(f"    External CSS: {css_count}")

            return f"[TECH] Tech Stack for {url}:\n" + "\n".join(output)

        elif name == "js_analyze":
            url = args["url"].rstrip("/")
            extract = args.get("extract", "all")
            output = []
            js_contents = []
            raw_js = ""

            if url.endswith(".js"):
                try:
                    rr = httpx.get(url, timeout=15, verify=False, headers={"User-Agent": "Mozilla/5.0"})
                    if rr.status_code == 200:
                        raw_js = rr.text
                        js_contents.append((url.rsplit("/", 1)[-1], raw_js))
                except:
                    return f"[JS] Error fetching JS file: {url}"
            else:
                try:
                    r = httpx.get(url, timeout=15, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code != 200:
                        return f"[JS] Error: {url} returned {r.status_code}"
                    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.IGNORECASE)
                    inline_scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.IGNORECASE | re.DOTALL)
                    inline_js = "\n".join(inline_scripts)
                    if inline_js.strip():
                        js_contents.append(("inline", inline_js))

                    for src in scripts[:15]:
                        js_url = src if src.startswith("http") else (url.rstrip("/") + "/" + src.lstrip("/"))
                        try:
                            rr = httpx.get(js_url, timeout=10, verify=False, headers={"User-Agent": "Mozilla/5.0"})
                            if rr.status_code == 200:
                                name = js_url.rsplit("/", 1)[-1][:40]
                                js_contents.append((name, rr.text))
                        except:
                            pass
                except Exception as e:
                    return f"[JS] Error: {e}"

            if not js_contents:
                return f"[JS] No JavaScript found at {url}"

            output.append(f"  JS files analyzed: {len(js_contents)}")

            all_js = "\n".join(js for _, js in js_contents)

            if extract in ("endpoints", "all"):
                output.append(f"\n  [API Endpoints / URLs]")
                url_patterns = [
                    r'["\'](https?://[^"\']+)["\']',
                    r'["\'](/[a-zA-Z][^"\']*(?:api|v[0-9]+|rest|graphql|endpoint|webhook)[^"\']*)["\']',
                    r'["\'](/[a-zA-Z][^"\']*/(?:get|post|put|delete|fetch|save|update|create|list|search|find|query)[^"\']*)["\']',
                    r'["\'](/[a-zA-Z][^"\']*\.(?:php|asp|aspx|jsp|json|xml|do|action))["\']',
                    r'fetch\(["\']([^"\']+)["\']',
                    r'axios\.\w+\(["\']([^"\']+)["\']',
                    r'ajax\(\s*["\']([^"\']+)["\']',
                    r'\$\..*?\(["\']([^"\']+)["\']',
                    r'XMLHttpRequest[^;]*["\']([^"\']+)["\']',
                    r'url:\s*["\']([^"\']+)["\']',
                    r'endpoint:\s*["\']([^"\']+)["\']',
                    r'baseURL:\s*["\']([^"\']+)["\']',
                    r'baseUrl:\s*["\']([^"\']+)["\']',
                    r'apiUrl:\s*["\']([^"\']+)["\']',
                    r'api_url:\s*["\']([^"\']+)["\']',
                ]
                found_urls = set()
                for pat in url_patterns:
                    for m in re.finditer(pat, all_js, re.IGNORECASE):
                        found_urls.add(m.group(1))

                found_urls = [u for u in found_urls if len(u) > 3 and u != " "]
                found_urls = sorted(set(found_urls))

                if found_urls:
                    for u in found_urls[:40]:
                        output.append(f"    {u}")
                    if len(found_urls) > 40:
                        output.append(f"    ... and {len(found_urls) - 40} more")
                else:
                    output.append(f"    (no endpoints found)")

            if extract in ("secrets", "all"):
                output.append(f"\n  [Potential Secrets / Credentials]")
                secret_patterns = [
                    (r'api[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Key"),
                    (r'api_key["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Key"),
                    (r'apiSecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Secret"),
                    (r'api_secret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "API Secret"),
                    (r'[Aa]ccess[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Access Key"),
                    (r'[Ss]ecret[Aa]ccess[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Secret Access Key"),
                    (r'[Aa]pp[Kk]ey["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "App Key"),
                    (r'[Aa]pp[Ss]ecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "App Secret"),
                    (r'[Tt]oken["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Token"),
                    (r'[Pp]assword["\']?\s*[:=]\s*["\']([^"\']{6,})["\']', "Password"),
                    (r'[Pp]asswd["\']?\s*[:=]\s*["\']([^"\']{6,})["\']', "Password"),
                    (r'[Ss]ecret["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', "Secret"),
                    (r'[Jj][Ww][Tt]["\']?\s*[:=]\s*["\']([^"\']+)["\']', "JWT"),
                    (r'[Bb]earer\s+([a-zA-Z0-9._-]{20,})', "Bearer Token"),
                    (r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', "Private Key"),
                    (r'ghp_[a-zA-Z0-9]{36}', "GitHub Token"),
                    (r'sk_live_[a-zA-Z0-9]{24,}', "Stripe Live Key"),
                    (r'pk_live_[a-zA-Z0-9]{24,}', "Stripe Live Key"),
                    (r'sk_test_[a-zA-Z0-9]{24,}', "Stripe Test Key"),
                    (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
                    (r'["\']password["\'"]\s*["\']([^"\']{4,})["\']', "Hardcoded Password"),
                    (r'["\'][Pp]assword["\']\s*[:=]\s*["\']([^"\']{4,})["\']', "Hardcoded Password"),
                ]
                secrets_found = []
                for pat, label in secret_patterns:
                    matches = re.findall(pat, all_js)
                    for m in matches:
                        val = m if isinstance(m, str) else m[0]
                        if val and len(val) < 200 and val not in ("undefined", "null", "true", "false", ""):
                            secrets_found.append(f"    [{label}] {val[:80]}")

                if secrets_found:
                    for s in secrets_found[:20]:
                        output.append(s)
                    if len(secrets_found) > 20:
                        output.append(f"    ... and {len(secrets_found) - 20} more")
                else:
                    output.append(f"    (no secrets detected)")

                output.append(f"\n  [Interesting Keywords]")
                keywords = ["debugger", "eval(", "Function(", "setTimeout", "setInterval",
                           "XMLHttpRequest", "fetch(", "WebSocket", "localStorage",
                           "sessionStorage", "document.cookie", "postMessage",
                           "import(", "require(", "export ", "module.exports"]
                found_kw = []
                for kw in keywords:
                    count = all_js.count(kw)
                    if count > 0:
                        found_kw.append(f"    {kw}: {count}x")
                if found_kw:
                    output.extend(found_kw)
                else:
                    output.append(f"    (none)")

            return f"[JS] JavaScript Analysis for {url}:\n" + "\n".join(output)

        elif name == "api_discover":
            url = args["url"].rstrip("/")
            depth = int(args.get("depth", 2))
            output = []

            try:
                r = httpx.get(url, timeout=15, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            except Exception as e:
                return f"[API] Error accessing {url}: {e}"

            text = r.text
            apis = set()

            output.append(f"  Target: {url}")

            output.append(f"\n  [Form Actions]")
            form_actions = re.findall(r'<form[^>]+action=["\']([^"\']+)["\']', text, re.IGNORECASE)
            for fa in form_actions:
                apis.add(fa)
                output.append(f"    {fa}")
            if not form_actions:
                output.append(f"    (no forms found)")

            output.append(f"\n  [Inline API Calls]")
            fetch_patterns = [
                r'fetch\(["\']([^"\']+)["\']',
                r'axios\.\w+\(["\']([^"\']+)["\']',
                r'\$\.(?:get|post|ajax)\(["\']([^"\']+)["\']',
                r'\.ajax\(\{.*?url:\s*["\']([^"\']+)["\']',
                r'XMLHttpRequest[^;]*?\.open\(["\'][A-Z]+["\'],\s*["\']([^"\']+)["\']',
                r'url:\s*["\']([^"\']+)["\']',
            ]
            for pat in fetch_patterns:
                for m in re.finditer(pat, text, re.IGNORECASE):
                    apis.add(m.group(1))
                    output.append(f"    {m.group(1)[:100]}")

            if depth >= 2:
                output.append(f"\n  [Script File URLs]")
                js_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE)
                for js_src in js_srcs[:10]:
                    js_url = js_src if js_src.startswith("http") else (url.rstrip("/") + "/" + js_src.lstrip("/"))
                    try:
                        rr = httpx.get(js_url, timeout=10, verify=False, headers={"User-Agent": "Mozilla/5.0"})
                        if rr.status_code == 200:
                            inner_patterns = [
                                r'["\'](https?://[^"\']*api[^"\']*)["\']',
                                r'["\'](/api/[^"\']+)["\']',
                                r'["\'](/v[0-9]+/[^"\']+)["\']',
                                r'["\'](/graphql)[^"\']*["\']',
                                r'["\'](/rest/[^"\']+)["\']',
                                r'["\'](/[^"\']*(?:endpoint|webhook|callback)[^"\']*)["\']',
                            ]
                            for ipat in inner_patterns:
                                for m in re.finditer(ipat, rr.text, re.IGNORECASE):
                                    apis.add(m.group(1))
                    except:
                        pass

                if apis:
                    output.append(f"\n  [Unique API Paths Found]")
                    for api in sorted(apis)[:40]:
                        output.append(f"    {api}")
                else:
                    output.append(f"\n  [Unique API Paths Found]")
                    output.append(f"    (none found)")

                output.append(f"\n  [API Patterns]")
                api_patterns_found = set()
                for api in apis:
                    parts = api.rstrip("/").split("/")
                    for i, p in enumerate(parts):
                        if p in ("api", "v1", "v2", "v3", "rest", "graphql", "webhook", "endpoint"):
                            pattern = "/".join(parts[:i+2])
                            api_patterns_found.add(pattern)
                if api_patterns_found:
                    for p in sorted(api_patterns_found)[:15]:
                        output.append(f"    /{p.lstrip('/')}")
                else:
                    output.append(f"    (no specific API pattern)")

            return f"[API] API Discovery for {url}:\n" + "\n".join(output)

        elif name == "source_map_check":
            url = args["url"].rstrip("/")
            output = []

            try:
                r = httpx.get(url, timeout=15, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            except Exception as e:
                return f"[SOURCEMAP] Error accessing {url}: {e}"

            output.append(f"  Target: {url}")

            output.append(f"\n  [Source Map Discovery]")
            js_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', r.text, re.IGNORECASE)
            found_maps = []

            for js_src in js_srcs[:20]:
                js_url = js_src if js_src.startswith("http") else (url.rstrip("/") + "/" + js_src.lstrip("/"))
                if js_url.endswith(".map"):
                    found_maps.append(js_url)
                    continue
                map_url = js_url + ".map"
                alt_map = re.sub(r'\.js$', '.map', js_url)
                for mu in [map_url, alt_map]:
                    try:
                        mr = httpx.head(mu, timeout=5, verify=False)
                        if mr.status_code in (200, 204):
                            found_maps.append(mu)
                    except:
                        pass

            comment_maps = re.findall(r'//#\s*sourceMappingURL=(.+\.map)', r.text)
            if comment_maps:
                for cm in comment_maps:
                    if not cm.startswith("http"):
                        cm = url.rstrip("/") + "/" + cm.lstrip("/")
                    found_maps.append(cm)

            if found_maps:
                output.append(f"  \033[31mEXPOSED SOURCE MAPS DETECTED!\033[0m")
                for fm in sorted(set(found_maps)):
                    output.append(f"    {fm}")
            else:
                output.append(f"  No source maps found (good)")

            if found_maps:
                output.append(f"\n  [Content from First Source Map]")
                try:
                    sm_url = list(set(found_maps))[0]
                    sm_r = httpx.get(sm_url, timeout=10, verify=False)
                    if sm_r.status_code == 200:
                        sm_data = sm_r.json()
                        sources = sm_data.get("sources", [])
                        names = sm_data.get("names", [])
                        if sources:
                            output.append(f"    Original sources ({len(sources)}):")
                            for s in sources[:15]:
                                output.append(f"      {s}")
                        if names:
                            output.append(f"    Identifiers ({len(names)}):")
                            for n in names[:20]:
                                output.append(f"      {n}")
                except:
                    output.append(f"    (could not parse source map)")

            return f"[SOURCEMAP] Source Map Check for {url}:\n" + "\n".join(output)

        elif name == "form_analyze":
            url = args["url"].rstrip("/")
            output = []

            try:
                r = httpx.get(url, timeout=15, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            except Exception as e:
                return f"[FORM] Error accessing {url}: {e}"

            output.append(f"  Target: {url}")
            output.append(f"  Status: {r.status_code}")

            forms = re.findall(r'(<form[^>]*>(.*?)</form>)', r.text, re.IGNORECASE | re.DOTALL)

            if not forms:
                output.append(f"\n  No forms found")
                return f"[FORM] Form Analysis for {url}:\n" + "\n".join(output)

            output.append(f"\n  Forms found: {len(forms)}")

            for i, (form_html, form_body) in enumerate(forms):
                output.append(f"\n  {'='*40}")
                output.append(f"  Form #{i+1}")

                action = re.search(r'action=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
                method = re.search(r'method=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
                enctype = re.search(r'enctype=["\']([^"\']*)["\']', form_html, re.IGNORECASE)

                output.append(f"    Action: {action.group(1) if action else '(self)'}")
                output.append(f"    Method: {method.group(1).upper() if method else 'GET'}")
                if enctype:
                    output.append(f"    Enctype: {enctype.group(1)}")

                output.append(f"\n    [Fields]")
                inputs = re.findall(r'(<input[^>]*>)', form_body, re.IGNORECASE)
                selects = re.findall(r'(<select[^>]*>.*?</select>)', form_body, re.IGNORECASE | re.DOTALL)
                textareas = re.findall(r'(<textarea[^>]*>.*?</textarea>)', form_body, re.IGNORECASE | re.DOTALL)

                for inp in inputs:
                    inp_type = re.search(r'type=["\']([^"\']*)["\']', inp, re.IGNORECASE)
                    inp_name = re.search(r'name=["\']([^"\']*)["\']', inp, re.IGNORECASE)
                    inp_val = re.search(r'value=["\']([^"\']*)["\']', inp, re.IGNORECASE)
                    inp_id = re.search(r'id=["\']([^"\']*)["\']', inp, re.IGNORECASE)
                    inp_auto = re.search(r'autocomplete=["\']([^"\']*)["\']', inp, re.IGNORECASE)

                    t = inp_type.group(1).lower() if inp_type else "text"
                    n = inp_name.group(1) if inp_name else "(unnamed)"
                    v = inp_val.group(1) if inp_val else "(empty)"

                    tag = ""
                    if t == "hidden":
                        tag = " \033[33m[HIDDEN]\033[0m"
                    if inp_auto and inp_auto.group(1).lower() == "off":
                        tag += " \033[31m[autocomplete=off]\033[0m"

                    output.append(f"      [{t}] {n} = {v[:40]}{tag}")

                for sel in selects:
                    sel_name = re.search(r'name=["\']([^"\']*)["\']', sel, re.IGNORECASE)
                    n = sel_name.group(1) if sel_name else "(unnamed)"
                    options = re.findall(r'<option[^>]*value=["\']([^"\']*)["\']', sel, re.IGNORECASE)
                    output.append(f"      [select] {n} (options: {options[:5]})")

                for ta in textareas:
                    ta_name = re.search(r'name=["\']([^"\']*)["\']', ta, re.IGNORECASE)
                    n = ta_name.group(1) if ta_name else "(unnamed)"
                    output.append(f"      [textarea] {n}")

                csrf_inputs = re.findall(r'<input[^>]*name=["\']([^"\']*(?:csrf|token|authenticity|_token)[^"\']*)["\'][^>]*>', form_html, re.IGNORECASE)
                if csrf_inputs:
                    output.append(f"    \033[32m[CSRF Protection Detected]\033[0m")
                    for c in csrf_inputs:
                        output.append(f"      CSRF field: {c}")

            return f"[FORM] Form Analysis for {url}:\n" + "\n".join(output)

        elif name == "apk_analyze":
            path = args["path"]
            output = []

            if not os.path.isfile(path):
                return f"[APK] File not found: {path}"

            size = os.path.getsize(path)
            output.append(f"  File: {path}")
            output.append(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

            has_aapt = subprocess.run(["which", "aapt2"], capture_output=True, text=True).returncode == 0
            has_aapt_old = subprocess.run(["which", "aapt"], capture_output=True, text=True).returncode == 0
            has_apkanalyzer = subprocess.run(["which", "apkanalyzer"], capture_output=True, text=True).returncode == 0
            has_unzip = subprocess.run(["which", "unzip"], capture_output=True, text=True).returncode == 0
            has_jarsigner = subprocess.run(["which", "jarsigner"], capture_output=True, text=True).returncode == 0

            if has_aapt:
                r = subprocess.run(["aapt2", "dump", "badging", path], capture_output=True, text=True, timeout=60)
                out = r.stdout
                for line in out.splitlines():
                    if any(k in line for k in ["package:", "application-label:", "sdkVersion:",
                                                "targetSdkVersion:", "launchable-activity:",
                                                "uses-permission:", "uses-feature:",
                                                "application-label-en:", "versionCode:", "versionName:",
                                                "maxSdkVersion:", "minSdkVersion:"]):
                        output.append(f"  {line.strip()}")
            elif has_aapt_old:
                r = subprocess.run(["aapt", "dump", "badging", path], capture_output=True, text=True, timeout=60)
                out = r.stdout
                for line in out.splitlines():
                    if any(k in line for k in ["package:", "application-label:", "sdkVersion:",
                                                "targetSdkVersion:", "launchable-activity:",
                                                "uses-permission:", "uses-feature:",
                                                "application-label-en:", "versionCode:", "versionName:"]):
                        output.append(f"  {line.strip()}")
            else:
                output.append(f"\n  [Basic Info (aapt2/aapt not installed)]")
                if has_unzip:
                    r = subprocess.run(["unzip", "-p", path, "AndroidManifest.xml"], capture_output=True, text=True, timeout=30)
                    if r.stdout:
                        output.append(f"  AndroidManifest.xml extracted (binary)")
                    r = subprocess.run(["unzip", "-l", path], capture_output=True, text=True, timeout=30)
                    for line in r.stdout.splitlines():
                        if any(k in line for k in [".dex", "AndroidManifest", "resources.arsc",
                                                    "lib/", "META-INF", "res/"]):
                            output.append(f"  {line.strip()}")

            if has_apkanalyzer:
                for info_type in ["manifest application-id", "manifest version-name",
                                  "manifest version-code", "manifest min-sdk",
                                  "manifest target-sdk", "manifest debuggable"]:
                    r = subprocess.run(["apkanalyzer", *info_type.split(), path], capture_output=True, text=True, timeout=30)
                    if r.stdout.strip():
                        output.append(f"  {info_type}: {r.stdout.strip()}")

            if has_jarsigner:
                r = subprocess.run(["jarsigner", "-verify", "-verbose", "-certs", path],
                                   capture_output=True, text=True, timeout=30)
                for line in r.stderr.splitlines():
                    if any(k in line for k in ["jar verified", "signer", "X.509", "CN="]):
                        output.append(f"  [Sign] {line.strip()}")

            output.append(f"\n  [Available Analysis Tools]")
            tools_status = {
                "aapt2": has_aapt, "aapt": has_aapt_old,
                "apkanalyzer": has_apkanalyzer, "unzip": has_unzip,
                "jarsigner": has_jarsigner
            }
            for tool, available in tools_status.items():
                output.append(f"    {tool}: {'\033[32mINSTALLED\033[0m' if available else '\033[31mNOT INSTALLED\033[0m'}")
            output.append(f"\n  Install Android tools: sudo apt install android-sdk")
            output.append(f"  Install apkanalyzer: sudo apt install apkanalyzer")

            return f"[APK] APK Analysis:\n" + "\n".join(output)

        elif name == "binary_analyze":
            path = args["path"]
            min_len = int(args.get("strings_min", 6))
            output = []

            if not os.path.isfile(path):
                return f"[BINARY] File not found: {path}"

            size = os.path.getsize(path)
            output.append(f"  File: {path}")
            output.append(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

            has_file = subprocess.run(["which", "file"], capture_output=True, text=True).returncode == 0
            has_strings = subprocess.run(["which", "strings"], capture_output=True, text=True).returncode == 0
            has_objdump = subprocess.run(["which", "objdump"], capture_output=True, text=True).returncode == 0
            has_xxd = subprocess.run(["which", "xxd"], capture_output=True, text=True).returncode == 0
            has_exiftool = subprocess.run(["which", "exiftool"], capture_output=True, text=True).returncode == 0

            if has_file:
                r = subprocess.run(["file", "-b", path], capture_output=True, text=True, timeout=15)
                file_type = r.stdout.strip()
                output.append(f"  Type: {file_type}")
            else:
                output.append(f"  Type: (install 'file' command for detection)")

            if has_exiftool:
                r = subprocess.run(["exiftool", path], capture_output=True, text=True, timeout=30)
                exif_lines = r.stdout.strip().splitlines()
                important_tags = ["File Size", "MIME Type", "Image Size", "File Type",
                                  "Created Date", "Modify Date", "Create Date",
                                  "Software", "Creator", "Author", "Producer",
                                  "Application", "Company", "Architecture", "OS/ABI",
                                  "Operating System", "Compiler", "Linker",
                                  "Entry Point", "Section Count", "Debug Info",
                                  "Machine", "Class", "Endianness"]
                for line in exif_lines:
                    if any(t.lower() in line.lower() for t in important_tags):
                        output.append(f"  [Meta] {line.strip()}")

            output.append(f"\n  [Available Tools]")
            tools_status = {
                "file": has_file, "strings": has_strings, "objdump": has_objdump,
                "xxd": has_xxd, "exiftool": has_exiftool
            }
            for tool, available in tools_status.items():
                output.append(f"    {tool}: {'\033[32mINSTALLED\033[0m' if available else '\033[31mNOT INSTALLED\033[0m'}")

            if has_objdump:
                output.append(f"\n  [ELF/Header Info]")
                r = subprocess.run(["objdump", "-f", path], capture_output=True, text=True, timeout=15)
                header_info = r.stdout.strip()
                if header_info and "file format" in header_info:
                    for line in header_info.splitlines()[:10]:
                        if any(k in line.lower() for k in ["file format", "architecture", "flags",
                                                            "start address", "entry"]):
                            output.append(f"    {line.strip()}")
                r2 = subprocess.run(["objdump", "-p", path], capture_output=True, text=True, timeout=15)
                for line in r2.stdout.splitlines():
                    if any(k in line.lower() for k in ["needed", "soname", "rpath", "runpath",
                                                       "interp", "stack", "relro",
                                                       "nx", "pie", "dynamic"]):
                        output.append(f"    {line.strip()}")

            if has_strings:
                output.append(f"\n  [Strings (min {min_len} chars)]")
                r = subprocess.run(["strings", f"-n{min_len}", path], capture_output=True, text=True, timeout=30)
                all_strings = r.stdout.splitlines()
                output.append(f"    Total strings: {len(all_strings)}")

                interesting_strings = []
                interesting_patterns = [
                    r'https?://[^"\s]+', r'(?:sk|pk)_(?:live|test)_[a-zA-Z0-9]+',
                    r'AKIA[0-9A-Z]{16}', r'-----BEGIN', r'password',
                    r'api_key', r'secret', r'token', r'config',
                    r'database', r'mysql', r'postgres', r'mongodb',
                    r'/etc/', r'/var/', r'/home/', r'/tmp/',
                    r'\.php', r'\.asp', r'\.jsp', r'\.exe',
                    r'\.pdb', r'certificate', r'private_key',
                ]
                for s in all_strings:
                    if len(s) < 4:
                        continue
                    for pat in interesting_patterns:
                        if re.search(pat, s, re.IGNORECASE):
                            interesting_strings.append(s.strip())
                            break

                if interesting_strings:
                    output.append(f"    Interesting strings: {len(interesting_strings)}")
                    for s in sorted(set(interesting_strings))[:30]:
                        output.append(f"      {s[:120]}")
                else:
                    output.append(f"    (no interesting strings found)")

            return f"[BINARY] Binary Analysis:\n" + "\n".join(output)

        elif name == "todo_create":
            items = args["items"]
            _save_todo(items)
            lines = [f"  {i+1}. [ ] {item}" for i, item in enumerate(items)]
            return f"TODO list dibuat ({len(items)} item):\n" + "\n".join(lines)

        elif name == "todo_done":
            indices = args["indices"]
            items = _load_todo()
            marked = []
            for idx in indices:
                if 1 <= idx <= len(items):
                    items[idx - 1] = f"✅ {items[idx - 1]}"
                    marked.append(str(idx))
            _save_todo(items)
            
            # Trigger visual verification if the last item is completed and mentions "Verifikasi"
            visual_trigger = ""
            if indices and max(indices) == len(items):
                last_item = items[-1]
                if "Verifikasi" in last_item:
                    visual_trigger = "\n\n[SISTEM] Deteksi item 'Verifikasi' di akhir TODO. Menyiapkan validasi visual..."
            
            return f"Item TODO {' dan '.join(marked)} selesai! {visual_trigger}\n" + "\n".join(f"  {i+1}. {item}" for i, item in enumerate(items))

        elif name == "todo_show":
            items = _load_todo()
            if not items:
                return "(TODO list kosong)"
            lines = [f"  {i+1}. {item}" for i, item in enumerate(items)]
            done = sum(1 for i in items if i.startswith("✅"))
            return f"TODO list ({done}/{len(items)} selesai):\n" + "\n".join(lines)

        elif name == "ui_screenshot":
            path = args.get("path", "/tmp/joki_ui_screen.png")
            region = args.get("region", "full")
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            if region == "full":
                r = subprocess.run(["import", "-window", "root", path], capture_output=True, text=True, timeout=15)
            else:
                r = subprocess.run(["import", "-crop", region, path], capture_output=True, text=True, timeout=15)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return f"Screenshot saved: {path} ({os.path.getsize(path)} bytes)"
            return f"Error screenshot: {r.stderr or 'unknown'}. Install imagemagick: sudo apt install imagemagick"

        elif name == "ui_click":
            x, y = args["x"], args["y"]
            btn = args.get("button", "left")
            btn_map = {"left": 1, "middle": 2, "right": 3}
            count = args.get("click_count", 1)
            click_arg = "".join([str(btn_map.get(btn, 1))] * count)
            r = subprocess.run(["xdotool", "mousemove", str(x), str(y), "click", click_arg],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return f"Clicked {btn} at ({x},{y})"
            return f"Click error: {r.stderr}. Install xdotool: sudo apt install xdotool"

        elif name == "ui_type":
            text = args["text"]
            safe = text.replace('"', '\\"')
            r = subprocess.run(["xdotool", "type", safe], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return f"Typed: {text[:100]}{'...' if len(text) > 100 else ''}"
            return f"Type error: {r.stderr}"

        elif name == "ui_keypress":
            keys = args["keys"]
            r = subprocess.run(["xdotool", "key", keys], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return f"Key pressed: {keys}"
            return f"Key error: {r.stderr}"

        elif name == "ui_focus":
            title = args["title"]
            r = subprocess.run(["xdotool", "search", "--name", title, "windowactivate"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return f"Window focused: {title}"
            # fallback: coba windowactivate via classname
            r2 = subprocess.run(["xdotool", "search", "--class", title, "windowactivate"],
                                capture_output=True, text=True, timeout=10)
            if r2.returncode == 0 and r2.stdout.strip():
                return f"Window focused: {title}"
            return f"Window '{title}' not found. Gunakan --name atau --class."

        elif name == "usb_list":
            verbose = args.get("verbose", False)
            cmd = ["lsusb"] if not verbose else ["lsusb", "-v"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                out = r.stdout.strip()
                return out or "(no USB devices)"
            return f"lsusb error: {r.stderr}. Install usbutils: sudo apt install usbutils"

        elif name == "serial_send":
            port = args["port"]
            data = args["data"]
            baud = str(args.get("baud", 9600))
            timeout = args.get("read_timeout", 2)
            try:
                import serial
            except ImportError:
                return "pyserial tidak terinstall. Install: pip install pyserial"
            try:
                ser = serial.Serial(port, int(baud), timeout=timeout)
                ser.write(data.encode())
                response = b""
                import time as _time
                _time.sleep(0.5)
                while ser.in_waiting:
                    response += ser.read(ser.in_waiting)
                    _time.sleep(0.2)
                ser.close()
                resp_text = response.decode(errors="replace").strip()
                if resp_text:
                    return f"Sent: {data}\nResponse: {resp_text}"
                return f"Sent: {data} (no response)"
            except Exception as e:
                return f"Serial error: {e}"

        elif name == "camera_capture":
            device = args.get("device", "/dev/video0")
            path = args.get("path", "/tmp/joki_cam.jpg")
            resolution = args.get("resolution", "640x480")
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            # Try fswebcam first, then ffmpeg
            r = subprocess.run(["fswebcam", "-d", device, "-r", resolution, path],
                               capture_output=True, text=True, timeout=15)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return f"Camera capture saved: {path} ({os.path.getsize(path)} bytes)"
            r2 = subprocess.run(
                ["ffmpeg", "-f", "v4l2", "-i", device, "-vframes", "1", "-s", resolution, "-y", path],
                capture_output=True, text=True, timeout=15
            )
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return f"Camera capture saved: {path} ({os.path.getsize(path)} bytes)"
            return "Gagal capture kamera. Install fswebcam: sudo apt install fswebcam"

        elif name == "sandbox_run":
            code = args["code"]
            interpreter = args.get("interpreter", "auto")
            timeout = min(args.get("timeout", 15), 60)
            import tempfile, uuid
            sandbox_dir = os.path.join(tempfile.gettempdir(), f"joki_sandbox_{uuid.uuid4().hex[:8]}")
            os.makedirs(sandbox_dir, exist_ok=True)

            files_raw = args.get("files", "")
            if files_raw:
                for entry in files_raw.split("|"):
                    if "=" in entry:
                        fpath, fcontent = entry.split("=", 1)
                        fdest = os.path.join(sandbox_dir, fpath.strip())
                        os.makedirs(os.path.dirname(fdest), exist_ok=True)
                        with open(fdest, "w") as f:
                            f.write(fcontent)

            script_path = os.path.join(sandbox_dir, "script")
            ext_map = {"python3": ".py", "node": ".js", "bash": ".sh", "sh": ".sh", "auto": ""}

            if interpreter == "auto":
                if code.startswith("#!"):
                    interp_cmd = code.splitlines()[0].lstrip("#!").strip()
                    interpreter = "bash" if "bash" in interp_cmd or "sh" in interp_cmd else "python3" if "python" in interp_cmd else "node" if "node" in interp_cmd else "bash"
                elif any(kw in code for kw in ["import ", "def ", "class ", "print("]):
                    interpreter = "python3"
                elif any(kw in code for kw in ["require(", "module.exports", "console.log"]):
                    interpreter = "node"
                else:
                    interpreter = "bash"

            ext = ext_map.get(interpreter, "")
            script_path = os.path.join(sandbox_dir, f"script{ext}")
            with open(script_path, "w") as f:
                f.write(code)
            os.chmod(script_path, 0o755)

            try:
                r = subprocess.run(
                    [interpreter, script_path] if interpreter in ("python3", "node") else ["bash", script_path],
                    capture_output=True, text=True, timeout=timeout, cwd=sandbox_dir
                )
                output = r.stdout + r.stderr
                if not output.strip():
                    output = "(no output)"
                status = "SUCCESS" if r.returncode == 0 else f"FAILED (exit {r.returncode})"
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                return f"[SANDBOX] {status}\n{output.strip()}"
            except subprocess.TimeoutExpired:
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                return f"[SANDBOX] TIMEOUT (>{timeout}s)"
            except Exception as e:
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                return f"[SANDBOX] Error: {e}"

        elif name == "predict_command":
            cmd = args["cmd"]
            risks = []
            dangerous_patterns = [
                (r"\brm\s+-rf\b", "Menghapus file/direktori secara paksa (rm -rf) — data bisa hilang permanen"),
                (r"\bmv\s+", "Memindahkan file — bisa timpa file tujuan"),
                (r"\bdd\b", "Low-level disk operation — bisa merusak partisi jika salah"),
                (r"\bmkfs|mkfs\.|fdisk|parted", "Operasi partisi/format — bisa menghapus seluruh data"),
                (r"\bchmod\s+777", "Memberi izin akses penuh ke semua user — risiko keamanan"),
                (r"\bchown\b", "Mengubah kepemilikan file — bisa menyebabkan akses error"),
                (r":(){ :\|:& };:", "Fork bomb — bisa crash sistem"),
                (r">\s*/dev/", "Menulis langsung ke device — bisa merusak sistem"),
                (r"wget|curl.*\|.*sh", "Download dan pipe ke shell — risiko malware"),
                (r"sudo", "Menjalankan dengan hak akses root"),
                (r"apt install|apt-get install|pip install|npm install", "Menginstall package baru"),
                (r"systemctl (stop|disable|mask)", "Menghentikan/menonaktifkan service sistem"),
                (r"DROP TABLE|DELETE FROM|TRUNCATE", "Operasi database destruktif"),
                (r">\s+\S+\.(json|txt|py|js|yaml|conf|ini)", "Menimpa isi file (write)"),
            ]
            for pattern, desc in dangerous_patterns:
                if re.search(pattern, cmd, re.IGNORECASE):
                    risks.append(f"  ⚠ {desc}")
            if not risks:
                risks.append("  ✓ Tidak terdeteksi pola berbahaya")
            return f"Analisa perintah: `{cmd[:200]}`\n" + "\n".join(risks)

        elif name == "audio_info":
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", args["path"]],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                return f"Error: {r.stderr or 'ffprobe not found. Install: sudo apt install ffmpeg'}"
            data = json.loads(r.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            lines = [f"  File: {args['path']}"]
            lines.append(f"  Duration: {fmt.get('duration', 'N/A')}s")
            lines.append(f"  Size: {fmt.get('size', 'N/A')} bytes")
            lines.append(f"  Bitrate: {fmt.get('bit_rate', 'N/A')} bps")
            for s in streams:
                if s.get("codec_type") == "audio":
                    lines.append(f"  Codec: {s.get('codec_name', 'N/A')}")
                    lines.append(f"  Sample Rate: {s.get('sample_rate', 'N/A')} Hz")
                    lines.append(f"  Channels: {s.get('channels', 'N/A')}")
                    lines.append(f"  Language: {s.get('tags', {}).get('language', 'N/A')}")
            return "\n".join(lines)

        elif name == "audio_transcribe":
            path = args["path"]
            model_size = args.get("model", "base")
            language = args.get("language", "")
            if not os.path.exists(path):
                return f"File not found: {path}"
            try:
                import whisper
            except ImportError:
                return "whisper tidak terinstall. Install: pip install openai-whisper"
            with _Spinner(f"Transkripsi audio (model: {model_size})..."):
                model = whisper.load_model(model_size)
                opts = {"language": language} if language else {}
                result = model.transcribe(path, **opts)
            text = result.get("text", "").strip()
            detected = result.get("language", "")
            segments = result.get("segments", [])
            duration = segments[-1]["end"] if segments else 0
            info = f"  Bahasa: {detected.upper() if detected else 'auto'}"
            info += f"\n  Durasi: {duration:.1f}s" if duration else ""
            info += f"\n  Teks ({len(text)} chars):\n{text}"
            return info

        elif name == "video_info":
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", args["path"]],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                return f"Error: {r.stderr or 'ffprobe not found. Install: sudo apt install ffmpeg'}"
            data = json.loads(r.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            lines = [f"  File: {args['path']}"]
            lines.append(f"  Duration: {fmt.get('duration', 'N/A')}s")
            lines.append(f"  Size: {fmt.get('size', 'N/A')} bytes")
            lines.append(f"  Bitrate: {fmt.get('bit_rate', 'N/A')} bps")
            for s in streams:
                codec_type = s.get("codec_type", "unknown")
                lines.append(f"  [{codec_type}]")
                lines.append(f"    Codec: {s.get('codec_name', 'N/A')}")
                if codec_type == "video":
                    lines.append(f"    Resolution: {s.get('width', 'N/A')}x{s.get('height', 'N/A')}")
                    rate = s.get('r_frame_rate', '0/1')
                    if '/' in rate:
                        try:
                            num, den = rate.split('/')
                            fps = float(int(num) / int(den)) if int(den) else 0.0
                        except ValueError:
                            fps = 0.0
                    else:
                        try:
                            fps = float(rate)
                        except ValueError:
                            fps = 0.0
                    lines.append(f"    FPS: {fps:.2f}")
                    lines.append(f"    Pixel Format: {s.get('pix_fmt', 'N/A')}")
                elif codec_type == "audio":
                    lines.append(f"    Sample Rate: {s.get('sample_rate', 'N/A')} Hz")
                    lines.append(f"    Channels: {s.get('channels', 'N/A')}")
            return "\n".join(lines)

        elif name == "video_extract":
            path = args["path"]
            mode = args["mode"]
            output_dir = args.get("output_dir", "/tmp/joki_video_extract")
            os.makedirs(output_dir, exist_ok=True)
            if mode == "thumbnail":
                out = os.path.join(output_dir, "thumbnail.jpg")
                r = subprocess.run(
                    ["ffmpeg", "-i", path, "-vframes", "1", "-q:v", "2", "-y", out],
                    capture_output=True, text=True, timeout=30
                )
                if os.path.exists(out):
                    return f"Thumbnail saved: {out} ({os.path.getsize(out)} bytes)"
                return f"Error: {r.stderr}"
            elif mode == "timestamp":
                ts = args.get("timestamp", 0)
                out = os.path.join(output_dir, f"frame_{ts}s.jpg")
                r = subprocess.run(
                    ["ffmpeg", "-ss", str(ts), "-i", path, "-vframes", "1", "-q:v", "2", "-y", out],
                    capture_output=True, text=True, timeout=30
                )
                if os.path.exists(out):
                    return f"Frame at {ts}s saved: {out} ({os.path.getsize(out)} bytes)"
                return f"Error: {r.stderr}"
            elif mode == "frames":
                fps = args.get("fps", 1)
                out_pattern = os.path.join(output_dir, "frame_%04d.jpg")
                r = subprocess.run(
                    ["ffmpeg", "-i", path, "-vf", f"fps={fps}", "-q:v", "2", "-y", out_pattern],
                    capture_output=True, text=True, timeout=60
                )
                count = len([f for f in os.listdir(output_dir) if f.startswith("frame_")])
                return f"Extracted {count} frames to {output_dir}/ (fps={fps})"
            return f"Unknown mode: {mode}"

    except subprocess.TimeoutExpired:
        return "Error: command timed out (60s)"
    except subprocess.CalledProcessError as e:
        return f"Error: {e.output}"
    except Exception as e:
        return f"Error: {e}"
# ============================================================
# LLM CALL
# ============================================================
def call_llm(messages):
    global _current_model_config
    _joki_cancel.clear()
    _attempted_ids = set()  # track (base_url, model) tuples tried in this call

    for _attempt in range(20):  # safety limit
        mc = _current_model_config
        identity = (mc["base_url"], mc["model"])
        _attempted_ids.add(identity)

        all_keys = mc.get("api_keys") or [mc.get("api_key", "")]
        available = [(i, k) for i, k in enumerate(all_keys) if k not in _exhausted_keys and k]

        if not available:
            fallback = mc.get("fallback", "")
            if fallback and fallback in _MODELS:
                fb_id = (_MODELS[fallback]["base_url"], _MODELS[fallback]["model"])
                if fb_id not in _attempted_ids:
                    _current_model_config = dict(_MODELS[fallback])
                    _console.print(f"[yellow]⚠ Model fallback: {_current_model_config['name']} ({_current_model_config['model']})[/yellow]")
                    continue

            # coba model lain di config.json yang belum dicoba
            found_untried = False
            for km, vm in _MODELS.items():
                vid = (vm["base_url"], vm["model"])
                if vid not in _attempted_ids:
                    _current_model_config = dict(vm)
                    _console.print(f"[yellow]⚠ Model dicoba: {vm['name']} ({vm['model']})[/yellow]")
                    found_untried = True
                    break
            if found_untried:
                continue

            # semua model habis — tampilkan notifikasi
            from rich.panel import Panel
            _console.print(Panel(
                "[bold red]SEMUA MODEL HABIS QUOTA![/bold red]\n\n"
                "Semua API key di semua model yang tersedia sudah habis quota.\n"
                "Gunakan [bold]/reset_quota[/bold] untuk mereset status, atau\n"
                "isi API key baru di [bold]config.json[/bold].",
                title="😵 QUOTA HABIS",
                border_style="red"
            ))
            return {"role": "assistant", "content": "[ERROR] Semua model di config.json sudah habis quota. Gunakan /reset_quota untuk reset."}

        for key_idx, api_key in available:
            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            result = []
            error_data = []

            def _do_request(key=api_key, idx=key_idx, model_cfg=mc):
                try:
                    is_openai = model_cfg["provider"] == "openai"
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["Authorization"] = f"Bearer {key}"
                    if is_openai:
                        url = f"{model_cfg['base_url']}/chat/completions"
                        body = {"model": model_cfg["model"], "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": 4096}
                    else:
                        url = f"{model_cfg['base_url']}/api/chat"
                        body = {"model": model_cfg["model"], "messages": messages, "tools": TOOLS, "stream": False, "max_tokens": 4096}
                    r = httpx.post(url, json=body, headers=headers, timeout=120, follow_redirects=True)
                    data = r.json()
                    if r.status_code != 200:
                        raise httpx.HTTPStatusError(f"{data}", request=r.request, response=r)
                    err_info = data.get("error") or data.get("error_code")
                    if err_info:
                        raise httpx.HTTPStatusError(f"{err_info}", request=r.request, response=r)
                    if is_openai:
                        result.append(data["choices"][0]["message"])
                    else:
                        result.append(data["message"])
                except Exception as e:
                    err_resp = getattr(e, "response", None)
                    if err_resp is not None:
                        status = err_resp.status_code
                        body_lower = err_resp.text.lower()
                        is_quota = (
                            status in (429, 402) or
                            any(w in body_lower for w in ["quota", "rate limit", "exhausted",
                                                          "insufficient", "limit reached",
                                                          "too many requests", "billing"])
                        )
                        if is_quota:
                            error_data.append(("quota", f"Key #{idx+1} quota exhausted"))
                        else:
                            error_data.append(("err", f"HTTP {status}: {err_resp.text[:300]}"))
                    else:
                        error_data.append(("err", str(e)))

            req = threading.Thread(target=_do_request, daemon=True)
            req.start()

            with _Spinner("Joki memproses..."):
                while req.is_alive():
                    if _joki_cancel.is_set():
                        req.join(1)
                        break
                    req.join(timeout=0.1)

            if _joki_cancel.is_set():
                return {"role": "assistant", "content": "[CANCELLED] Permintaan dibatalkan oleh pengguna."}

            if error_data:
                etype, emsg = error_data[0]
                if etype == "quota":
                    _exhausted_keys.add(api_key)
                    continue
                return {"role": "assistant", "content": f"[ERROR] LLM call failed: {emsg}"}

            return result[0]

    return {"role": "assistant", "content": "[ERROR] Max attempts reached."}


# ============================================================
# STREAMING & DISPLAY
# ============================================================
_TOOL_LABEL = {
    "read_file": "Membaca file",
    "write_file": "Menulis file",
    "edit_file": "Mengedit file",
    "run_command": "Menjalankan perintah",
    "search_code": "Mencari kode",
    "list_dir": "Melihat isi direktori",
    "db_query": "Menjalankan query database",
    "service_control": "Mengelola service",
    "config_edit": "Mengedit konfigurasi",
    "package_check": "Memeriksa paket",
    "web_fetch": "Mengambil konten web",
    "web_search": "Mencari di web",
    "test_and_fix": "Mengetes dan memperbaiki",
    "memory_store": "Menyimpan memori",
    "memory_recall": "Mengambil memori",
    "memory_forget": "Menghapus memori",
    "screenshot": "Mengambil screenshot",
    "port_scan": "Port scanning",
    "dns_enum": "DNS enumeration",
    "web_vuln_scan": "Web vulnerability scan",
    "whois_lookup": "WHOIS lookup",
    "ssl_check": "SSL/TLS check",
    "dir_bruteforce": "Directory brute-force",
    "cve_search": "CVE search",
    "tech_detect": "Technology detection",
    "js_analyze": "JavaScript analysis",
    "api_discover": "API discovery",
    "source_map_check": "Source map check",
    "form_analyze": "Form analysis",
    "apk_analyze": "APK analysis",
    "binary_analyze": "Binary analysis",
    "todo_create": "Membuat TODO list",
    "todo_done": "Menyelesaikan item TODO",
    "todo_show": "Menampilkan TODO list",
    "ui_screenshot": "Screenshot UI",
    "ui_click": "Klik mouse",
    "ui_type": "Mengetik teks",
    "ui_keypress": "Tekan keyboard",
    "ui_focus": "Fokus window",
    "usb_list": "Daftar USB",
    "serial_send": "Kirim serial",
    "camera_capture": "Capture kamera",
    "sandbox_run": "Sandbox execution",
    "predict_command": "Prediksi perintah",
    "audio_info": "Info audio",
    "audio_transcribe": "Transkripsi audio",
    "video_info": "Info video",
    "video_extract": "Ekstrak video",
}

class _Spinner:
    def __init__(self, message="Processing"):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self):
        fd = sys.stdin.fileno()
        old = None
        if _HAS_TTY:
            try:
                old = termios.tcgetattr(fd)
                tty.setraw(fd, termios.TCSANOW)
            except Exception:
                old = None

        try:
            esc_count = 0
            last_esc = 0.0

            while not self._stop.is_set():
                for c in '|/-\\':
                    if self._stop.is_set():
                        break
                    sys.stdout.write(f'\r  {c} {self.message}... ')
                    sys.stdout.flush()

                    now = time.time()
                    if now - last_esc > 1.0:
                        esc_count = 0

                    if old is not None and select.select([sys.stdin], [], [], 0.05)[0]:
                        key = sys.stdin.read(1)
                        if key == '\x1b':
                            esc_count += 1
                            last_esc = now
                            if esc_count >= 2:
                                _joki_cancel.set()
                                self._stop.set()
                                return
                        elif key == '\x03':
                            raise KeyboardInterrupt
                        else:
                            esc_count = 0
                    else:
                        time.sleep(0.05)
        finally:
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, old)
                except Exception:
                    pass

    def __exit__(self, *args):
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write('\r' + ' ' * (len(self.message) + 10) + '\r')
        sys.stdout.flush()

_console = Console()

_LATEX_REPLACE = {
    r"$\rightarrow$": "→",
    r"$\Rightarrow$": "⇒",
    r"$\gets$": "←",
    r"$\leftarrow$": "←",
    r"$\Leftarrow$": "⇐",
    r"$\mapsto$": "↦",
    r"$\implies$": "⇒",
    r"$\iff$": "⇔",
    r"$\to$": "→",
    r"$\ge$": "≥",
    r"$\le$": "≤",
    r"$\neq$": "≠",
    r"$\approx$": "≈",
    r"$\equiv$": "≡",
    r"$\cdot$": "·",
    r"$\times$": "×",
    r"$\alpha$": "α",
    r"$\beta$": "β",
    r"$\gamma$": "γ",
    r"$\delta$": "δ",
    r"$\epsilon$": "ε",
    r"$\lambda$": "λ",
    r"$\mu$": "μ",
    r"$\pi$": "π",
    r"$\theta$": "θ",
    r"$\omega$": "ω",
    r"$\sigma$": "σ",
    r"$\phi$": "φ",
    r"$\dots$": "...",
    r"$\ldots$": "...",
    r"$\infty$": "∞",
    r"$\sum$": "Σ",
    r"$\prod$": "Π",
    r"$\sqrt{x}$": "√(x)",
}

def _clean_latex(text):
    if "$" not in text:
        return text
    for latex, plain in _LATEX_REPLACE.items():
        text = text.replace(latex, plain)
    text = re.sub(r"\$\$\\sqrt\{([^}]*)\}\$\$", r"√(\1)", text)
    text = re.sub(r"\$\\sqrt\{([^}]*)\}\$", r"√(\1)", text)
    text = re.sub(r"\$\$\\frac\{([^}]*)\}\{([^}]*)\}\$\$", r"(\1)/(\2)", text)
    text = re.sub(r"\$\\frac\{([^}]*)\}\{([^}]*)\}\$", r"(\1)/(\2)", text)
    return text

def _is_markdown(text):
    if not text or len(text) < 3:
        return False
    lines = text.strip().splitlines()
    if not lines:
        return False
    md_patterns = 0
    for line in lines[:15]:
        stripped = line.strip()
        if stripped.startswith(("# ", "## ", "### ", "#### ", "##### ", "###### ")):
            md_patterns += 2
        elif stripped.startswith(("- ", "* ", "+ ")) or stripped.startswith("1. "):
            md_patterns += 1
        elif stripped.startswith("```") or stripped.endswith("```"):
            md_patterns += 2
        elif "**" in stripped or "__" in stripped:
            md_patterns += 1
        elif "`" in stripped and len(stripped) > 10:
            md_patterns += 1
        elif "|" in stripped and "-" in stripped and "---" in stripped:
            md_patterns += 2
        elif re.search(r'\[.*\]\(.*\)', stripped):
            md_patterns += 1
    return md_patterns >= 2

def stream_print(text, delay=0.008):
    if not text:
        return
    fence_parts = re.split(r'(```[\s\S]*?```)', text)
    items = []
    for part in fence_parts:
        if part.startswith('```') and part.endswith('```'):
            lines = part.splitlines()
            info = lines[0].lstrip('`').strip() if lines else ''
            code = '\n'.join(lines[1:-1]) if len(lines) > 2 else ''
            lang = info.split()[0] if info else "text"
            items.append(Syntax(code, lang, line_numbers=True, word_wrap=True))
        else:
            sub = re.split(r'(`[^`\n]+`)', part)
            for s in sub:
                if s.startswith('`') and s.endswith('`'):
                    items.append(Markdown(s))
                elif s.strip():
                    items.append(Markdown(_clean_latex(s)))
    if not items:
        return
    try:
        if len(items) == 1:
            _console.print(items[0])
        else:
            _console.print(Group(*items))
    except KeyboardInterrupt:
        raise

def _run_auto_test(full_cmd, base_timeout=60, idle_extension=30, max_timeout=600):
    """Jalankan perintah dengan timeout yang diperpanjang otomatis jika masih memproduksi output.
    
    - base_timeout: waktu tunggu awal sebelum ekstensi dimulai
    - idle_extension: setiap kali ada output baru, deadline ditambah segini
    - max_timeout: batas absolut maksimum
    """
    proc = subprocess.Popen(
        full_cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1
    )

    out_lines, err_lines = [], []
    lock = threading.Lock()
    last_output_time = [time.time()]

    def _pipe_reader(pipe, store):
        try:
            for line in iter(pipe.readline, ''):
                with lock:
                    store.append(line)
                    last_output_time[0] = time.time()
        except ValueError:
            pass
        finally:
            pipe.close()

    t_out = threading.Thread(target=_pipe_reader, args=(proc.stdout, out_lines), daemon=True)
    t_err = threading.Thread(target=_pipe_reader, args=(proc.stderr, err_lines), daemon=True)
    t_out.start()
    t_err.start()

    start = time.time()
    deadline = start + base_timeout
    max_deadline = start + max_timeout
    timed_out = False

    while proc.poll() is None:
        now = time.time()
        if now >= max_deadline:
            timed_out = True
            break
        if now >= deadline:
            with lock:
                idle = now - last_output_time[0]
            if idle >= idle_extension:
                timed_out = True
                break
            else:
                deadline = now + (idle_extension - idle)
        time.sleep(0.5)

    if timed_out:
        proc.kill()
    proc.wait(timeout=10)

    with lock:
        output = ''.join(out_lines) + ''.join(err_lines)
    return_code = proc.returncode if not timed_out else None
    return return_code, output, timed_out


# ============================================================
# AGENT LOOP (single turn)
# ============================================================
def agent_loop(messages):
    for i in range(25):
        _console.rule("[bold cyan]JOKI[/bold cyan]")
        msg = call_llm(messages)
        messages.append(msg)

        content = (msg.get("content") or "")
        if content.startswith("[CANCELLED]"):
            _console.print("[bold yellow]Dibatalkan oleh pengguna.[/bold yellow]")
            return

        if msg.get("tool_calls"):
            if content and not content.strip().startswith("[RENCANA]"):
                stream_print(content)

            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                raw = tc["function"]["arguments"]
                args = json.loads(raw) if isinstance(raw, str) else raw

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

                try:
                    result = execute(name, args)
                except Exception as ex:
                    result = f"[ERROR] Exception saat mengeksekusi {name}: {ex}"
                if result:
                    stream_print(f"       ```\n{result}\n       ```", delay=0.001)
                messages.append({
                    "role": "tool",
                    "content": (result or "")[:10000],
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
        else:
            content = (msg.get("content") or "")
            if content and not content.strip().startswith("[RENCANA]"):
                stream_print(content)
            if content and ("run_command(" in content or "write_file(" in content or "edit_file(" in content or "service_control(" in content):
                messages.append({
                    "role": "user",
                    "content": "Jangan tulis tool sebagai teks. EKSEKUSI tool di atas menggunakan function calling API sekarang."
                })
                continue
            if not content.strip():
                messages.append({
                    "role": "user",
                    "content": "Respons kamu kosong. Berikan respons atau panggil tool yang sesuai. Jangan diam saja — kerjakan task-nya."
                })
                continue
            return
    stream_print(f"\n[INFO] Max iterations reached (25).")
    _console.rule(style="cyan")


# ============================================================
# SESSION MANAGEMENT
# ============================================================
SESSION_DIR = _get_data_dir()
LOG_DIR = os.path.join(_get_data_dir(), "logs")

def _session_path(name):
    return os.path.join(SESSION_DIR, f"{name}.json")

def _log_path(name):
    return os.path.join(LOG_DIR, f"{name}.log")

def auto_save_log(messages, name):
    """Simpan percakapan ke logs/<session_name>.log dalam format readable."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = _log_path(name)
    ts = subprocess.run(["date", "+%Y-%m-%d %H:%M:%S"], capture_output=True, text=True).stdout.strip()
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"  JOKI SESSION LOG")
    lines.append(f"  Session: {name}")
    lines.append(f"  Date: {ts}")
    lines.append(f"{'='*60}\n")
    role_label = {"system": "SYSTEM", "user": "USER", "assistant": "JOKI", "tool": "TOOL"}
    for msg in messages:
        role = msg.get("role", "unknown")
        content = _clean_latex((msg.get("content") or ""))
        tool_calls = msg.get("tool_calls")
        label = role_label.get(role, role.upper())
        if role == "system":
            continue
        if tool_calls:
            for tc in tool_calls:
                fn = tc["function"]
                lines.append(f"[JOKI → {fn['name']}]")
                args_str = fn.get("arguments", "")
                if isinstance(args_str, str) and len(args_str) > 200:
                    args_str = args_str[:200] + "..."
                lines.append(f"  args: {args_str}")
            lines.append("")
        elif content:
            for c in content.splitlines():
                lines.append(f"[{label}] {c}")
            lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path

def save_session(messages, name=None):
    global _CURRENT_SESSION
    os.makedirs(SESSION_DIR, exist_ok=True)
    if not name:
        ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
        name = f"session_{ts}"
    path = _session_path(name)
    with open(path, "w") as f:
        json.dump({"messages": messages}, f, indent=2)
    _CURRENT_SESSION = name
    mem = _load_memory()
    if mem:
        _save_memory(mem, name=name)
    auto_save_log(messages, name)
    return name, path

def _load_session_data(name):
    path = _session_path(name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def list_sessions():
    os.makedirs(SESSION_DIR, exist_ok=True)
    files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")]
    if not files:
        return "(no saved sessions)"
    files.sort(key=lambda f: os.path.getmtime(os.path.join(SESSION_DIR, f)), reverse=True)
    lines = []
    for i, f in enumerate(files, 1):
        fname = f.replace(".json", "")
        size = os.path.getsize(os.path.join(SESSION_DIR, f))
        mod = os.path.getmtime(os.path.join(SESSION_DIR, f))
        lines.append(f"  {i:>2}. {fname}  ({size}b, {datetime.fromtimestamp(mod).strftime('%Y-%m-%d %H:%M')})")
    return "\n".join(lines), files

def view_session_history(name):
    data = _load_session_data(name)
    if data is None:
        return f"Session '{name}' not found."
    messages = data.get("messages", [])
    if not messages:
        return "(empty session)"
    console = Console()
    output = []
    role_label = {"system": "SYSTEM", "user": "USER", "assistant": "JOKI", "tool": "TOOL"}
    role_color = {"system": "dim", "user": "yellow", "assistant": "cyan", "tool": "magenta"}
    for msg in messages:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "")
        tool_calls = msg.get("tool_calls")
        label = role_label.get(role, role.upper())
        color = role_color.get(role, "white")
        if role == "system":
            continue
        if tool_calls:
            output.append(f"[{label}]")
            for tc in tool_calls:
                fn = tc["function"]
                args_str = fn.get("arguments", "")
                if isinstance(args_str, str) and len(args_str) > 300:
                    args_str = args_str[:300] + "..."
                output.append(f"  \u2192 {fn['name']}({args_str})")
        elif content:
            output.append(f"[{label}]")
            for line in content.strip().splitlines():
                output.append(f"  {line}")
        output.append("")
    return "\n".join(output)

# ============================================================
# MAIN
# ============================================================
_SYSTEM_PROMPT_BASE = (
    "Kamu adalah Joki — AI agent yang dibuat oleh Rahmad Budiman. Jika ditanya siapa yang membuat atau menciptakan kamu, jawab: 'Saya dibuat oleh Rahmad Budiman.'\n"
    "Aturan utama: JANGAN PERNAH BERHENTI DI TENGAH JALAN. Kerjakan task sampai tuntas "
    "dalam satu sesi — jangan minta konfirmasi, jangan ngasih laporan parsial, "
    "jangan nanya 'mau dilanjutkan?' LANJUTKAN TERUS sampai dapat hasil akhir atau error fatal.\n\n"
    "SEBELUM MENGERJAKAN APAPUN, buat TODO list dulu menggunakan todo_create — "
    "rinci langkah-langkah yang akan dilakukan. Setelah satu langkah selesai, "
    "tandai dengan todo_done. Gunakan todo_show untuk cek progress.\n\n"
    "PENTING: Setelah MENYELESAIKAN semua TODO, jangan diam saja. "
    "Buat ringkasan naratif dari hasil kerja — jelaskan apa yang dilakukan "
    "dan hasilnya dalam bahasa yang mudah dipahami pengguna. Jangan hanya "
    "menampilkan output tool mentah atau isi TODO list.\n\n"
    "FILE SEMENTARA: Jika membuat script sebagai alat bantu (misal script Python "
    "untuk ngecek API key, parsing data, dll.), simpan di /tmp/ JANGAN di "
    "direktori kerja. Setelah tugas selesai, hapus file tersebut pakai "
    "run_command(\"rm /tmp/namafile\").\n\n"
    "Keluarkan [RENCANA] sebagai teks (2-3 baris), lalu KIRIMKAN tool_calls SEBENARNYA (fungsi) — jangan tulis deskripsi tool sebagai teks.\n"
    "PENTING: tool_calls harus dikirim sebagai struktur data fungsi, BUKAN ditulis manual sebagai teks.\n"
    "Contoh: content=\"[RENCANA] Cek MySQL\" + tool_calls=run_command(...)\n\n"
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
    "  - read_file / write_file / edit_file / search_code / list_dir\n"
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
     "  - todo_create / todo_done / todo_show — buat dan kelola TODO list\n\n"
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

def _build_system_prompt():
    base = _SYSTEM_PROMPT_BASE
    memories = _load_memory()
    if memories:
        items = "\n".join(f"  - {k}: {v[:120]}" for k, v in memories.items())
        base += f"\n\nMemori tersimpan ({len(memories)}):\n{items}\n\nGunakan memory_recall untuk detail, memory_store untuk menyimpan info baru."
    return base

def main():
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
        return

    ts = subprocess.run(["date", "+%Y%m%d_%H%M%S"], capture_output=True, text=True).stdout.strip()
    _CURRENT_SESSION = f"session_{ts}"

    _console.print()
    _console.rule("[bold]JOKI[/bold]", style="cyan")
    _console.print(f"  Session: [cyan]{_CURRENT_SESSION}[/cyan]", style="dim")
    _console.print(f"  Model: [cyan]{_current_model_config['name']}[/cyan] [dim]({_current_model_config['model']})[/dim]", style="dim")
    _console.print("  [bold]/model[/bold] — ganti model  |  [bold]/sessions[/bold] [bold]/view[/bold] [bold]/new[/bold] [bold]/exit[/bold]", style="dim")
    _console.rule(style="dim")

    messages = [{"role": "system", "content": _build_system_prompt()}]

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    session = PromptSession(key_bindings=bindings)

    while True:
        try:
            user_input = session.prompt("joki> ")
        except (EOFError, KeyboardInterrupt):
            print()
            save_session(messages)
            _console.print(f"[dim]Percakapan tersimpan: logs/{_CURRENT_SESSION}.log[/dim]")
            _close_shell()
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.strip().split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            now = __import__("datetime").datetime.now()

            if cmd == "/sessions":
                out, files = list_sessions()
                print(out)

            elif cmd == "/view":
                if not arg:
                    print("  Usage: /view <session_name_or_number>")
                    print("  Gunakan /sessions untuk lihat daftar.")
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

            elif cmd in ("/exit", "/quit"):
                save_session(messages)
                _console.print(f"[dim]Percakapan tersimpan: logs/{_CURRENT_SESSION}.log[/dim]")
                _close_shell()
                break

            elif cmd == "/new":
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
                    if cfg.get("provider") == "openai" and not any(keys):
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
