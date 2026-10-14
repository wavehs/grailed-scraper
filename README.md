# Grailed Liquidity Analyzer

Finds Grailed items that are starting to sell well: live listings (active and sold) grouped into brand → product type → model, with sales growth, time to sell, price and supply. The runtime has no mock, replay, synthetic-source, or offline acceptance mode.

## Quick Start (macOS)

Requirements: `brew install uv node@20 pnpm` (uv provides Python 3.11.9 from `.python-version`).

1. Run `make setup` once. It creates `backend/.venv`, installs backend and frontend packages,
   applies migrations and builds the UI. It is safe to rerun; `scripts/setup.sh --force`
   recreates the venv.
2. Run `make start`. It rebuilds the UI when sources changed, applies migrations, and opens
   http://127.0.0.1:8000. For development use `make dev` (backend :8000 + Next.js :3000).
3. Add brands on **Brands**, press **Update data** on **Collect data**, then open **Trends**:
   models with growing sales, fast sales and small supply (see [docs/METRICS.md](docs/METRICS.md)).
   Groups are brand + product type + model ([docs/GROUPING.md](docs/GROUPING.md)) and can be
   renamed, merged or split on the group card.

Before any Grailed request, review the applicable ToS, `robots.txt`, and law.

Do not run Uvicorn with `--reload` or multiple workers: the backend holds a single-instance
lock and SQLite has one writer.

## Checks

```bash
make check            # everything CI runs
make backend-check    # ruff, mypy, pytest --cov
make frontend-check   # eslint, tsc, vitest, next build
make test ARGS="tests/test_metrics.py -k <name>"
```

`make help` lists all targets.

Source-independent checks do not replace the bounded live canary required by [docs/TESTING.md](docs/TESTING.md)
(`python -m app.cli discover`, `canary`, `taxonomy-check`).

## SQLite operations

From `backend/` with the venv active (`source .venv/bin/activate`):

```bash
python -m app.cli retention
python -m app.cli retention --apply
python -m app.cli db-backup
python -m app.cli regroup
python -m app.cli grouping-report --brand "Rick Owens"
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3 --apply
```

Stop the backend before applying a restore.
