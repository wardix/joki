# Joki

**AI Agent CLI Otonom untuk Linux**

Joki (bahasa gaul Indonesia: "orang yang mengerjakan sesuatu untukmu") adalah AI agent berbasis terminal yang bisa mengeksekusi tugas sistem secara otonom — coding, manajemen server, database, web testing, reverse engineering, security scanning, kontrol hardware, dan processing media.

## Fitur Utama

- **44+ tools bawaan** — baca/tulis/edit file, shell commands, query database (MySQL/Postgres/MongoDB/SQLite/MSSQL/Oracle/Redis), web search/fetch, port scanning, DNS enumeration, CVE search, analisis JS/APK/binary, USB/serial/camera, audio/video, UI automation, todo management, memory persisten
- **Multi-model** — Gemini, Gemma 4, Qwen3 via OpenRouter + Ollama lokal; auto rotasi key & fallback jika quota habis
- **Auto-test & fix** — setelah nulis script, langsung di-run dan diperbaiki otomatis jika gagal (hingga 5 percobaan)
- **Eksekusi paralel** — read-only tools jalan bersamaan via ThreadPoolExecutor
- **Streaming respons** — token-by-token dengan rendering Markdown
- **Plugin system** — tambah tools kustom di `~/.local/share/joki/plugins/`
- **Session & memory** — riwayat percakapan persisten + memori jangka panjang lintas sesi
- **Keamanan** — konfirmasi untuk perintah berbahaya, elevasi sudo, sandbox execution

## Cara Pakai

```bash
python -m joki              # mode REPL interaktif
python -m joki "kerjakan sesuatu"   # one-shot
```

## Lisensi

MIT
