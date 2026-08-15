# Release runbook

Current verdict: `HOLD` until the current revision passes the bounded live workflow.

1. Review ToS, `robots.txt`, and applicable law; set `APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true` only after approval.
2. Install pinned dependencies, run `scrapling install`, apply migrations, and verify `python -m app.cli doctor`.
3. Run backend lint, typing, source-independent tests, frontend lint/typecheck/tests/build, and dependency audits.
4. Refresh live discovery and run `python -m app.cli canary --brand "Rick Owens" --limit 50`.
5. Run the UI workflow discovery → mapping → dry run → confirmation → run. Confirm coverage, duplicate count, lifecycle, tier, warnings, and cleanup.
6. Verify backup/restore preview and `PRAGMA integrity_check`.
7. Tag only the exact verified revision. Roll back application revision and database backup together if the release check fails.

Any CAPTCHA, automation prohibition, repeated 429, secret/PII leak, silent truncation, or revision mismatch keeps the verdict at `HOLD`.
