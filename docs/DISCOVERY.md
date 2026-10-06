## 4. Discovery-фаза (новое, критично)

Выполняется **редко** (раз в TTL или по кнопке), результат кэшируется в `source_credentials` + `source_schema`.

### 4.1. Шаг 1 — Credentials + индексы из конфигурации страницы

Один GET `https://www.grailed.com/shop` обычным HTTP-клиентом. В HTML есть
`window.PUBLIC_CONFIG = {"algolia": {"app_id": ..., "public_search_key": ..., "indexes": {...}}}`.
Из него берутся `app_id`, публичный search-only ключ и имена listing-индексов
(`services/sources/grailed/discovery/page_config.py`). Браузер не нужен. Если конфигурации
нет, discovery завершается ошибкой `discovery_unavailable`, и прогон не начинается.

Кандидаты индексов проверяются в порядке: сначала основные `Listing_production` и
`Listing_sold_production`, затем реплики из конфигурации.

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
valid_until, discovered_at, discovery_method ("page_config"|"manual"),
last_verified_at, verification_status
```

Инвалидация:
- `now > valid_until` (из ключа) или `now - discovered_at > ttl_hours` (дефолт 12ч);
- первый же `401/403` от Algolia → пометить `verification_status=stale`, запустить re-discovery **однократно** (single-flight под блокировкой);
- ручная кнопка «Refresh credentials» в UI.


---
