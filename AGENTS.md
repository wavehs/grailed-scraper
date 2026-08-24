# Repository instructions

This file is intentionally short because it is loaded for every task.

## Read only what the task needs

- `README.md`: setup and entry points.
- `TASKS.md`: current priorities, status, and gates.
- `docs/INDEX.md`: routing to canonical requirements. Before source or transport work,
  read `docs/PARSING.md`, `docs/COMPLIANCE.md`, and `docs/TESTING.md`. Before mapping or
  lifecycle work, read `docs/DATA_MODEL.md`, `docs/LIFECYCLE.md`, and
  `docs/CONFIGURATION.md`.
- Prefer Codebase Memory for structural discovery and coverage checks. Use `rg` for
  literals, configuration/non-code files, and ranges the index did not cover.

## Non-negotiable constraints

- This is a live Grailed parser. Do not add mock/replay sources, fake Algolia servers,
  synthetic listings, offline parser fixtures, or offline acceptance flows.
  Source-independent tests are allowed, but parser/source acceptance requires the
  smallest bounded live canary described in `docs/TESTING.md`.
- Access only public, read-only data. Follow current ToS, `robots.txt`, and applicable
  law. Stop on CAPTCHA, prohibited automation, or repeated 429 responses. Never use
  Selenium, Puppeteer, undetected-chromedriver, or manual CAPTCHA bypasses.
- T1 direct Algolia is the default; activate T2/T3 only for an observed live failure.
  Keep Scrapling imports inside `backend/app/services/transport/` and
  `backend/app/services/sources/grailed/{browser,dom}/`, and Camoufox imports inside
  `backend/app/services/sources/grailed/browser/`. Other modules depend on
  `backend/app/services/transport/protocols.py`.
- Defaults must not exceed 90 requests/minute or three concurrent requests. Keep API
  keys, proxy credentials, salts, and seller PII out of logs and responses. Store seller
  usernames only in the configured privacy mode. Use `Decimal` for money end to end.
- Field mappings belong in `config/sources/grailed.yaml`, not Python. Upsert by
  `grailed_id`; persist `raw_json`, `schema_version`, fetch tier, and parser run ID.
  A missing active listing becomes `removed_pending`, never implicitly sold.
- Pagination must never fail silently: use browse, then keyset, then adaptive range
  splitting as available; report coverage and explicit `partial`/`truncated` state.
- Runs must remain resumable through `parser_run_tasks`; persist progress at least every
  two seconds. Pin Scrapling and let it select its compatible Camoufox version.

## Change discipline

- Reuse existing helpers and boundaries; make the smallest complete change.
- Run the smallest relevant checks. Parser/source changes also require the bounded live
  gate; offline tests never replace it.
- Update the canonical document when a contract changes. Do not copy implementation
  trees, full document indexes, dependency versions, or installed-skill catalogs here.
  Project rules and canonical docs override external playbooks.
