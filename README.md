# Joki

**AI Agent CLI Otonom untuk Linux**

Joki (bahasa gaul Indonesia: "orang yang mengerjakan sesuatu untukmu") adalah AI agent berbasis terminal yang bisa mengeksekusi tugas sistem secara otonom — coding, manajemen server, database, web testing, reverse engineering, security scanning, kontrol hardware, dan processing media.

Dibuat oleh **Rahmad Budiman** ([budieqnet](https://github.com/budieqnet)).

Requirement: **Python 3.10+** (ditest dengan Python 3.12).

## Fitur Utama

- **16 modul tools bawaan** (`joki/tools/`) — baca/tulis/edit file (dengan undo & backup otomatis), shell commands, query database (MySQL/Postgres/MongoDB/SQLite/MSSQL/Oracle/Redis), web search/fetch/scrape + session login, port scanning, DNS enumeration, CVE search, web vulnerability scan, analisis JS/APK/binary, USB/serial/camera, audio/video transcribe & extract, UI automation, git lengkap, linter, test runner, analisis dependency & impact, office files (DOCX/XLSX/PPTX/PDF), LSP code intelligence, todo management, dan memory persisten
- **Multi-model** — Nemotron 3 Ultra 550B (NVIDIA NIM), Nemotron 3.5 Super VL 120B, MiniMax M3.1 Flash, Ollama lokal; auto rotasi API key & fallback jika quota habis; model hakim (judge) terpisah untuk verifikasi klaim
- **Mode tugas otomatis** — klasifikasi permintaan ke Coding / Sysadmin / Security / General, dengan set tool yang disesuaikan
- **Auto-test & fix** — setelah menulis script, langsung di-run dan diperbaiki otomatis jika gagal (hingga 3-5 percobaan)
- **Context management** — project index, checkpoint tugas, dedup baca file, auto-summarization konteks, deteksi dry-run/stagnasi untuk mencegah loop
- **TUI & streaming** — interface terminal (Textual) dengan streaming respons + rendering Markdown; header atas menampilkan model aktif + estimasi pemakaian token/konteks (persen dari budget)
- **LSP pre-inject** — mendeteksi permintaan perbaikan error dan otomatis mengambil diagnostik LSP
- **Plugin system** — tambah tools kustom di `plugins/`
- **Memory** — memori jangka panjang lintas sesi
- **Keamanan** — hakim independen anti-klaim-palsu (`verifier.py`), konfirmasi untuk perintah destruktif/ireversibel, deteksi sandbox, sandbox execution, prediksi efek perintah (`predict_command`)

## Instalasi

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements_linux.txt
```

Opsional (untuk fitur tertentu, uncomment di `requirements_linux.txt`):
`pyserial`, `openai-whisper`, `pyautogui`.

## Konfigurasi

Config disimpan di `joki/config.json` (atau `~/.config/joki/config.json` pada build frozen). Auto-dibuat saat pertama kali dijalankan. File ini **tidak ikut di-version-control** (di-gitignore) karena berisi API key pribadi.

Salin template `joki/config.json.ori` menjadi `joki/config.json`, lalu isi API keys kamu:

```bash
cp joki/config.json.ori joki/config.json
```

API key juga bisa diisi lewat environment variable `JOKI_<NAMA_MODEL>_KEY` (atau `JOKI_OPENROUTER_KEY` untuk provider OpenRouter).

## Cara Pakai

```bash
python -m joki                          # mode REPL interaktif (TUI)
python -m joki "kerjakan sesuatu"        # one-shot
python -m joki /path/to/project "..."    # kerjakan di direktori tertentu
python -m joki --sessions                # daftar session tersimpan
python -m joki --resume [id|nomor]       # lanjutkan session tersimpan
python -m joki --version                 # versi
python -m joki --update                  # update dari git
```

Percakapan disimpan otomatis tiap giliran ke `sessions/` (folder joki)
sehingga bisa dilanjutkan kapan pun (termasuk checkpoint tugas, todos, dan
memori session tersebut). Saat dilanjutkan (`--resume` atau `/resume`),
riwayat percakapan (user/JOKI, maks 40 pesan terakhir) langsung ditampilkan
di layar/log.

Atau via launcher:

```bash
python launcher.py
```

### Perintah slash

| Perintah | Fungsi |
|---|---|
| `/model` | Lihat/ganti model aktif, cek status API keys |
| `/model <nama>` | Ganti model |
| `/context` | Info token konteks terpakai + persen dari budget |
| `/skills` | Kelola skills (list/on/off/reload) |
| `/baru` | Mulai session baru |
| `/sessions` | Daftar session tersimpan (untuk dilanjutkan) |
| `/resume <nomor\|id>` | Lanjutkan session tersimpan (percakapan + checkpoint tugas) |
| `/reset_quota` | Reset state quota API key yang exhausted |
| `/reload` | Reload config.json |
| `/install-lsp` | Install LSP server |
| `/keluar` | Keluar |

Batal di tengah tugas: tekan **Esc Esc** (atau Ctrl+C).

Riwayat prompt: di TUI tekan **↑** (prompt sebelumnya) / **↓** (berikutnya),
ala shell/opencode. Riwayat per sesi — ikut terisi otomatis dari prompt
sesi yang di-`/resume`/`--resume`. Tombol ↑/↓ tetap aman saat menulis
prompt multiline (kursor pindah baris dulu) dan saat menu `/` terbuka
(panah menggeser pilihan menu). Mode non-TUI sudah punya riwayat sendiri
(panah atas/bawah lewat file `history`).

## Struktur Proyek

```
launcher.py                          # entry point alternatif (python launcher.py)
requirements_linux.txt               # dependencies
tests/                               # pytest suite

joki/
  __main__.py                        # entry point (python -m joki)
  cli.py                             # loop agent, klasifikasi mode, perintah slash
  config.py, constants.py            # konfigurasi model & definisi tools
  executor.py                        # eksekusi tool
  llm.py                             # multi-model + auto rotasi key + context summarization
  verifier.py                        # hakim independen anti-klaim-palsu (read-only, fail-open; JOKI_DISABLE_VERIFIER=1 untuk matikan)
  tui.py, display.py, rich_display.py, thinking.py   # UI terminal
  sessions.py, state.py, utils.py    # session, state persistens, util
  plugins.py, project_index.py       # plugin system & indeks proyek
  tools/
    database.py, deps.py, files.py, git.py, hardware.py,
    lint.py, lsp.py, media.py, memory.py, office.py, question.py,
    reverse_eng.py, security.py, shell.py, ui.py, web.py
```

## Lisensi

MIT
