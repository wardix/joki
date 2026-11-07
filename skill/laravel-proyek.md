---
name: SOP Proyek Laravel
always_on: false
trigger: [laravel, artisan, eloquent, blade, form request]
---

Untuk tugas Laravel, ikuti konvensi ini (di luar SOP umum):

1. Struktur: `routes/web.php` atau `api.php`, `app/Http/Controllers/`,
   `app/Models/` (Eloquent), `app/Http/Requests/` (validasi),
   `app/Services/` (logika bisnis), `database/migrations/`,
   `database/seeders/`, `resources/views/` (Blade).
2. Jangan tulis scaffold dari nol bila ada generator:
   `composer create-project`, `php artisan make:model/controller/request`.
3. Eloquent dulu, raw SQL hanya bila performa kritis. Validasi di
   Form Request, bukan controller. Task berat (email/export) via Queue.
4. Alur kerja per file: `read_file` → `edit_file` (old_text unik 3-5 baris)
   → `run_command` (`php -l`, `php artisan test`). Setelah ubah service/
   model, uji juga controller/pemanggilnya (`impact_analysis` bila ragu).
5. Dilarang `dd()/dump()` di kode production. `.env`/kredensial DB
   jangan diubah tanpa izin eksplisit — tanya via `question` bila ragu.
6. Untuk ubahan tampilan (Blade/CSS): `php -l` yang lolos bukan bukti
   tampil benar. Pastikan elemen kunci masih ada (`search_code` id/tag)
   + bukti render (`screenshot`, `curl` halaman tersaji, atau
   `artisan view:cache`) sebelum klaim selesai/responsive.
