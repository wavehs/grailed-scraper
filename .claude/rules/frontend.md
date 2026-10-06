---
paths:
  - "frontend/**"
---

# Frontend rules

- Add every new UI string to both the `en` and `ru` maps in `frontend/src/lib/i18n.tsx`. Lookup falls back `ru → en → key`, so a missing `ru` key silently shows English and nothing catches it.
- Tests select elements by the English labels. If you rename an `en` string, update the matching `*.test.tsx`.
- The production build is a static export (`output: 'export'`). FastAPI serves `frontend/out/` with an SPA fallback. Don't use SSR, route handlers, server actions, or Next image optimization.
- Dynamic routes need `generateStaticParams`, or the build fails.
- All backend calls go through `frontend/src/lib/api.ts`, whose base is `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000/api`). React Query hooks live in `frontend/src/lib/queries.ts`.
- Use `pnpm` only. Don't add an npm or yarn lockfile. Keep the `pnpm.overrides` pins in `package.json`; they exist for the audit.
