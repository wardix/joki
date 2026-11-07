---
name: SOP Proyek Node (Next.js / Vue / TypeScript)
always_on: false
trigger: [nextjs, next.js, vue, nuxt, typescript, tsconfig, pinia, vite]
---

Untuk tugas Node/Next.js/Vue/TypeScript, ikuti konvensi ini:

1. TypeScript strict: `interface` untuk object shape, jangan `any`
   (pakai `unknown` bila ragu), `async/await` + `Promise.all()`,
   optional chaining (`?.`) dan nullish coalescing (`??`).
2. Struktur: `src/components/`, `src/pages/`, `src/hooks/`, `src/utils/`,
   `src/types/`. Test berdampingan (`*.test.ts`) atau `__tests__/`.
3. Jangan andalkan linter saja (flag `--ext` mati di ESLint v9):
   verifikasi dengan `tsc --noEmit` + `npx jest` + run dev/build.
4. Alur kerja per file: `read_file` → `edit_file` → `tsc`/`jest`.
   Setelah ubah util/tipe bersama, uji juga komponen pemakainya.
   Untuk `.vue`, hindari rename otomatis — petakan via
   `analyze_deps` + `search_code` dulu.
5. Manajer paket ikut proyek yang ada (npm/pnpm/bun) — jangan campur.
   Bila spec ambigu (versi, package manager, struktur), tanya via
   `question` sebelum menebak.
6. Untuk ubahan tampilan (komponen/CSS): build/lint yang lolos bukan
   bukti tampil benar. Pastikan elemen kunci masih ada (`search_code`)
   + bukti render (`screenshot`, `curl` dev server, atau build sukses)
   sebelum klaim selesai/responsive.
