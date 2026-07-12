from joki.state import *
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
                    "limit": {"type": "integer", "description": "Jumlah baris maksimal (default: semua)"}
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
            "description": "Search and replace text in an existing file. Supports fuzzy matching — whitespace differences (spasi, tab, indentasi) otomatis dinormalisasi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Text to replace. Tidak perlu whitespace-eksak — sistem akan fuzzy match."},
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
            "description": "Run any shell command. Gunakan untuk: psql, mongosh, apachectl, nginx, docker, git, apt, systemctl, dsb. PENTING: untuk perintah yang butuh admin/root, WAJIB tambahkan prefix 'sudo ' (Linux/macOS) atau 'runas ' (Windows). Contoh: 'sudo apt update', 'sudo systemctl restart nginx', 'runas net start mysql'. Parameter timeout (ms), cwd, dan isInteractive tersedia untuk kontrol lebih lanjut.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command"},
                    "timeout": {"type": "integer", "description": "Timeout dalam milidetik (default: 120000, max: 600000 / 10 menit)"},
                    "cwd": {"type": "string", "description": "Working directory (default: direktori aktif saat ini)"},
                    "isInteractive": {"type": "boolean", "description": "Jika true, jalankan dalam mode interaktif (default: false)"}
                },
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
            "name": "lsp_query",
            "description": "Dapatkan informasi kode secara semantik via LSP: cari definisi fungsi, referensi, tipe data, atau lihat struktur file. Jauh lebih akurat daripada search_code untuk pertanyaan semantik.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["goToDefinition", "findReferences", "hover", "documentSymbol", "workspaceSymbol"],
                        "description": "Operasi LSP: goToDefinition=cari definisi, findReferences=cari semua pemanggil, hover=info tipe data, documentSymbol=struktur file, workspaceSymbol=cari simbol di seluruh project"
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
