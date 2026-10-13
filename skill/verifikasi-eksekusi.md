---
name: Verifikasi Eksekusi Sebelum Klaim Selesai
always_on: true
---

Sebelum menyatakan sebuah task/fix selesai, WAJIB lakukan verifikasi berikut —
jangan cukup mengandalkan test suite atau linter lolos, karena keduanya
terbukti berkali-kali bisa lolos padahal ada bug nyata di jalur eksekusi:

1. **Eksekusi jalur kode yang diubah secara langsung**, bukan cuma
   import/syntax check. Kalau mengubah fungsi yang dipanggil saat runtime
   (misal tool handler, agent loop, request builder), jalankan skenario
   nyata yang melewati kode itu dan lihat hasilnya — bukan cuma asumsi dari
   membaca kode.

2. **Kalau menyentuh sistem yang punya 2 sisi yang harus selalu sinkron**
   (skema tool vs handler eksekusi, `requirements_linux.txt` vs
   `pyproject.toml`, nama key di config vs yang dirujuk kode/test) —
   cross-check keduanya secara eksplisit, jangan cuma edit salah satu sisi.

3. **Kalau membuat modul/file baru**, pastikan modul itu benar-benar
   dipanggil dari jalur yang aktif (entry point, fungsi yang tereksekusi
   tiap sesi) — bukan cuma didefinisikan lalu diimpor oleh dirinya sendiri
   atau modul lain yang juga tidak pernah dipanggil. Trace dari
   `__main__.py`/entry point sampai ke kode barumu, pastikan benar-benar
   tersambung.

4. **Jangan mengubah scope di luar yang diminta** tanpa menyebutkannya
   secara eksplisit ke user — terutama file konfigurasi (`config.json`) atau
   penghapusan test yang sudah ada. Kalau ada perubahan tambahan yang
   dirasa perlu, sebutkan terpisah dan biarkan user memutuskan, jangan
   digabung diam-diam ke task utama.

5. **Setelah verifikasi, laporkan secara spesifik apa yang dites dan
   hasilnya** (bukan cuma "sudah saya perbaiki") — supaya user bisa menilai
   tingkat keyakinan atas klaim itu.
