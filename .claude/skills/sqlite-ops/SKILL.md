---
name: sqlite-ops
description: Back up, prune (retention), or restore the local SQLite database (data/grailed.db) using app.cli. Use when the user asks to back up, restore, or clean up the database.
disable-model-invocation: true
---

# SQLite operations

Run everything from `backend/` with `.venv/Scripts/python -m app.cli ...`. Every command prints JSON; show the user the result.

## Backup
1. `db-backup` writes a snapshot to `data/backups/grailed-<UTC timestamp>.sqlite3` and prints the path.

## Retention (deletes data)
1. Preview with `retention` (dry run) and show the counts to the user.
2. Run `retention --apply` only after the user explicitly confirms. Take a `db-backup` first.

## Restore (overwrites the live DB)
1. Preview with `db-restore <backup-file>`. This checks the backup's integrity and prints the source and target paths.
2. Ask the user to stop the backend (uvicorn, desktop app, `start.bat`). With `--apply`, the command refuses to run while the app is running.
3. After explicit confirmation, run `db-restore <backup-file> --apply`. It takes a safety backup automatically and re-checks integrity afterward.
4. Report the `safety_backup` path so the user can roll back.
5. If the backup predates the latest migration, run `.venv/Scripts/alembic upgrade head`.

Never delete files in `data/backups/` by hand. Retention handles pruning (`APP_BACKUP_RETENTION_DAYS`).
