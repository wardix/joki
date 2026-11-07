# Laporan Update Joki — 17–18 Oktober 2026

Dokumen ini merangkum seluruh pekerjaan yang sudah selesai dan terverifikasi
pada sesi ini. Semua perubahan sudah diterapkan langsung ke file di
`/home/budi/joki` (bukan git repo, sehingga tidak ada commit).

**Status akhir: 455 test lolos semua (`./venv/bin/python -m pytest tests/`).**

---

## Ringkasan

| # | Pekerjaan | Hasil |
|---|---|---|
| 1 | Pengerasan verifikasi model lemah (anti klaim palsu & anti "suruh user jalan sendiri") | Selesai + 10 test regresi |
| 2 | Fix bug sinkronisasi session + fitur simpan/resume session | Selesai + 13 test baru |
| 3 | Data joki dipindah ke folder project (`/home/budi/joki`), self-contained | Selesai + terverifikasi |
| 4 | Uji ulang poolside + patch 3 celah harness (cwd guard, glob guard, direktori kerja) | Selesai + 8 test regresi |
| 5 | Fix bug TUI keluar ke terminal saat `/sessions` (tag style `[budi]`) | Selesai + 6 test regresi |
| 6 | Riwayat percakapan langsung tampil setelah `/resume` & `--resume` | Selesai + 4 test regresi |
| 7 | Header atas TUI menampilkan persentase pemakaian token & konteks | Selesai + 4 test regresi |
| 8 | Cek kuota provider (Ollama 429) + perbaiki dampak overload NVIDIA NIM (fallback & judge) | Selesai + 2 test invariansi |
| 9 | Tambah model `nemotron-3.5-super-vl-120b-a12b-nvidia` ke config | Selesai + terverifikasi (curl & jalur kode joki) |
| 10 | Riwayat prompt di TUI: panah atas/bawah (ala opencode) | Selesai + 5 test regresi |

---

## 1. Pengerasan Verifikasi (Model Lemah)

### Masalah
Model seperti `poolside-laguna-s-2.1` sering:
- mengklaim "sudah diperbaiki" padahal error yang sama masih muncul;
- menuliskan perintah shell sebagai teks ("jalankan `npm run build`") tanpa
  memanggil tool `run_command` sendiri;
- lolos gate verifikasi hanya dengan bukti `grep`/lint (bukan bukti eksekusi).

### Perubahan utama
**`joki/cli.py`**
- Helper baru: `_effective_cmd`, `_is_lint_only_cmd`, `_wants_syntax_fix`,
  `_VIEW_ONLY_BINS`, `_HTTP_PROOF_BINS`, `_LOCAL_HTTP_RE`.
- `_VERIFY_FAIL_RE` diperluas: `ErrorException`, `Whoops`, `Internal Server Error`,
  `PHP Fatal/Parse`, `Uncaught`, `SQLSTATE`, HTTP 5xx, `500` telanjang, `ERR_*` Node.
- `_is_meaningful_verify` ditulis ulang: menolak lint-only & file-viewer
  (`cat`/`less`/dsb.), menerima `curl`/`wget` localhost sebagai bukti reproduksi,
  pengecualian konteks syntax-error, dan mengecek juga tool `test_and_fix`.
- Detektor "menyuruh pengguna menjalankan perintah": `_ASK_RUN_RE` + `_SHELLISH_RE`
  dengan nudge `[AKSI SENDIRI]` (maks 2x, tidak aktif untuk small talk).
- Budget nudge zero-tool kini sadar tugas: `_TASK_FIRST_TEXT_MAX_NUDGES = 2`.
- Auto-test tidak lagi menjalankan `php <file>` untuk `.blade.php` dan file PHP
  framework (namespace + class) yang selalu exit 0 (sumber klaim "BERHASIL" palsu).

**`joki/verifier.py`**
- `_NON_EVIDENCE_BINS` diperluas dengan view bins; helper lint; `_has_any_evidence(allow_lint=)`.
- `deterministic_check(..., original_request)` ditulis ulang: menolak bukti lint/viewer,
  `curl` localhost boleh menutupi file yang diubah, repro-lock sadar lint.
- Prompt hakim (judge) diperketat (aturan 3 & 4: tolak bukti read-only dan
  syntax-only untuk error runtime). Model judge dibaca dari `config.json`.

**`joki/config.json`**
- `"judge_model": "nemotron-3-ultra-550b-a55b-nvidia"` (hakim independen, bukan self-judge).
- Dua entri poolside: `"temperature": 0.3` dan fallback `gemma4` (mati/429 semua key)
  diganti `nemotron-3-ultra-550b-a55b-nvidia`.
- Prioritas hakim: env `JOKI_JUDGE_MODEL` > `judge_model` di config > model worker.

### Bukti pengujian
- `tests/test_hardening.py` (baru, 10 test regresi).
- Validasi end-to-end: klaim lint-only → **FAIL**; klaim `grep` → **FAIL**;
  `curl localhost` sebagai repro → **PASS** (hakim: nemotron); konteks syntax error →
  lint diizinkan.

---

## 2. Fix Bug Session + Fitur Simpan/Resume

### Akar masalah
`cli.py` memakai `from joki.state import *`, sehingga `_CURRENT_SESSION = ...`
hanya mengubah salinan lokal modul `cli` — `joki.state._CURRENT_SESSION` tetap
`None`. Akibatnya `todos/` dan `memories/` selalu menulis ke `default.json`
(tidak pernah per-session).

### Perbaikan
- **`joki/state.py`**: `_set_current_session()` / `_get_current_session()` sebagai
  satu-satunya sumber kebenaran (ditambahkan ke `__all__`).
- **`joki/tools/memory.py`**: `_memory_path()` & `_todo_path()` membaca session
  secara dinamis dari state.
- **`joki/cli.py`**: startup, `/baru`, dan `/resume` semuanya lewat `_set_current_session()`.

### Fitur baru: simpan & resume session
**`joki/sessions.py` (baru)**
- Simpan/muat/daftar session di `<data_dir>/sessions/<id>.json`.
- Penulisan atomik (tmp + `os.replace`), prune otomatis (maks 100 file),
  potong riwayat (maks 400 pesan), konten > 20.000 karakter dipotong,
  seluruh fungsi fail-open.
- Metadata: `session`, `created`, `updated`, `cwd`, `model`, `message_count`, `first_user`.

**Autosave**
- Percakapan disimpan otomatis setiap `agent_loop` selesai (REPL, one-shot, TUI).
- Guard `_SESSION_AUTOSAVE` (aktif hanya saat aplikasi berjalan via `main()`)
  agar pemanggilan ad-hoc/test tidak menulis file session.

**Cara resume**
- CLI: `joki --sessions` (daftar) dan `joki --resume [id|nomor]`
  (alias `-r`, `--continue`, `-c`); helper `_parse_session_flags()`.
- Slash: `/sessions` dan `/resume <nomor|id>` (alias `/sesi`, `/lanjut`) —
  jalan di REPL maupun TUI.
- TUI: entri menu `/sessions` & `/resume` + sub-menu daftar session;
  autocomplete session di prompt.
- Saat resume: system prompt dibangun ulang, riwayat dimuat, checkpoint tugas
  (`checkpoints/<session>.json`) dikembalikan ke `_task_context` (file yang pernah
  diubah, rencana, dsb.), approval & status key exhausted di-reset, dan
  (untuk CLI) working directory lama dipakai lagi bila ada.

### Perbaikan isolasi test
- Ditemukan test lama yang membocorkan `_CURRENT_SESSION` global
  (`tests/test_tui.py::test_handle_command_baru`) dan memicu autosave menulis
  file session nyata ke data dir pengguna. Sudah dibersihkan:
  - guard `_SESSION_AUTOSAVE` di kode,
  - cleanup `try/finally` di test TUI,
  - file nyasar dihapus, suite dijalankan ulang — data asli tetap bersih.

---

## 3. Data Joki Self-Contained di `/home/budi/joki`

- **`joki/config.py`**: `_get_data_dir()` kini mengembalikan folder project
  (`/home/budi/joki`) saat dijalankan dari source. Build standalone (frozen)
  tetap memakai `~/.local/share/joki` karena folder aplikasi read-only.
- **Semua data dipindah fisik** dari `~/.local/share/joki/` ke `/home/budi/joki/`:

| Item | Jumlah |
|---|---|
| `checkpoints/` | 18 file |
| `dep_graph/` | 118 file |
| `project_index/` | 109 file |
| `memories/`, `todos/`, `skills/` | masing-masing 1 file |
| `voice/` | 3 file |
| `plugins/`, `sessions/` | kosong (belum ada isi) |
| `agent_exit.log` | ± 50 KB |

- Folder lama `~/.local/share/joki/` sudah dihapus (folder `.local/share` lain tidak disentuh).
- Dokumentasi diperbarui: `README.md` (path plugin & sessions), docstring
  `config.py`, `sessions.py`, komentar `skills.py`, pesan/docstring `cli.py`.

### Verifikasi
- `_get_data_dir()` → `/home/budi/joki` ✅
- Skill terbaca dari lokasi baru: 5 skill (`debug-error`, `laravel-proyek`,
  `node-proyek`, `refactor-aman`, `verifikasi-eksekusi`) ✅
- Smoke test sungguhan: save session → `joki --sessions` menampilkan →
  `joki --resume 1` berhasil melanjutkan — semua dari `/home/budi/joki/sessions/` ✅
  (file tes sudah dibersihkan kembali)

---

## 4. Uji Ulang Poolside + Patch Celah Harness (lanjutan)

### Hasil uji ulang `poolside-laguna-s-2.1` (temperature 0.3, config tidak diubah)

3 tugas nyata di sandbox + 1 ulangan; hasil akhir diverifikasi otonom oleh skrip
(nilai sebenarnya), bukan oleh klaim model:

| Tugas | Hasil | Catatan |
|---|---|---|
| Perbaiki `ZeroDivisionError` (a.py) | ❌ GAGAL + klaim palsu | Menebak `/tmp/a.py`, mengedit file yang salah di `/tmp/pytest-of-budi/...`, lapor "✅ Selesai" — `a.py` asli masih error (24 iterasi) |
| Perbaiki test gagal (calc.py) | ✅ LULUS | Lewat detour `/tmp` dulu, lalu fix benar, 3 test lulus (9 iterasi) |
| Buat `hitung.py` dari `data.txt` | ❌ GAGAL | `glob("**/data.txt", path="/")` menjelajah seluruh filesystem → hang 300s, script tidak pernah dibuat |
| Ulangan tugas 1 (fallback mendarat di poolside) | ❌ GAGAL | 33× `list_dir` folder pytest yang sama → "dry run final stop (36x)" |

Pembanding: nemotron (via rantai fallback) menyelesaikan tugas `hitung.py` dalam
7 iterasi — salah tebak `/home/user`, langsung koreksi dengan `pwd && ls -la`.

**Kesimpulan:** temperature 0.3 tidak menyembuhkan kecenderungan ngelantur
poolside. Uji ini sekaligus menemukan 3 celah harness yang memperbesar masalah.

### 3 celah harness yang dipatch

1. **Direktori kerja kini disebut ke model**
   - `joki/cli.py` (`_build_system_prompt`): blok `DIREKTORI KERJA: <path absolut>`
     + instruksi jangan menebak lokasi (cek `list_dir('.')`/`pwd`).
   - `joki/project_index.py` (`_build_index_text`): baris `Direktori kerja: <root>`
     di awal `[INDEX PROYEK]`.
2. **`cd` keluar workspace tidak lagi menyeret cwd proses joki**
   - `joki/tools/shell.py`: `_get_shell_boundary()` (lazy, aman untuk `--resume`)
     + `_sync_cwd_allowed()`; saat shell `cd` ke luar workspace: cwd joki tetap,
     shell dikembalikan ke root workspace, model menerima catatan `[cwd dijaga] ...`.
   - Sebelumnya: sekali model menjalankan `cd /tmp && ...`, cwd seluruh sesi
     (indeks proyek, auto-test, path relatif) ikut pindah permanen.
3. **`glob` dari root dibatasi**
   - `joki/tools/files.py` (`handle_glob`): `path='/'` / pattern absolut dari root
     ditolak; pencarian lain diberi batas waktu 8 detik (`_GLOB_TIMEOUT_S`).

### Bukti
- E2E: `glob("**/data.txt", "/")` → ditolak; `run_command "cd /tmp && pwd"` →
  `os.getcwd()` tetap workspace (sebelumnya menjadi `/tmp`); system prompt &
  `[INDEX PROYEK]` memuat path absolut direktori kerja.
- Test regresi baru: `tests/test_harness_gaps.py` (8 test).
- **Full suite: 434 passed** (426 + 8), tanpa regresi.

---

## 5. Fix Bug: TUI Keluar ke Terminal Setelah `/sessions`

### Laporan
User: *"saya ketik `/session` di prompt joki kemudian saya enter malah keluar
ke terminal"*.

### Akar masalah (tereproduksi, bukan dugaan)
1. Di prompt TUI, `/session` (tanpa 's') memunculkan dropdown `/sessions`;
   Enter pertama melengkapi ke `/sessions`, Enter kedua mengeksekusinya.
2. Baris daftar memakai format `[{cwd}]` dengan basename cwd — untuk cwd
   `/home/budi` teksnya jadi `[budi]`.
3. Rich membaca `[budi]` sebagai **tag style**. Validasi style **ditunda**:
   `Text.from_markup('[budi] x')` sukses (span.style = `'budi'`), error
   `MissingStyle` baru muncul saat render Textual di layout → exception
   menjatuhkan app dan user kembali ke terminal (exit tanpa pesan).
4. Reproduksi: pilot Textual (`app.is_running` jadi False setelah Enter #2)
   dan PTY nyata (joki exit code 0 setelah Enter #2) — keduanya sebelum fix.

### Perbaikan
1. **`joki/cli.py` (`/sessions`)** — tag cwd di-escape UTUH:
   `escape(f"[{basename}]")` sehingga `[budi]` tampil literal, bukan style.
2. **`joki/tui.py` (pertahanan berlapis)**:
   - helper `_markup_styles_valid()` + `_safe_markup_text()`: span style
     divalidasi lebih awal dengan `Style.parse`; kalau ada yang tak dikenal,
     teks di-render sebagai teks biasa (tidak menutup app);
   - `_make_log_widgets()` dan `_set_status()` memakai helper tersebut;
   - label dropdown `_update_cmd_menu()` (label `/resume` memuat pesan pertama
     user — rawan `[kata]`) dan opsi/default `_QuestionDialog` di-escape.

### Bukti
- Setelah fix: pilot test → app tetap `running` setelah `/session` + 2×Enter,
  log menampilkan ` 1. session_...  [budi]  4 pesan  pwd` secara literal;
  PTY nyata → app tetap hidup setelah Enter #2.
- Test regresi baru di `tests/test_tui.py` (6 test): fallback
  `_safe_markup_text`, `_make_log_widgets`, escape payload `/sessions`,
  skenario persis user (`/session` + Enter ×2), status bar, dan label `/resume`.
- **Full suite: 440 passed** (434 + 6), tanpa regresi.

### Catatan
- Proses joki yang **sedang berjalan** masih memuat kode lama — restart joki
  agar fix ini aktif.

---

## 6. Riwayat Percakapan Langsung Tampil Setelah `/resume`

### Permintaan
User: *"saya mau joki langsung menampilkan riwayat percakapan setelah
`/resume no_session` di enter"*.

### Perubahan
- Helper baru `_render_session_history()` di `joki/cli.py`:
  - menampilkan pesan **user & JOKI** sebagai card (tool call/result dilewati
    agar log tidak banjir);
  - batas tampilan: 40 pesan terakhir, 4000 karakter per pesan (pesan/kelebihan
    dipotong dengan penanda);
  - header redup `── Riwayat percakapan (N pesan terakhir, M disembunyikan) —
    <session_id> ──`.
- Dipanggil di:
  1. handler `/resume <nomor|id>` (TUI & REPL) — inti permintaan user;
  2. `JokiApp.on_mount()` (TUI) saat dibuka dengan `joki --resume` — layar log
     tidak kosong lagi;
  3. mode scrollback non-TTY di `main()`.
- Lock vs autosave: murni tampilan — pesan tetap dimuat ke konteks agent seperti
  sebelumnya (bukti test: isi `messages` ikut terisi).

### Bukti
- Pilot TUI: `/resume 1` + Enter → `app.is_running=True`, log berisi
  "Riwayat percakapan", pesan lama user & jawaban JOKI; header dirender
  **sekali** (bukan dobel).
- PTY nyata: setelah `/resume 2` + Enter tampil
  `Session dilanjutkan: ... (4 pesan)` lalu
  `── Riwayat percakapan (2 pesan terakhir) — session_... ──`, `│ USER │ pwd`,
  `│ JOKI │ Direktori kerja saat ini: /home/budi`.
- Test regresi baru di `tests/test_tui.py` (4 test): isi riwayat via queue,
  pemotongan pada riwayat panjang, skenario TUI `/resume 1`, dan startup
  `--resume` di TUI.
- **Full suite: 444 passed** (440 + 4), tanpa regresi.

---

## 7. Header Atas TUI: Persentase Pemakaian Token & Konteks

### Permintaan
User: *"saya mau di header joki bagian atas ditampilkan informasi persentase
penggunaan token dan context"*.

### Perubahan
- Header (`#topbar`) kini dua kolom:
  - kiri: `JOKI  •  <nama model>` (seperti sebelumnya);
  - kanan: `Konteks ~X% (A/B token)` — estimasi pemakaian dari
    `llm.context_usage()` (pesan + schema tools) terhadap budget efektif model.
- Penanda zona: `⚠` saat mencapai ambang summarize otomatis (≥65%,
  `SUMMARY_TRIGGER_RATIO`) dan `⛔` saat kritis (≥90%).
- Helper baru `_context_header_text()` di `joki/tui.py` — **fail-open**
  (return `""` bila hitungan gagal, header tidak boleh menjatuhkan TUI).
- Header disegarkan: saat mount, awal/akhir tugas (`_set_busy`), setelah
  command yang mengubah `messages`/model (`/baru`, `/resume`, `/model`), dan
  berkala maks 1×/detik selama tugas berjalan (throttle `_topbar_refresh_ts`
  di `_drain_queue` — hitungan token itu iteratif, jangan tiap 50 ms).

### Bukti
- Pilot TUI (110 kolom): `title = "JOKI  •  Nemotron..."`,
  `ctx = "Konteks ~2.2% (5.342/245.760 token)"`, posisi kanan
  (region ctx `x=64 w=35`, ujung menyentuh lebar layar).
- PTY nyata: baris header tampil utuh —
  `JOKI • Nemotron 3 Ultra 550B (NVIDIA NIM)   Konteks ~3.4% (8.455/245.760 token)`.
- Zona terverifikasi: budget dikecilkan → `⚠ Konteks ~71.3% ...` (warn) dan
  `⛔ Konteks ~97.3% ...` (kritis); budget 0 / error → string kosong.
- Selama tugas berjalan, header ikut ter-update begitu pesan tool bertambah.
- Test regresi baru (4): format & zona helper, tampilan topbar + refresh saat
  messages berubah, update setelah `/baru`, dan update saat tugas masih
  berjalan (agent ditahan pakai `threading.Event`).
- **Full suite: 448 passed** (444 + 4), tanpa regresi.

---

## 8. Cek Kuota Provider & Perbaikan Dampak Overload NVIDIA NIM

### A. Kunci Ollama (diminta user — semua kena 429?)
Ya. 7 kunci cloud `ollama.com` di `config.json` (1 nemotron-ollama + 6 gemma4):

- `POST /v1/chat/completions` → **429 semua**; `GET /v1/models` → **200 semua**
  (kunci valid, bukan salah auth — murni kuota generasi).
- 4 akun kena kuota bulanan: jokieqnet, budieqnet, eqposstudio,
  fadhlubudiman2010; 3 kunci lain kena cap plan gratis (5-jam/mingguan,
  bisa pulih sendiri).
- `ollama-local` (localhost:11434): server **tidak berjalan** (connection refused).

### B. NVIDIA NIM "Network/server error, retry in 2.4s"
- Model default `nvidia/nemotron-3-ultra-550b-a55b` **overload intermiten**:
  11 dari 13 percobaan → `503 Service temporarily overloaded` (~15% sukses).
- Karena joki streaming: HTTP 200 tapi event SSE pertama berisi error code 503 —
  ditangkap `_raise_on_sse_error()` (`llm.py:480`) → retry `2^retry + acak`
  (2.4s dst) → pesan yang dilihat user adalah perilaku benar, bukan bug.
- Bukan key/bentuk request: tanpa tools/`chat_template_kwargs` tetap 503;
  kunci yang sama untuk `super-120b` 3/3 → 200; via OpenRouter pun upstream
  Nvidia-nya 503.
- `nemotron-3.5-lightning-30b-a3b`: hang/timeout (0/3) — bukan kandidat fallback.

### C. Perubahan
- Entry baru `nemotron-3-super-120b-a12b-nvidia` (NIM, kunci sama; terverifikasi
  3/3 200 + mendukung `chat_template_kwargs` streaming).
- Semua `fallback: "gemma4"` (5 entri) dialihkan ke entry baru
  (`nemotron-3-super-120b-a12b-nvidia`) — gemma4 sudah mati 429.
- `judge_model` dialihkan `nemotron-3-ultra-550b-a55b-nvidia` →
  `nemotron-3-super-120b-a12b-nvidia` (hakim verifikasi tidak lagi flaky 503;
  kalau entry judge hilang, verifier diam-diam jadi self-judge).
- Test invariansi baru di `tests/test_config.py`:
  - `test_fallback_targets_valid` — setiap `fallback` wajib menunjuk model yang
    ada & bukan dirinya sendiri;
  - `test_judge_model_valid` — `judge_model` (bila diset) wajib ada di `models`.

### Bukti
- Simulasi end-to-end (ultra disimulasikan gagal konek):
  `Network/server error, retry in 1.8s...` → `⚠ Model fallback: Nemotron 3
  Super 120B (NVIDIA NIM)` → jawaban `halo` dari super-120b.
- Judge nyata dengan model baru: request lemah → hakim balas
  `{"verdict": "FAIL", "reason": "Bukti hanya menjalankan aplikasi ... tanpa
  menguji endpoint login ..."}` — parse verdict OK.
- **Full suite: 450 passed** (448 + 2), tanpa regresi.

---

## 9. Model Baru: Nemotron 3.5 Super VL 120B (NVIDIA NIM)

Permintaan user: tambahkan
`https://build.nvidia.com/nvidia/nemotron-3.5-super-vl-120b-a12b`
ke `joki/config.json`.

### Hasil uji API (curl, kunci NIM yang sama)
- ID `nvidia/nemotron-3.5-super-vl-120b-a12b` valid di `/v1/models`;
  non-stream HTTP 200.
- Streaming + `chat_template_kwargs` (`enable_thinking`, `medium_effort`,
  `force_nonempty_content`): 22 chunk SSE, `reasoning_content` terpisah,
  `content` akhir `"2"`. Tanpa `force_nonempty_content` konten bisa `null`
  (pengujian non-stream), jadi kwargs ini ikut dipasang di config.
- Tool calling (non-stream & streaming): `get_weather({"city":"Bandung"})`,
  `finish_reason: tool_calls` — dukung agent loop joki.
- Rekomendasi sampling halaman NVIDIA: text temp 1.0 / top_p 0.95; vision
  temp 0.6 / top_p 0.95. Config memakai 0.5 / 0.9 agar konsisten dengan kedua
  entri Nemotron NIM yang ada (mode coding tetap di-cap 0.3 oleh `llm.py`).

### Perubahan
- Entry baru `nemotron-3.5-super-vl-120b-a12b-nvidia`: `max_tokens` 16384,
  `context_window` 1.000.000 / `effective_context` 262.144, `max_iterations` 40,
  fallback `nemotron-3-super-120b-a12b-nvidia`, `default: false`.
- VL = vision-language, tapi joki belum mengirim gambar ke LLM — yang dipakai
  kemampuan teks + tool-calling-nya.

### Bukti jalur kode joki (`llm.call_llm`, `/tmp/opencode/verify_new_model.py`)
```
[1] model akhir : nvidia/nemotron-3.5-super-vl-120b-a12b
[1] balasan     : 'halo'
[2] tool calls  : [('get_weather', '{"city":"Surabaya"}')]
[2] model akhir : nvidia/nemotron-3.5-super-vl-120b-a12b
```
Tidak jatuh ke fallback; panel berpikir menerima `reasoning_content`.
`tests/test_config.py` 9 passed; total model di config kini 11.

---

## 10. Riwayat Prompt di TUI: Panah Atas/Bawah (ala Opencode)

Permintaan user: tekan panah atas di prompt → tampil prompt/perintah
sebelumnya, panah bawah → maju lagi (seperti di opencode).

### Perubahan (`joki/tui.py`, kelas `JokiInput`)
- Riwayat prompt per app: dicatat saat submit; dedup berturut-turut, maksimum
  200 entri.
- Saat mount, riwayat diisi dari pesan `user` sesi yang sedang dibuka — jadi
  setelah `/resume`/`--resume`, panah atas langsung menampilkan prompt lama.
  Pesan internal/nudge sistem (selalu diawali `[`) tidak ikut masuk.
- Panah atas: item lebih lama (tahan di item paling lama). Panah bawah: item
  lebih baru; melewati ujung = **pulihkan draft** yang sedang diketik.
- Aman untuk input multiline: panah atas hanya "diambil alih" bila kursor di
  baris pertama (panah bawah: baris terakhir), kecuali buffer belum diubah
  sejak item riwayat dimuat — navigasi beruntun tetap bisa melewati prompt
  multiline. Kursor ditaruh di akhir teks setelah memuat item.
- Menu slash-command tetap prioritas: saat menu terbuka, panah menggeser
  pilihan menu (bukan riwayat).
- Mode non-TUI tidak berubah — sudah ada riwayat via prompt_toolkit
  `FileHistory` (`history`).

### Bukti
- 5 test regresi baru di `tests/test_tui.py`: navigasi naik/turun + tahan di
  ujung & posisi kursor, pulihkan draft, batas multiline, seed dari pesan sesi
  (nudge `[CHECK-IN]` dilewati), menu prioritas di atas riwayat.
- Smoke PTY nyata (pyte, `--resume session_20261018_080449`; skrip
  `/tmp/opencode/pty_history.py`):
  - prompt awal: placeholder (riwayat belum dipakai)
  - ↑ #1: `tampilkan semua file dan folder`
  - ↑ #2: `pwd`
  - ↓ #1: `tampilkan semua file dan folder`
  - ↓ #2: draft kosong (placeholder kembali)
- **Full suite: 455 passed** (450 + 5), tanpa regresi.

---

## Daftar File yang Berubah

| File | Jenis | Ringkasan |
|---|---|---|
| `joki/cli.py` | ubah | Gate verifikasi, nudge anti "suruh user", autosave session, `/sessions` `/resume`, flag `--sessions`/`--resume`, resume context, `DIREKTORI KERJA` di system prompt; escape tag `[cwd]` di daftar `/sessions`; `_render_session_history()` (riwayat setelah resume) |
| `joki/verifier.py` | ubah | Deterministic check + prompt hakim diperketat, model judge dari config |
| `joki/config.json` | ubah | `judge_model` (kini `nemotron-3-super-120b-a12b-nvidia`), temperature poolside 0.3, fallback baru; entry baru `nemotron-3-super-120b-a12b-nvidia` + semua fallback gemma4 dialihkan ke sana; entry baru `nemotron-3.5-super-vl-120b-a12b-nvidia` |
| `joki/state.py` | ubah | `_set_current_session()` / `_get_current_session()` |
| `joki/tools/memory.py` | ubah | Path todos/memories baca session dinamis |
| `joki/sessions.py` | **baru** | Penyimpanan & pemuatan session |
| `joki/config.py` | ubah | `_get_data_dir()` → folder project |
| `joki/tui.py` | ubah | Menu slash `/sessions` & `/resume` + sub-opsi; `_safe_markup_text`/`_markup_styles_valid` (anti-crash style tak dikenal), escape label dropdown & dialog pertanyaan; riwayat percakapan dirender di `on_mount` saat `--resume`; header 2 kolom + `_context_header_text()` (persentase token/konteks, refresh berkala saat tugas); riwayat prompt panah ↑/↓ di `JokiInput` (batas multiline, pulihkan draft, seed dari pesan sesi) |
| `joki/skills.py` | ubah | Komentar sumber skill |
| `tests/test_hardening.py` | **baru** | 10 test regresi verifikasi |
| `tests/test_config.py` | ubah | +2 test invariansi: fallback & judge_model harus menunjuk model yang ada di config |
| `tests/test_session.py` | **baru** | 13 test session (sync, save/load, resolve, prune, autosave, resume) |
| `tests/test_memory.py` | ubah | Target patch pindah ke `joki.state._CURRENT_SESSION` |
| `tests/test_tui.py` | ubah | Cleanup session global setelah `/baru`; +6 test regresi anti-crash tag style `[budi]` (`/session` + Enter ×2); +4 test riwayat setelah `/resume`; +4 test header token/konteks (`_context_header_text`, topbar, `/baru`, refresh saat tugas berjalan); +5 test riwayat prompt panah ↑/↓ |
| `README.md` | ubah | Dokumentasi flag & slash command baru; header TUI menampilkan token/konteks |
| `joki/tools/files.py` | ubah | Guard `glob`: tolak root `/`, batas waktu 8 detik |
| `joki/tools/shell.py` | ubah | Boundary workspace: `cd` keluar workspace tidak mengubah cwd proses |
| `joki/project_index.py` | ubah | Baris `Direktori kerja:` di `[INDEX PROYEK]` |
| `tests/test_harness_gaps.py` | **baru** | 8 test regresi celah harness |

---

## Cara Pakai Fitur Baru

```bash
joki --sessions                  # daftar session tersimpan
joki --resume                    # lanjutkan session terbaru
joki --resume 3                  # lanjutkan nomor 3 dari daftar
joki --resume session_20261017_143000
```

Di dalam joki (REPL / TUI):

```text
/sessions                        # daftar session (nomor untuk resume)
/resume 1                        # lanjutkan session nomor 1 (riwayat langsung tampil)
/resume session_20261017_143000  # atau pakai id-nya
```

Riwayat yang ditampilkan: maks 40 pesan terakhir (user & JOKI), 4000 karakter
per pesan; bila dipotong ada catatan redup di header/pesan.

Riwayat prompt di TUI: panah **↑** / **↓** menelusuri prompt yang sudah
dikirim (per sesi; ikut terisi saat resume). Panah bawah dari prompt terbaru
mengembalikan draft yang sedang diketik.

Ganti model hakim verifikasi (opsional):

```bash
JOKI_JUDGE_MODEL=nemotron-3-ultra-550b-a55b-nvidia joki
# atau set "judge_model" di joki/config.json
```

---

## Catatan Penting

1. **Todos & memori kini per-session** (sesuai perbaikan bug). Data lama di
   `memories/default.json` dan `todos/default.json` tidak otomatis muncul di
   session baru — hanya di session yang id-nya sama. (Bisa dibuatkan skrip
   migrasi bila diinginkan.)
2. **Semua data sekarang di `/home/budi/joki`** (`agent_exit.log`, `sessions/`,
   `todos/`, `memories/`, `checkpoints/`, `project_index/`, `dep_graph/`,
   `skills/`, `voice/`, `plugins/`). Kalau folder ini nanti dijadikan git repo,
   tambahkan folder-folder tersebut + `agent_exit.log` ke `.gitignore`.
3. Autosave session hanya aktif saat aplikasi berjalan normal (via `main()`),
   bukan saat `agent_loop` dipanggil ad-hoc (mis. test/library).
4. `poolside-laguna-s-2.1` kini `temperature: 0.3` dengan fallback
   `nemotron-3-ultra-550b-a55b-nvidia`; fallback lama `gemma4` dinonaktifkan
   karena kena 429 di semua key.
5. Perilaku baru pasca-patch celah harness: cwd sesi **tidak** ikut pindah saat
   perintah melakukan `cd` ke luar workspace (shell dikembalikan + model diberi
   catatan `[cwd dijaga]`), `glob` dari root `/` ditolak, dan direktori kerja
   absolut kini disebut di system prompt + `[INDEX PROYEK]`.
