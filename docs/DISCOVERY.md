## 4. Discovery-фаза (новое, критично)

Выполняется **редко** (раз в TTL или по кнопке), результат кэшируется в `source_credentials` + `source_schema`.

### 4.1. Шаг 1 — Credentials + индексы (Camoufox через Scrapling)

```python
# псевдокод; реальные имена аргументов проверять по capabilities.py
async def discover(proxy, geoip=True):
    captured = []

    async def page_action(page):
        page.on("request",  lambda r: _maybe_capture_request(r, captured))
        page.on("response", lambda r: _maybe_capture_response(r, captured))
        await page.goto("https://www.grailed.com/shop/…?query=hoodie",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)   # дать XHR уйти
        return page

    resp = await StealthyFetcher.async_fetch(
        "https://www.grailed.com/",
        headless=True, humanize=True, geoip=geoip, proxy=proxy,
        network_idle=True, block_images=True, disable_resources=False,
        solve_cloudflare=True, timeout=45000, page_action=page_action,
    )
```

Что вытаскиваем из перехвата:

| Что | Откуда |
|---|---|
| `app_id` | заголовок `x-algolia-application-id`, либо поддомен URL `https://{APPID}-dsn.algolia.net`, либо query-параметр |
| `api_key` | заголовок `x-algolia-api-key` или query-параметр |
| `x-algolia-agent` | query-параметр — **сохранить и переиспользовать дословно** |
| имена индексов | тело запроса `{"requests":[{"indexName":"..."}]}` |
| активные facetFilters | тело запроса — так узнаём **реальное имя фасета бренда** |
| cookies | `resp.cookies` → в HTTP-сессию |
| UA / язык | `resp.request_headers` |

> **Важно:** страницу поиска открываем с непустым запросом и, если возможно, сразу с фильтром по дизайнеру (`/designers/rick-owens`) — тогда в перехваченном запросе будет видна и структура `facetFilters`, и имя sold-индекса (страница «sold» отдельная).

### 4.2. Шаг 2 — Fallback: JS-бандл

Если перехват пуст: скачать HTML + все `<script src>` (через `FetcherSession`), прогнать regex:

- `["']?(?:appId|applicationId|ALGOLIA_APP_ID)["']?\s*[:=]\s*["']([A-Z0-9]{8,12})["']`
- `["']?(?:apiKey|searchApiKey|ALGOLIA_.*KEY)["']?\s*[:=]\s*["']([a-f0-9]{32})["']`
- `["']([A-Za-z_]+_(?:production|prod))["']` — кандидаты индексов

Каждый кандидат **валидируется пробным запросом** до сохранения.

### 4.3. Шаг 3 — Интроспекция ключа

`GET https://{app}-dsn.algolia.net/1/keys/{api_key}` с этим же ключом (Algolia разрешает ключу читать сам себя; при 403 — просто пропускаем).

Что даёт:

| Поле | Как используем |
|---|---|
| `acl` | есть ли `browse` → включить Browse-стратегию (снимает лимит 1000!) |
| `indexes` | список разрешённых индексов/паттернов |
| `validity` / `validUntil` | **TTL кэша ключа = validUntil − 10 мин**, а не жёсткие 24ч |
| `maxQueriesPerIPPerHour` | автонастройка rate limiter'а (берём 50% от лимита) |
| `maxHitsPerQuery` | верхняя граница `hitsPerPage` |

### 4.4. Шаг 4 — Проб индексов и реплик

Кандидаты (проверяются запросом `hitsPerPage=1`):

```
Listing_production
Listing_by_date_added_production
Listing_by_low_price_production
Listing_by_high_price_production
Listing_by_popularity_production
Listing_sold_production
Listing_sold_by_date_added_production
```

Для каждого живого индекса определяем:
- `nbHits` на пустом запросе (размер индекса),
- **`paginationLimitedTo`** — бинарным пробом: запрос `page=P, hitsPerPage=1`, ищем максимальное P без ошибки/пустоты (обычно 1000/hitsPerPage). Кэшируем.
- **max `hitsPerPage`** — проб 1000 → 500 → 250 → 100, берём максимальный принятый.
- признак сортировки (по первому/последнему hit) → пригоден ли для keyset-пагинации.

### 4.5. Шаг 5 — Проб фасетов и схемы

1. Запрос `facets:["*"], maxValuesPerFacet:100, hitsPerPage:0` → **полный список фасетных атрибутов**. Из него определяем реальное имя бренд-фасета (`designers.name` / `designer_names` / `brand`) и категорийного.
2. `POST /1/indexes/{idx}/facets/{brandFacet}/query` (searchForFacetValues) → **автоматическое сопоставление наших брендов с точными именами на Grailed** (см. §11).
3. Выборка 200 hits (`attributesToRetrieve: ["*"]`) → `schema_sampler` строит карту: `field → (частота, тип, пример)`. Сохраняется в `source_schema`. При следующем прогоне сравнивается → **schema drift alert**.

### 4.6. Кэш и инвалидация

Таблица `source_credentials`:

```
id, source ("grailed"), app_id, api_key, algolia_agent,
active_index, sold_index, sorted_indices (json), brand_facet, category_facet,
key_acl (json), pagination_limit, max_hits_per_page,
valid_until, discovered_at, discovery_method ("intercept"|"bundle"|"manual"),
last_verified_at, verification_status
```

Инвалидация:
- `now > valid_until` (из ключа) или `now - discovered_at > ttl_hours` (дефолт 12ч);
- первый же `401/403` от Algolia → пометить `verification_status=stale`, запустить re-discovery **однократно** (с блокировкой, чтобы 10 воркеров не подняли 10 браузеров);
- ручная кнопка «Refresh credentials» в UI.

**Ручной ввод**: в Settings можно вбить app_id/api_key/индексы руками (режим `discovery_method=manual`), тогда браузер не поднимается вообще.

---
