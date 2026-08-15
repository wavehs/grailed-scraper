# Live Grailed parsing architecture

## Tool boundaries

| Concern | Implementation |
|---|---|
| T1 Algolia HTTP | `scrapling.fetchers.FetcherSession` via `services/transport/scrapling_http.py` |
| HTTP fallback | `httpx[socks]` via `services/transport/httpx_http.py` |
| T2 browser | Scrapling `StealthySession` / `AsyncStealthySession` via `services/sources/grailed/browser/` |
| Emergency browser | `camoufox.async_api.AsyncCamoufox` only in `browser/` |
| T3 HTML parsing | `scrapling.Selector(adaptive=True)` via `services/sources/grailed/dom/` |

Everything outside those wrappers depends on `HttpTransport` or `BrowserSession`. Scrapling and Camoufox imports do not cross these boundaries.

## Live tiers

| Tier | Path | Use |
|---|---|---|
| T1 | Direct Algolia multi-query over Scrapling HTTP | Default |
| T2 | In-page Algolia fetch/response interception | Live 401/403/429/WAF escalation |
| T3 | Allowed DOM pages plus embedded JSON | Only when T1/T2 are insufficient |

T1 retries rotate `-dsn.algolia.net`, then `-1/-2/-3.algolianet.com`. Credential refresh on 401/403 is single-flight. Browser, HTTP session, proxy, UA, language, and cookies must represent one client. Stop on CAPTCHA or prohibited automation.

## Discovery

Discovery captures and persists Algolia credentials, indices, replicas, facets, key ACL/expiry/rate limits, pagination limits, maximum page size, and a schema sample. Refresh under a lock after 401/403. API keys are always masked.

## Pagination and coverage

Use the first available complete strategy:

1. `/browse` cursor when ACL permits.
2. Keyset pagination on a sorted replica.
3. Adaptive recursive range splitting using zero-hit probes.

Use multi-query batches of at most eight, disable analytics/highlighting, and never assume static price buckets cover an index. Every brand reports expected hits, collected unique hits, coverage, duplicates, and partial/truncated state.

## Persistence and lifecycle

Field paths live in `config/sources/grailed.yaml`. Money stays `Decimal`. Listings are upserted by `grailed_id` with `raw_json`, `schema_version`, fetch tier, and parser run ID. A missing active listing becomes `removed_pending`, never sold. `parser_run_tasks` and cursors make runs resumable; progress is persisted at least every two seconds.

## Resource limits

Defaults are at most 90 requests/minute and three concurrent requests. Reuse one browser per run and restart after 300 requests or 20 minutes. Close browser pages, sessions, and transports after success, cancellation, or error.
