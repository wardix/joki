from joki.state import *

MAX_TOKENS = 128000  # default context window if model doesn't have "context_window" in config.json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read content of an existing file. Gunakan offset dan limit untuk baca sebagian (mirip head/tail).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "offset": {"type": "integer", "description": "Baris awal (1-indexed, default: 1)"},
                    "limit": {"type": "integer", "description": "Jumlah baris maksimal (default: semua)"},
                    "force_full": {"type": "boolean", "description": "True untuk baca file utuh tanpa chunking/paging (bisa besar, hati-hati)"}
                },
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
            "description": "Search and replace text in an existing file. Supports fuzzy matching — whitespace differences otomatis dinormalisasi. WAJIB read_file dulu. old_text harus UNIK min 3-5 baris konteks. Hasil merusak sintaks otomatis di-rollback. Setelah sukses, linter file + test pendamping (bila ada) dijalankan otomatis sebagai verifikasi. Untuk refactor besar: panggil analyze_deps + impact_analysis dulu, dan pakai rename_symbol untuk rename (jangan edit manual per file).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Text to replace (unik, min 3-5 baris konteks). Tidak perlu whitespace-eksak — sistem akan fuzzy match."},
                    "new_text": {"type": "string", "description": "Replacement text"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "undo_edit",
            "description": "Undo/rollback perubahan terakhir pada file. Membutuhkan backup_path dari hasil edit_file/config_edit sebelumnya.",
            "parameters": {
                "type": "object",
                "properties": {
                    "backup_path": {"type": "string", "description": "Path ke file backup (didapat dari hasil edit_file atau config_edit)"},
                    "path": {"type": "string", "description": "Original file path (opsional, akan dideteksi otomatis dari backup_path jika tidak disertakan)"}
                },
                "required": ["backup_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run any shell command. Gunakan untuk: psql, mongosh, apachectl, nginx, docker, git, apt, systemctl, dsb. PENTING: untuk perintah yang butuh admin/root, WAJIB tambahkan prefix 'sudo '. Contoh: 'sudo apt update', 'sudo systemctl restart nginx'. Parameter timeout (ms), cwd, dan isInteractive tersedia untuk kontrol lebih lanjut.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command"},
                    "timeout": {"type": "integer", "description": "Timeout dalam milidetik (default: 600000 = 10 menit, max: 600000 / 10 menit)"},
                    "idle_timeout": {"type": "integer", "description": "Deteksi macet: durasi dalam ms tanpa output apapun sebelum command dianggap stuck (kemungkinan menunggu input interaktif). Default: 60000 (60 detik). Perintah yang memang lama diam secara wajar (npm install, docker pull, build) biasanya tetap mengeluarkan output berkala jadi tidak kena; jika ragu set 0 untuk menonaktifkan atau perbesar nilainya."},
                    "cwd": {"type": "string", "description": "Working directory (default: direktori aktif saat ini)"},
                    "isInteractive": {"type": "boolean", "description": "Jika true, jalankan dalam mode interaktif (default: false)"},
                    "confirmed": {"type": "boolean", "description": "Fallback langsung tanpa manusia (mis. test). Di alur agent JANGAN panggil ulang dengan confirmed=true — cukup panggil sekali, manusia akan ditanya Y/N (terminal) atau dialog Ya/Tidak (TUI: sekali / sesi / batal), lalu eksekusi langsung jalan."}
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for pattern across files (regex supported, ripgrep bila tersedia, hormati .gitignore). Untuk pertanyaan semantik (definisi/referensi/rename) pakai lsp_query/findReferences/rename_symbol yang jauh lebih akurat.",
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
            "name": "glob",
            "description": "Find files by glob pattern. Contoh: '**/*.py', 'src/**/*.ts', '*.json'. Support recursive dengan **.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, misal '**/*.py'"},
                    "path": {"type": "string", "description": "Directory to search (default: current)"}
                },
                "required": ["pattern"]
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
            "description": "Ambil konten dari URL, hasil dalam format Markdown (HTML otomatis dikonversi).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Complete URL (https://...)"},
                    "format": {"type": "string", "enum": ["markdown", "text"], "description": "Output format: markdown (default) atau text (HTML mentah)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_scrape",
            "description": "Ambil/menggali konten dari website tertentu secara cerdas. Ekstrak artikel berita, konten utama, atau data dari halaman web. Support CSS selector untuk ambil bagian spesifik, dan filter topik untuk cari konten relevan dari halaman besar. Bisa pake session setelah login. Contoh: web_scrape(url='https://detik.com'), web_scrape(url='https://cnnindonesia.com', topic='teknologi'), web_scrape(url='https://example.com', use_session=True)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL website yang ingin diambil kontennya"},
                    "topic": {"type": "string", "description": "Filter konten berdasarkan topik/kata kunci (opsional)"},
                    "selector": {"type": "string", "description": "CSS selector untuk ambil bagian spesifik halaman (opsional)"},
                    "use_session": {"type": "boolean", "description": "Gunakan session login yang sudah tersimpan (default: false). Login dulu dengan web_login()."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_login",
            "description": "Login ke website yang butuh autentikasi. Deteksi form login otomatis (termasuk CSRF token). Simpan session/cookie buat dipake web_scrape berikutnya dengan use_session=True. Contoh: web_login(url='https://example.com/login', username='user@email.com', password='pass123')",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL halaman login website"},
                    "username": {"type": "string", "description": "Username atau email untuk login"},
                    "password": {"type": "string", "description": "Password untuk login"},
                    "username_field": {"type": "string", "description": "Nama field username di form (opsional, auto-detect)"},
                    "password_field": {"type": "string", "description": "Nama field password di form (opsional, auto-detect)"}
                },
                "required": ["url", "username", "password"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_logout",
            "description": "Hapus session login yang tersimpan. Kalo dikasih URL, logout dari website itu aja. Kalo kosong, logout semua. Contoh: web_logout(), web_logout(url='https://example.com')",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL website yang mau di-logout (opsional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_sessions",
            "description": "Lihat daftar session login yang aktif saat ini.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Cari di web. Sumber: auto (TinyFish > Tavily > Brave > Google > DDG, default), tinyfish, tavily, brave, duckduckgo, google. Hasil markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "source": {"type": "string", "enum": ["auto", "tinyfish", "tavily", "brave", "duckduckgo", "google"], "description": "Sumber pencarian (default: auto)"},
                    "max_results": {"type": "integer", "description": "Jumlah hasil maksimal (default: 5, max 20)"}
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
                    "path": {"type": "string", "description": "Path untuk menyimpan screenshot (opsional, default: temp_dir/joki_screenshot_<timestamp>.png)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "port_scan",
            "description": "Scan port terbuka pada target. Gunakan untuk penetration testing — cek service apa saja yang berjalan, deteksi port tidak aman yang terbuka. WAJIB punya izin tertulis untuk men-scan target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address atau hostname target (contoh: 192.168.1.1, scanme.nmap.org)"},
                    "ports": {"type": "string", "description": "Range port (contoh: '22,80,443', '1-1000', 'common'). Default: common ports (1-1024 + service umum)"},
                    "scan_type": {"type": "string", "enum": ["tcp", "syn", "udp", "quick"], "description": "Tipe scan. 'tcp'=TCP connect, 'syn'=SYN stealth (butuh root), 'quick'=port terkenal saja. Default: quick"},
                    "target_authorized": {"type": "boolean", "description": "Fallback langsung tanpa manusia. Di alur agent JANGAN panggil ulang hanya untuk set flag ini — cukup panggil sekali, manusia akan ditanya (terminal/TUI) lalu eksekusi langsung jalan."}
                },
                "required": ["target", "target_authorized"]
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
            "description": "Web vulnerability scan: cek security headers, SQL injection (basic), XSS refleksi, directory traversal, informasi server. Untuk penetration testing. WAJIB punya izin tertulis untuk men-scan target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL target lengkap (contoh: https://example.com)"},
                    "checks": {"type": "string", "description": "Jenis cek: 'headers' (security headers), 'sqli' (SQL injection basic), 'xss' (XSS refleksi), 'info' (informasi server), 'all'. Default: headers,info"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"},
                    "target_authorized": {"type": "boolean", "description": "Fallback langsung tanpa manusia. Di alur agent JANGAN panggil ulang hanya untuk set flag ini — cukup panggil sekali, manusia akan ditanya (terminal/TUI) lalu eksekusi langsung jalan."}
                },
                "required": ["url", "target_authorized"]
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
            "description": "Bruteforce directory/file pada web server menggunakan wordlist. Temukan hidden paths, admin panel, backup file, dsb. Untuk penetration testing. WAJIB punya izin tertulis untuk men-scan target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL (contoh: https://example.com)"},
                    "wordlist": {"type": "string", "description": "Path ke wordlist atau ukuran wordlist: 'small' (100 paths), 'medium' (1000), 'large' (5000). Default: small"},
                    "extensions": {"type": "string", "description": "Ekstensi file yang dicari (contoh: 'php,txt,zip,bak'). Default: tidak ada"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"},
                    "target_authorized": {"type": "boolean", "description": "Fallback langsung tanpa manusia. Di alur agent JANGAN panggil ulang hanya untuk set flag ini — cukup panggil sekali, manusia akan ditanya (terminal/TUI) lalu eksekusi langsung jalan."}
                },
                "required": ["url", "target_authorized"]
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
                    "query": {"type": "string", "description": "Keyword pencarian (contoh: 'apache 2.4.49', 'nginx 1.20', 'openssh 8.9')"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
                    "deep": {"type": "string", "enum": ["simple", "deep"], "description": "'simple'=header+cookie saja, 'deep'=analisa HTML+JS juga. Default: simple"},
                    "timeout": {"type": "number", "description": "Timeout dalam detik. Default: 60"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
                    "extract": {"type": "string", "enum": ["endpoints", "secrets", "all"], "description": "'endpoints'=API path/URL saja, 'secrets'=key/token/password, 'all'=keduanya. Default: all"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
                    "depth": {"type": "integer", "description": "Kedalaman: 1=halaman utama saja, 2=+JS files, 3=+subpages. Default: 2"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
                    "url": {"type": "string", "description": "URL target (contoh: https://example.com)"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
                    "url": {"type": "string", "description": "URL target (contoh: https://example.com/login)"},
                    "skip_ssl_verify": {"type": "boolean", "description": "Lewati verifikasi SSL/TLS (berguna untuk target dengan self-signed certificate). Default: false"}
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
            "description": "Buat Rencana Pengerjaan untuk task yang akan dikerjakan. Panggil di awal sebelum mulai mengerjakan sesuatu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Daftar item Rencana Pengerjaan (masing-masing berupa string langkah)"
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
            "description": "Tandai satu atau lebih item Rencana Pengerjaan sebagai selesai.",
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
            "description": "Tampilkan Rencana Pengerjaan saat ini.",
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
                        "description": "Path untuk menyimpan screenshot (default: temp_dir/joki_ui_screen.png)"
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
                    "path": {"type": "string", "description": "Path output (default: temp_dir/joki_cam.jpg)"},
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
            "name": "lsp_query",
            "description": "Dapatkan informasi kode secara semantik via LSP: cari definisi fungsi, referensi, tipe data, lihat struktur file, atau rename simbol. WAJIB dipakai sebelum refactor besar (findReferences untuk pemanggil, rename untuk ganti nama). Jauh lebih akurat daripada search_code untuk pertanyaan semantik.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["goToDefinition", "findReferences", "hover", "documentSymbol", "workspaceSymbol", "rename", "codeActions", "format"],
                        "description": "Operasi LSP: goToDefinition=cari definisi, findReferences=cari semua pemanggil, hover=info tipe data, documentSymbol=struktur file, workspaceSymbol=cari simbol di seluruh project, rename=ganti nama simbol, codeActions=lihat quickfix/refactor yang tersedia, format=format otomatis file"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path file target"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Nama simbol/fungsi/variable yang dicari (untuk goToDefinition, findReferences, hover, workspaceSymbol)"
                    },
                    "line": {
                        "type": "integer",
                        "description": "Baris posisi kursor (0-indexed, optional)"
                    },
                    "character": {
                        "type": "integer",
                        "description": "Kolom posisi kursor (0-indexed, optional)"
                    },
                    "newName": {
                        "type": "string",
                        "description": "Nama baru untuk operasi rename (wajib jika operation='rename')"
                    }
                },
                "required": ["operation", "file_path"]
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
            "name": "read_office",
            "description": "Baca file Office: DOCX, XLSX, PPTX, PDF, CSV. Output dalam format teks yang bisa dibaca.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path ke file office (.docx, .xlsx, .pptx, .pdf, .csv)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_office",
            "description": "Buat file Office baru: DOCX, XLSX, PPTX, CSV. Content berupa teks (baris dipisah newline). Untuk XLSX bisa pake parameter 'rows' (JSON array of arrays) atau 'sheet_name'. Untuk DOCX bisa pake 'tables' (JSON array of arrays). Untuk PPTX, pisah slide dengan '---'. Contoh: write_office(path='test.docx', content='Halo\\nDunia', tables='[[\"a\",\"b\"],[\"1\",\"2\"]]')",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path untuk file baru (.docx, .xlsx, .pptx, .csv)"},
                    "content": {"type": "string", "description": "Teks konten. Baris dipisah newline. Untuk PPTX, pisah slide dengan '---'."},
                    "sheet_name": {"type": "string", "description": "Nama sheet untuk XLSX (default: Sheet1)"},
                    "rows": {"type": "string", "description": "JSON array of arrays untuk data XLSX/CSV (contoh: '[[\"a\",\"b\"],[\"1\",\"2\"]]')"},
                    "tables": {"type": "string", "description": "JSON array of arrays untuk tabel di DOCX"},
                    "title": {"type": "string", "description": "Judul slide pertama untuk PPTX (default: Presentation)"}
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
                        "description": "Direktori output (default: temp_dir/joki_video_extract/)"
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
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Tampilkan status git repository: modified, staged, untracked files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Lihat perubahan file (diff) di git. Bisa untuk staged/unstaged atau file spesifik.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "staged": {"type": "boolean", "description": "Lihat diff untuk staged files (default: false)"},
                    "path": {"type": "string", "description": "Filter untuk file tertentu (opsional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Lihat riwayat commit git. Bisa diffilter branch dan jumlah.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "max_count": {"type": "integer", "description": "Jumlah commit maksimal (default: 10)"},
                    "format": {"type": "string", "enum": ["oneline", "full"], "description": "Format output: oneline atau full (default: oneline)"},
                    "branch": {"type": "string", "description": "Branch filter (opsional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Buat commit baru dengan pesan tertentu. Gunakan setelah git_add.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "message": {"type": "string", "description": "Pesan commit (wajib)"},
                    "no_verify": {"type": "boolean", "description": "Skip pre-commit hooks (default: false)"}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push commit ke remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "remote": {"type": "string", "description": "Nama remote (default: origin)"},
                    "branch": {"type": "string", "description": "Branch yang di-push (default: branch aktif)"},
                    "force": {"type": "boolean", "description": "Force push (default: false)"},
                    "set_upstream": {"type": "boolean", "description": "Set upstream (-u) (default: false)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "Pull perubahan terbaru dari remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "remote": {"type": "string", "description": "Nama remote (default: origin)"},
                    "branch": {"type": "string", "description": "Branch yang di-pull (default: branch aktif)"},
                    "rebase": {"type": "boolean", "description": "Gunakan rebase instead of merge (default: false)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "Kelola branch git: list, create, delete, switch, create-switch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "action": {"type": "string", "enum": ["list", "create", "delete", "delete_force", "switch", "create_switch"], "description": "Tindakan: list=lihat branch, create=buat baru, delete=hapus, delete_force=hapus paksa, switch=pindah branch, create_switch=buat+pindah"},
                    "name": {"type": "string", "description": "Nama branch (wajib untuk create/delete/switch)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_clone",
            "description": "Clone repository dari URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL repository git (wajib)"},
                    "dest": {"type": "string", "description": "Direktori tujuan (opsional)"},
                    "branch": {"type": "string", "description": "Branch spesifik (opsional)"},
                    "depth": {"type": "integer", "description": "Clone depth untuk shallow clone (opsional)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_init",
            "description": "Inisialisasi repository git baru di direktori.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Direktori untuk inisialisasi (default: current)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_add",
            "description": "Stage file ke git index. Untuk commit, jalankan git_commit setelah ini.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "files": {"type": "string", "description": "File(s) to add (default: '.')"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_merge",
            "description": "Merge branch tertentu ke branch aktif.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "branch": {"type": "string", "description": "Branch yang akan di-merge (wajib)"}
                },
                "required": ["branch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_stash",
            "description": "Simpan sementara perubahan (stash) atau restore stash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "action": {"type": "string", "enum": ["save", "pop", "list", "drop", "apply"], "description": "save=simpan perubahan, pop=restore+hapus stash, list=lihat stash, drop=hapus stash, apply=restore tanpa hapus"},
                    "message": {"type": "string", "description": "Pesan untuk stash (opsional, untuk action=save)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_remote",
            "description": "Kelola remote repository: list, add, remove, set-url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "action": {"type": "string", "enum": ["list", "add", "remove", "set_url"], "description": "list=lihat remote, add=tambah remote, remove=hapus remote, set_url=ganti URL remote"},
                    "name": {"type": "string", "description": "Nama remote (default: origin)"},
                    "url": {"type": "string", "description": "URL remote (wajib untuk add/set_url)"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_linter",
            "description": "Jalankan static analysis/linter pada file atau project. Auto-detect bahasa: Python (ruff), JS/TS (eslint), PHP (phpcs), Go (golangci-lint), Rust (clippy), Ruby (rubocop), Shell (shellcheck), CSS (stylelint). Bisa auto-fix dengan fix=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file atau direktori (default: current)"},
                    "fix": {"type": "boolean", "description": "Auto-fix issues jika memungkinkan (default: false)"},
                    "lang": {"type": "string", "description": "Paksa bahasa tertentu (python, javascript, typescript, php, go, rust, ruby, shell, css, html). Default: auto-detect dari extension file."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lint_install",
            "description": "Cek dan install linter untuk bahasa tertentu. Biarkan lang kosong untuk lihat status semua linter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lang": {"type": "string", "description": "Bahasa: python, javascript, typescript, php, go, rust, ruby, shell, css, html (opsional). Kosongkan untuk lihat status."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Jalankan test suite. Auto-detect framework: pytest (Python), jest (JS/TS), phpunit (PHP), go test (Go), cargo test (Rust). Bisa paksa framework tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "framework": {"type": "string", "enum": ["auto", "pytest", "jest", "phpunit", "go_test", "cargo_test"], "description": "Framework testing (default: auto-detect)"},
                    "path": {"type": "string", "description": "Path ke file atau direktori project (default: current)"},
                    "timeout": {"type": "integer", "description": "Timeout dalam milidetik (default: 120000, max: 300000)"},
                    "cwd": {"type": "string", "description": "Working directory (opsional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_deps",
            "description": "Analisa dependency graph project: lihat import/export antar file, deteksi circular dependencies, cari orphan files. WAJIB langkah pertama sebelum refactoring besar/multi-file untuk tahu impact perubahan. Graph di-cache per proyek dan di-refresh otomatis saat file berubah; pakai refresh=true untuk scan ulang penuh.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file atau direktori (default: current). Kalo file, analisa dependency file itu. Kalo direktori, scan semua file."},
                    "refresh": {"type": "boolean", "description": "True untuk abaikan cache dan scan ulang penuh (default: false)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rename_symbol",
            "description": "Rename simbol (fungsi/class/variable) secara aman lintas file via LSP + impact check. WAJIB dipakai untuk ganti nama saat refactor besar — jangan edit_file manual per file karena rawan miss caller.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path file tempat simbol didefinisikan"},
                    "old_name": {"type": "string", "description": "Nama simbol lama"},
                    "new_name": {"type": "string", "description": "Nama simbol baru"},
                    "symbol": {"type": "string", "description": "Alias old_name (opsional)"},
                    "line": {"type": "integer", "description": "Baris posisi simbol (0-indexed, opsional, mempercepat LSP)"},
                    "character": {"type": "integer", "description": "Kolom posisi simbol (0-indexed, opsional)"}
                },
                "required": ["file_path", "new_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_subtask",
            "description": "Delegasikan sub-tugas sempit ke sub-agent dengan konteks terisolasi (mis. satu modul saat refactor multi-modul). Kembalikan ringkasan terstruktur. Sekuensial, maks 25 iterasi, tanpa delegasi bersarang. Scope dibatasi ke paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Deskripsi sub-tugas yang jelas dan sempit (wajib)"},
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Scope file/direktori (opsional, disarankan)"},
                    "max_iterations": {"type": "integer", "description": "Batas iterasi sub-agent (default: 12, maks: 25)"}
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "impact_analysis",
            "description": "Analisa dampak perubahan pada file tertentu: cari file yang mengimport file ini (langsung & transitive), estimasi jumlah file yang perlu dicek ulang. Wajib dipanggil SEBELUM refactoring besar. Isi 'symbol' untuk impact level simbol (definisi + titik referensi via indeks semantik).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path ke file yang akan diubah/dihapus/direname (wajib)"},
                    "symbol": {"type": "string", "description": "Nama fungsi/class/variable untuk impact level simbol (opsional)"},
                    "refresh": {"type": "boolean", "description": "True untuk abaikan cache graph dan scan ulang penuh (default: false)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "question",
            "description": "Bertanya ke manusia dan TUNGGU jawaban. WAJIB dipakai bila info kurang: file target ambigu, ada >1 kandidat, error tanpa stack trace/lokasi, scope destruktif belum jelas, atau dua pendekatan sama masuk akal. Jangan menebak — panggil question dulu. Hasil kembali sebagai 'Jawaban user: ...'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Pertanyaan spesifik ke user (wajib)"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Pilihan jawaban (maks 5, opsional)"},
                    "default": {"type": "string", "description": "Jawaban default bila user kosong (opsional)"}
                },
                "required": ["query"]
            }
        }
    },
]

_TOOL_MODES = {
    MODE_CODE: {
        "read_file", "write_file", "edit_file", "undo_edit", "run_command",
        "search_code", "list_dir", "glob", "db_query",
        "package_check", "web_fetch", "web_search", "web_scrape",
        "test_and_fix", "sandbox_run", "predict_command",
        "todo_create", "todo_done", "todo_show",
        "memory_store", "memory_recall", "memory_forget",
        "lsp_query", "rename_symbol", "screenshot",
        "git_status", "git_diff", "git_log", "git_commit", "git_push", "git_pull",
        "git_branch", "git_clone", "git_init", "git_add", "git_merge", "git_stash", "git_remote",
        "run_linter", "lint_install", "run_tests",
        "analyze_deps", "impact_analysis", "delegate_subtask",
        "question",
    },
    MODE_SYSADMIN: {
        "read_file", "write_file", "edit_file", "undo_edit", "run_command",
        "search_code", "list_dir", "glob", "db_query",
        "service_control", "config_edit", "package_check",
        "web_fetch", "web_search", "web_scrape",
        "test_and_fix", "sandbox_run", "predict_command",
        "todo_create", "todo_done", "todo_show",
        "memory_store", "memory_recall", "memory_forget",
        "port_scan", "dns_enum", "whois_lookup", "ssl_check",
        "cve_search", "tech_detect",
        "git_status", "git_diff", "git_log", "git_commit", "git_push", "git_pull",
        "git_branch", "git_clone", "git_init", "git_add", "git_merge", "git_stash", "git_remote",
        "run_linter", "lint_install", "run_tests",
        "question",
    },
    MODE_SECURITY: {
        "read_file", "run_command", "web_fetch", "web_search", "web_scrape",
        "port_scan", "dns_enum", "web_vuln_scan",
        "whois_lookup", "ssl_check", "dir_bruteforce",
        "cve_search", "tech_detect", "js_analyze",
        "api_discover", "source_map_check", "form_analyze",
        "apk_analyze", "binary_analyze", "predict_command",
        "todo_create", "todo_done", "todo_show",
        "memory_store", "memory_recall", "memory_forget",
        "question",
    },
}

# CATATAN (P0-8): backstop mode-gating kini ada di executor.execute()
# (_check_mode_gate): run_command untuk edit file ditolak di SECURITY,
# scan via shell ditolak di CODE/SYSADMIN, tool lintas-mode ditolak by-name.
# GENERAL tetap bebas. Redesign penuh (skema per-argumen) tetap deferred.
def get_tools_for_mode(mode):
    if mode == MODE_GENERAL:
        return TOOLS[:]
    allowed = _TOOL_MODES.get(mode, set())
    return [t for t in TOOLS if t["function"]["name"] in allowed]

# Tool inti yang SELALU dikirim di MODE_GENERAL untuk mengurangi overhead
# schema tool. Tool di luar set ini baru dikirim setelah pernah dipakai
# (tercatat di state._recent_tools).
CORE_TOOLS = {
    "read_file", "write_file", "edit_file", "undo_edit", "run_command",
    "search_code", "list_dir", "glob", "run_tests", "run_linter",
    "lint_install", "analyze_deps", "impact_analysis", "lsp_query",
    "rename_symbol", "delegate_subtask",
    "memory_store", "memory_recall", "memory_forget",
    "todo_create", "todo_done", "todo_show",
    "git_status", "git_diff", "git_log", "git_commit",
    "web_fetch", "web_search",
    "question",
}

