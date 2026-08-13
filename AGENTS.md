## Parser Architecture (v2: Scrapling + Camoufox)

### Toolchain roles — DO NOT MIX THESE UP

| Concern | Tool | Wrapper module |
|---|---|---|
| HTTP requests to Algolia | `scrapling.fetchers.FetcherSession` (curl_cffi, TLS impersonation) | `services/transport/scrapling_http.py` |
| HTTP fallback | `httpx[socks]` | `services/transport/httpx_http.py` |
| Stealth browser | `scrapling.fetchers.StealthySession` / `AsyncStealthySession` (Camoufox engine) | `services/sources/grailed/browser/session_pool.py` |
| Emergency browser | `camoufox.async_api.AsyncCamoufox` | `browser/raw_camoufox.py` |
| HTML parsing | `scrapling.Selector(adaptive=True)` | `services/sources/grailed/dom/*` |

Camoufox is NEVER imported outside `browser/`. Scrapling is NEVER imported
outside `transport/` and `sources/grailed/{browser,dom}/`. Everything else
depends only on the `HttpTransport` / `BrowserSession` Protocols in
`services/transport/protocols.py`. This isolates us from upstream API drift.

### Four-tier fetch strategy
- T0 mock/replay (fixtures, fake Algolia server) — used in dev/CI
- T1 direct Algolia over Scrapling HTTP — DEFAULT, 95% of traffic
- T2 browser-mediated Algolia (in-page `fetch()` via `page.evaluate`, or
  `page.on("response")` interception) — auto-escalation on 401/403/429/WAF
- T3 DOM fallback with Scrapling adaptive selectors + `__NEXT_DATA__`
Escalation/de-escalation rules and circuit breakers: see docs/PARSING.md §2.

### Discovery phase (run rarely, cached)
Credentials, index names, replicas, brand facet name, key ACL
(`GET /1/keys/{key}` → detects `browse` permission, `validUntil`,
`maxQueriesPerIPPerHour`), `paginationLimitedTo`, max `hitsPerPage`,
and a schema sample. Persisted in `source_credentials` + `source_schema`.
Invalidate on 401/403 (under a lock — never launch N browsers concurrently).

### Pagination — MANDATORY rules
Algolia caps `page * hitsPerPage <= paginationLimitedTo` (usually 1000).
Never assume static price buckets are enough. Use, in priority order:
1. `/browse` with cursor (if ACL allows)
2. keyset/seek pagination on a sorted replica via `numericFilters`
3. adaptive recursive range splitting with `hitsPerPage=0` probes
Always compute a coverage ratio per brand and surface `partial`/`truncated`
in the run report. Silent data loss is a bug.

### Efficiency rules
- Use `POST /1/indexes/*/queries` (multi-query, up to 8 sub-queries per call).
- Set `analytics=false`, `clickAnalytics=false`, `attributesToHighlight=[]`.
- Retry across Algolia hosts: `-dsn.algolia.net`, then `-1/-2/-3.algolianet.com`.
- Reuse one browser per run; hard-restart every 300 requests / 20 minutes.
- Bridge cookies + UA + Accept-Language + proxy between browser and HTTP
  session — they must look like ONE client.

### Field mapping
Field mapping lives in `config/sources/grailed.yaml`, NOT in Python.
Each logical field has an ordered list of candidate JSON paths.
Adding/renaming a source field must be a YAML change.

### Hard prohibitions
- Do NOT use Selenium/Puppeteer/undetected-chromedriver.
- Do NOT bypass captchas manually; only Scrapling's built-in interstitial handling.
- Do NOT log or return API keys unmasked.
- Do NOT use float for money — use `Decimal` end to end.
- Do NOT treat a disappeared active listing as sold; use `removed_pending`.
- Do NOT store seller usernames in plaintext unless explicitly enabled.
- Do NOT exceed 90 req/min or 3 concurrent requests by default.
- Do NOT require network or a browser for the default test suite.

### Always
- upsert by `grailed_id`, never duplicate;
- persist `raw_json` and `schema_version`;
- write `parser_run_tasks` so a run is resumable;
- update `parser_run` progress at least every 2 seconds;
- pin `scrapling==X.Y.Z` and let Scrapling pull its compatible Camoufox.

### Repository path map — read before changing a subsystem

Documentation index: `docs/INDEX.md`.

| Topic | Canonical path |
|---|---|
| Repository entry point | `README.md` |
| Documentation index | `docs/INDEX.md` |
| Product scope | `docs/PRD.md` |
| Architecture, toolchain, tiers | `docs/PARSING.md` |
| Credentials, indexes, facets, schema | `docs/DISCOVERY.md` |
| Algolia requests and error handling | `docs/ALGOLIA.md` |
| Pagination and coverage | `docs/PAGINATION.md` |
| T2/T3 browser and DOM fallbacks | `docs/BROWSER_FALLBACKS.md` |
| Listing mapping and data quality | `docs/DATA_MODEL.md` |
| Watermarks and listing lifecycle | `docs/LIFECYCLE.md` |
| Brand source mapping | `docs/BRAND_MAPPING.md` |
| Rate limits, proxies, persistence | `docs/OPERATIONS.md` |
| Logs, metrics, health | `docs/OBSERVABILITY.md` |
| Fixtures, fake server, offline tests | `docs/TESTING.md` |
| Runtime settings and field mapping | `docs/CONFIGURATION.md`, `config/sources/grailed.yaml` |
| Scoring contract | `docs/SCORING.md` |
| Legal and ethical limits | `docs/COMPLIANCE.md` |
| Ordered implementation plan | `docs/TASKS.md` |
| Acceptance gate | `docs/DEFINITION_OF_DONE.md` |
| Post-MVP work | `docs/ROADMAP.md` |

### Implementation path map

```text
backend/requirements.txt
backend/app/services/transport/
backend/app/services/sources/base/
backend/app/services/sources/grailed/discovery/
backend/app/services/sources/grailed/algolia/
backend/app/services/sources/grailed/browser/
backend/app/services/sources/grailed/dom/
backend/app/services/parser/
backend/app/services/parser/mock/
backend/app/services/normalization/
backend/app/services/scoring/
backend/app/services/analytics/
backend/tests/
config/sources/grailed.yaml
data/cache/
data/fixtures/
data/logs/
frontend/src/app/
frontend/src/components/
frontend/src/lib/
```

### Installed AAS skills for this repository

The following skills from `sickn33/agentic-awesome-skills` are installed in
the Codex user profile at `C:\Users\Alex\.codex\skills`. Invoke one only when
its task match is clear; they are supplemental playbooks, not project policy.

| Skill | Use for |
|---|---|
| `architecture-patterns` | Cross-cutting backend boundaries or significant refactors. |
| `python-fastapi-development` | FastAPI routes, Pydantic models, SQLAlchemy integration, and API errors. |
| `async-python-patterns` | Cancellation, backpressure, timeouts, concurrency, and async I/O. |
| `web-scraper` | Extraction strategy, validation, data quality, and pagination. |
| `database-design` | Schema, indexes, relationships, migrations, and query performance. |
| `python-testing-patterns` | Pytest, async fixtures, fakes, mocks, property tests, and coverage. |
| `nextjs-app-router-patterns` | Next.js 14 App Router pages, data fetching, and caching. |
| `frontend-api-integration-patterns` | React Query/API state, cancellation, retries, and error presentation. |
| `accessibility-compliance-accessibility-audit` | Keyboard, focus, contrast, and WCAG checks for UI changes. |
| `security-requirement-extraction` | Threat-informed acceptance criteria and security test cases. |
| `pre-ship-gate` | Staging/production release verification, especially when migrations ship. |

#### Conflict resolution for installed skills

This file and the canonical documents in `docs/` always take precedence over
an installed skill. In particular, when applying `web-scraper`, retain the
Scrapling + Camoufox toolchain, four-tier fetch strategy, 90 req/min and three
concurrent-request limits, robots/ToS requirements, and offline default test
suite. Do not substitute Selenium, Puppeteer, or undetected-chromedriver; do
not bypass CAPTCHAs; and do not weaken the `Decimal`, lifecycle, masking, or
coverage requirements above.
