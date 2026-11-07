---
name: SOP Refactor Aman Multi-File
always_on: false
trigger: [refactor, refaktor, rename, ganti nama, pindah modul, migrasi, multi-file, lintas file]
---

Untuk refactor yang menyentuh lebih dari satu file, ikuti urutan ini —
jangan lompat, jangan mengandalkan ingatan tentang siapa memakai apa:

1. `analyze_deps(path)` + `impact_analysis(path)` dulu untuk petakan file
   terpengaruh. Kalau rename simbol, isi juga `symbol` di impact_analysis.
2. Rename via `rename_symbol` (LSP + impact check), BUKAN edit manual per file.
3. `read_file` semua target SEBELUM `edit_file`; `old_text` unik min 3-5 baris.
4. Satu file per satu giliran; verifikasi tiap file (linter/test otomatis
   jalan — baca hasilnya, jangan abaikan).
5. Setelah semua file: `search_code` nama lama untuk pastikan nol sisa,
   lalu `run_tests` + `git_diff` sebagai review akhir.
6. DILARANG `run_command` untuk edit file (sed/awk/echo redirect).
