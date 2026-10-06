# Grailed Liquidity Analyzer

Live-only parser for Grailed listings. The runtime has no mock, replay, synthetic-source, or offline acceptance mode.

## Quick Start (Windows)

Requirements: Python 3.11+, Node.js 20+, pnpm 9+.

1. Run `setup.bat` once. It installs the backend venv, frontend packages, and applies migrations.
2. Run `start.bat`. It rebuilds the UI when sources changed, applies migrations, and opens
   http://127.0.0.1:8000. For development use `dev-web.bat` (backend :8000 + Next.js :3000).

Before any Grailed request, review the applicable ToS, `robots.txt`, and law.

Do not run Uvicorn with `--reload` or multiple workers: the backend holds a single-instance
lock and SQLite has one writer.

## Checks

```powershell
cd backend
ruff check app tests
mypy
pytest

cd ..\frontend
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
```

Source-independent checks do not replace the bounded live canary required by [docs/TESTING.md](docs/TESTING.md).

## SQLite operations

```powershell
python -m app.cli retention
python -m app.cli retention --apply
python -m app.cli db-backup
python -m app.cli market-rebuild
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3 --apply
```

Stop the backend before applying a restore.
