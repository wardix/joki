# Joki

**AI agentic CLI yang bisa ngerjain tugas di sistem kamu — dari coding, kelola server, database, sampai pentesting.**

Joki adalah asisten AI berbasis terminal yang bekerja secara otonom. Kasih perintah, Joki eksekusi sampai selesai — baca file, tulis kode, jalankan command, query database, scan security, dan banyak lagi.

> *"Joki" = yang ngerjain buat kamu.*

---

## ✨ Fitur

### 🛠️ Coding & File Management
- **Baca, tulis, edit file** — otomatis bikin dan modifikasi kode
- **Search code** — cari pattern di seluruh project (regex supported)
- **Auto-test & auto-fix** — setiap file yang ditulis langsung di-test, kalau gagal otomatis diperbaiki (sampai 5x percobaan)
- **Sandbox execution** — jalankan kode berbahaya di lingkungan terisolasi

### 🗄️ Database
- **Multi-database support** — MySQL, PostgreSQL, MongoDB, SQLite, MSSQL, Oracle, Redis
- Auto-detect jenis database dari connection string

### 🌐 Web & Networking
- **Web search** — cari info terkini via DuckDuckGo
- **Web fetch** — ambil konten dari URL (output markdown/text)
- **Port scan** — scan port terbuka pada target
- **DNS enumeration** — lookup records + subdomain brute-force
- **SSL/TLS check** — periksa certificate validity & cipher

### 🔒 Security & Pentesting
- **Web vulnerability scan** — cek security headers, SQLi, XSS, info server
- **Directory bruteforce** — temukan hidden paths pada web server
- **CVE search** — cari vulnerability berdasarkan software/service
- **WHOIS lookup** — informasi kepemilikan domain/IP
- **Tech detection** — deteksi technology stack website

### 🔍 Reverse Engineering
- **JavaScript analysis** — ekstrak API endpoints & hardcoded secrets dari JS
- **API discovery** — temukan REST/GraphQL endpoints dari HTML+JS
- **Source map check** — deteksi eksposur .map files
- **Form analysis** — ekstrak hidden fields, CSRF tokens, input types
- **APK analysis** — analisa Android APK (permissions, activities, manifest)
- **Binary analysis** — analisa executable (type, strings, metadata)

### 🖥️ System & Hardware
- **Shell execution** — persistent shell session, jalankan command apapun
- **Service management** — start/stop/restart/status systemd services
- **Config editor** — edit konfigurasi dengan auto-backup
- **UI automation** — screenshot, click, type, keypress (via xdotool)
- **USB & serial** — list USB devices, komunikasi serial (Arduino, modem)
- **Camera capture** — ambil gambar dari webcam
- **Audio/Video** — metadata, transcription (Whisper), frame extraction

### 🧠 Memori & Session
- **Long-term memory** — simpan info penting lintas sesi (password, path, config)
- **Session management** — simpan, list, dan lanjutkan percakapan
- **TODO tracking** — buat dan kelola task list per sesi

---

## 📦 Instalasi

### Prerequisites
- Python 3.8+
- pip

### Setup

```bash
git clone https://github.com/<username>/joki.git
cd joki
pip install -r requirements.txt
```

### Konfigurasi

Salin template konfigurasi dan isi API key kamu:

```bash
cp config.example.json config.json
```

Edit `config.json` dan masukkan API key:

```json
{
  "models": {
    "gemini": {
      "name": "Gemini 3 Flash Preview (OpenRouter)",
      "base_url": "https://openrouter.ai/api/v1",
      "model": "google/gemini-3-flash-preview",
      "api_keys": [
        "sk-or-v1-YOUR_KEY_HERE"
      ],
      "provider": "openai",
      "default": true
    }
  }
}
```

Kamu bisa menambahkan banyak model dan banyak API key per model — Joki akan otomatis rotate key kalau quota habis.

---

## 🚀 Penggunaan

### Mode interaktif
```bash
python joki.py
```

### Langsung kasih perintah
```bash
python joki.py "buatkan REST API sederhana pakai Flask"
```

### Masuk folder dulu, baru eksekusi
```bash
python joki.py /path/ke/project "fix semua bug di sini"
```

### Dalam sesi interaktif

| Command | Fungsi |
|---|---|
| `/model` | Lihat atau ganti model AI |
| `/model gemini` | Switch ke model tertentu |
| `/sessions` | Lihat daftar sesi tersimpan |
| `/view <nama>` | Lihat histori sesi |
| `/new` | Mulai sesi baru |
| `/reload` | Reload config.json |
| `/reset_quota` | Reset status quota API key |
| `/exit` | Keluar |

> **Tip:** Tekan `Esc + Enter` untuk input multi-line.

---

## 🤖 Model yang Didukung

Joki mendukung model AI apapun yang kompatibel dengan OpenAI API format:

| Model | Provider | Keterangan |
|---|---|---|
| Gemma 4 | Ollama (lokal/cloud) | Gratis, bisa lokal |
| Qwen3 Coder | OpenRouter | 480B, gratis tier |
| Gemini 3 Flash | OpenRouter | Cepat, default |
| DeepSeek V4 | DeepSeek API | Murah |
| *Model lain* | *OpenAI-compatible API* | Tambahkan di config.json |

### Fitur Multi-Key
- Taruh beberapa API key per model — kalau satu key habis quota, otomatis pindah ke key berikutnya
- Fallback ke model lain kalau semua key habis
- Reset quota dengan `/reset_quota`

---

## 📁 Struktur

```
joki/
├── joki.py              # Main script (semua dalam satu file)
├── config.json          # Konfigurasi model & API keys (JANGAN commit!)
├── config.example.json  # Template konfigurasi
├── requirements.txt     # Python dependencies
├── .gitignore
└── README.md
```

Data runtime disimpan di `~/.local/share/joki/`:
- `sessions/` — sesi percakapan (JSON)
- `logs/` — log readable per sesi
- `memory.json` — memori jangka panjang

---

## ⚙️ Dependencies

| Package | Fungsi |
|---|---|
| `httpx` | HTTP client untuk API calls |
| `rich` | Terminal UI (syntax highlighting, panels, spinners) |
| `prompt_toolkit` | Input interaktif dengan key bindings |
| `duckduckgo_search` | Web search |
| `pyserial` | Komunikasi serial (Arduino, modem) |
| `openai-whisper` | Audio transcription |

---

## ⚠️ Disclaimer

Joki bisa menjalankan command di sistem kamu secara langsung. Gunakan dengan bijak:

- **Jangan jalankan sebagai root** kecuali diperlukan
- **Review output** sebelum mempercayai hasil sepenuhnya
- Fitur pentesting **hanya untuk target yang kamu miliki/punya izin**
- API key di `config.json` bersifat sensitif — jangan commit ke git

---

## 📝 Lisensi

MIT License

---

## 👤 Author

**Rahmad Budiman**
