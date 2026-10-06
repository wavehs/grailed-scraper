# Grailed Liquidity Analyzer

Live-only parser for Grailed listings. The runtime has no mock, replay, synthetic-source, or offline acceptance mode.

## Quick Start (Automated 1-Click Setup)

Requirements: Python 3.11+, Node.js 20+, pnpm 9+.

### Windows
1. Run `setup.bat` (or in PowerShell: `.\scripts\install.ps1`).
2. Run `start.bat` (or `.\scripts\start.ps1`) to launch the interactive control center.

### Linux / macOS
1. Run `bash scripts/install.sh`.
2. Run `./scripts/start.sh` (or double-click `start.command` on macOS).

---

## Manual Setup (Alternative)

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

## Local model grouping

Install Ollama and run `ollama pull qwen3:8b`. In the project `.env` set
`APP_AI_GROUPING_PROVIDER=ollama`, `APP_OLLAMA_MODEL=qwen3:8b`, and
`APP_OLLAMA_CONTEXT=4096`, then restart the backend. Open **AI grouping** and run
the 100-item canary against existing real listings before processing the remainder.
The client only connects to `127.0.0.1:11434`; it has no cloud fallback.
Colors remain variants within a model. Conflicting product types or insufficient
evidence keep listings separate. Progress, cancellation, resume, backup and rollback
use the existing grouping workflow. See [testing gates](docs/TESTING.md).

On the checked GTX 1660 Super / 16 GB RAM machine, Qwen3 8B passed both local
passes for the user's Geobasket Milk and Dagger pendant/hat examples. This is a
hardware/example probe, not measured accuracy on the actual listing collection.

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
