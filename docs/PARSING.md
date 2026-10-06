# Live Grailed parsing architecture

## Tool boundaries

| Concern | Implementation |
|---|---|
| HTTP | `curl_cffi.requests.AsyncSession(impersonate="chrome")` via `services/transport/curl_http.py` |
| Algolia search | `services/sources/grailed/algolia/` (multi-query, host rotation, retries, circuit breaker) |
| Discovery | `services/sources/grailed/discovery/` (page config, key ACL, indices, facets, schema) |

Everything outside the transport package depends on `HttpTransport` (`services/transport/protocols.py`). There are no browsers, DOM scraping, or proxies: the public search key is read from the regular HTML page and all listing data comes from Algolia JSON.

## Request path

Direct Algolia multi-query over HTTP. Retries rotate `-dsn.algolia.net`, then `-1/-2/-3.algolianet.com`, with backoff and `Retry-After` on 429. A 401/403 triggers one single-flight credential refresh. Stop on CAPTCHA, prohibited automation, or repeated 429.

## Discovery

Discovery reads `window.PUBLIC_CONFIG.algolia` (`app_id`, `public_search_key`, index names) from one GET of `https://www.grailed.com/shop`, then validates the key, its ACL/expiry/rate limits, listing indices, facets, pagination limits, maximum page size, and a schema sample. See [DISCOVERY.md](DISCOVERY.md). API keys are always masked.

## Pagination and coverage

Use the first available complete strategy:

1. `/browse` cursor when ACL permits.
2. Keyset pagination on a sorted replica.
3. Adaptive recursive range splitting using zero-hit probes.

Use multi-query batches of at most eight, disable analytics/highlighting, and never assume static price buckets cover an index. Every brand reports expected hits, collected unique hits, coverage, duplicates, and partial/truncated state.

## Persistence and lifecycle

Field paths live in `config/sources/grailed.yaml`. Money stays `Decimal`. Listings are upserted by `grailed_id` with `raw_json`, `schema_version`, and parser run ID. A missing active listing becomes `removed_pending`, never sold. `parser_run_tasks` and cursors make runs resumable; progress is persisted at least every two seconds.

## Resource limits

Defaults are at most 90 requests/minute and three concurrent requests. Close HTTP sessions after success, cancellation, or error.
