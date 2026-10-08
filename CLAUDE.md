<!-- Path-scoped rules: .claude/rules/. Procedures: .claude/skills/. -->

Grailed Liquidity Analyzer: a local tool that collects live Grailed listings (active and sold) for tracked brands, groups them into brand → product type → model lines, and shows which items are trending (sales growth, time to sell, price, supply).

## Non-negotiable constraints

- Live Grailed only. No mock/replay sources, fake Algolia servers, synthetic listings, or offline acceptance flows. Source-independent unit tests are fine; parser changes also need the bounded live canary in `docs/TESTING.md`.
- Data access is plain HTTP: direct Algolia search with the public search key from Grailed's page config. No browsers, no CAPTCHA bypass. Stop on CAPTCHA, prohibited automation, or repeated 429.
- Defaults must not exceed 90 requests/minute or three concurrent requests.
- Keep API keys, salts, and seller PII out of logs and responses. Store seller usernames only in the configured privacy mode.
- Money is `Decimal` end to end. Field mappings live in `config/sources/grailed.yaml`. Upsert by `grailed_id`; persist `raw_json`, `schema_version`, and parser run ID. A missing active listing becomes `removed_pending`, never implicitly sold.
- Pagination never fails silently: report coverage and explicit `partial`/`truncated` state. Runs stay resumable through `parser_run_tasks`.

## Commands

Use the backend venv explicitly (`backend/.venv`, Python 3.11.9). The global `python`/`pip` won't work. Paths below are for Windows.

- Backend, from `backend/`:
  - Tests: `.venv/Scripts/python -m pytest` (~30 s). One test: `.venv/Scripts/python -m pytest tests/test_metrics.py -k <name>`.
  - Lint: `.venv/Scripts/python -m ruff check app tests`.
  - Typecheck: `.venv/Scripts/python -m mypy` (strict; covers `app` and `tests`).
  - Migrations: `.venv/Scripts/alembic upgrade head`.
  - Dev server: `.venv/Scripts/python -m uvicorn app.main:app --port 8000`.
- Frontend, from `frontend/` (pnpm 9.15.9):
  - Lint, typecheck, tests: `pnpm run lint`, `pnpm run typecheck`, `pnpm run test`.
  - Build: `pnpm run build` (static export to `frontend/out/`, served by the backend on :8000). Dev server: `pnpm dev` (port 3000).
- Run the app: `start.bat`. Dev stack: `dev-web.bat` (`scripts/start.ps1 -Mode dev`).

## Subagent Execution Policy (ECC)

When handling complex tasks, codebase exploration, or multi-step implementations, do NOT execute everything directly in the primary session. You must act as an orchestrator and delegate:

- **Exploration & Grepping**: Spawn the `code-searcher` or `scout` subagent. Do not spam broad grep/glob in the root context.
- **Architectural Planning**: Delegate to `planner` before writing major code changes.
- **Implementation**: Delegate specific coding tasks to `coder` or `tdd-guide`.
- **Review**: Delegate to `code-reviewer` before marking work done.

Trigger these subagents automatically via the `Agent` tool without asking for confirmation.

## Verify before calling a task done

- CI (`.github/workflows/ci.yml`) runs the backend checks (ruff check, mypy, pytest) and the frontend checks (lint, typecheck, test, build). Run the ones covering what you touched.

## Gotchas

- Don't run `ruff format` or `prettier --write` on the whole repo. Many files are unformatted, CI doesn't check formatting, and a mass reformat buries the real diff. Match the surrounding style.
- Don't start uvicorn with `--reload` or `--workers`; keep one worker (the app holds a single-instance lock and SQLite has one writer).
- If pnpm commands fail with `MODULE_NOT_FOUND`, `node_modules` is broken. Reinstall with `CI=true pnpm install --frozen-lockfile`; without `CI=true` pnpm hangs on a purge prompt.
- Settings use the `APP_` prefix and read the repo-root `.env`, not `backend/.env`. Variable names are in `.env.example`. The default DB is `data/grailed.db`. Tests redirect DB, lock and logs to a temp dir in `tests/conftest.py`.
- `backend/tests/test_project_config.py` asserts dependency pins and safe limits. If you change a pin or default limit, update that test.
- The root `TASKS.md` is the canonical task list. Most docs and `TASKS.md` are in Russian. Write code, comments, and commit messages in English.

## Repo etiquette

- Commit subject: plain imperative sentence, no Conventional Commits prefix, usually no body. Example: "Add AI grouping persistence and runtime integration".

## Compaction

- When compacting, keep the list of files you changed, the check commands you ran with their pass/fail results, and any compliance decisions.
