# Grailed Liquidity Analyzer

Live-only parser for Grailed listings. The runtime has no mock, replay, synthetic-source, or offline acceptance mode.

## Setup

Requirements: Python 3.11.9, Node.js 20.19.5, pnpm 9.15.9.

```powershell
Copy-Item .env.example .env
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
scrapling install
alembic upgrade head
python -m app.cli doctor
```

Before any Grailed request, review the applicable ToS, `robots.txt`, and law, then set `APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true`.

```powershell
python -m app.cli canary --brand "Rick Owens" --limit 50
uvicorn app.main:app --port 8000

cd ..\frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

On Windows, do not enable Uvicorn `--reload` or multiple workers: they select an
event loop without subprocess support, while the Scrapling browser requires it.

The UI workflow is discovery → brand mapping → dry run → confirmation → run. T1 direct Algolia is the default; T2 browser-mediated Algolia and T3 DOM are live fallbacks only.

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
