---
name: Debug Error Sistematis
always_on: false
trigger: [error, traceback, gagal, crash, exception, broken, tidak jalan, kenapa, bug]
---

Saat user melaporkan error/kegagalan, jangan menebak perbaikan:

1. Baca pesan error LENGKAP dulu (stack trace, exit code, file:line).
   Kalau asing, `web_search` solusi yang sudah diketahui sebelum menyentuh kode.
2. Reproduksi dulu bila mungkin (jalankan perintah/file yang gagal),
   catat kondisi reproduksinya.
3. Cari akar masalah dengan membaca kode di titik error (read_file),
   bukan tebak-tebak. Satu hipotesis → satu verifikasi.
4. Perbaiki minimal, lalu verifikasi dengan menjalankan ulang skenario
   reproduksi — bukan cuma "kelihatannya benar".
5. Maksimal 3x percobaan perbaikan yang gagal berturut-turut: BERHENTI,
   laporkan apa yang sudah dicoba + bukti, minta petunjuk user.
