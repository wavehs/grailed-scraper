## 5. Algolia Client v2

### 5.1. Хосты и retry-стратегия (как в официальных SDK)

```
read hosts (в порядке, с shuffle хвоста):
  1. {app}-dsn.algolia.net        # приоритетный, CDN-кэш
  2. {app}-1.algolianet.com
  3. {app}-2.algolianet.com
  4. {app}-3.algolianet.com
```
Таймауты: connect 2с, read 8с (настраивается). При таймауте/5xx — следующий хост; после полного круга — экспоненциальный backoff. Хост, отдавший ошибку, помечается «down» на 5 минут.

### 5.2. Эндпоинты

| Назначение | Метод/путь |
|---|---|
| Мульти-запрос (**основной**) | `POST /1/indexes/*/queries` |
| Одиночный запрос | `POST /1/indexes/{index}/query` |
| Browse (если ACL) | `POST /1/indexes/{index}/browse` (+ `cursor`) |
| Search facet values | `POST /1/indexes/{index}/facets/{facet}/query` |
| Интроспекция ключа | `GET /1/keys/{key}` |

Multi-query тело:
```json
{"requests":[
  {"indexName":"Listing_sold_production","params":"..."},
  {"indexName":"Listing_sold_production","params":"..."},
  {"indexName":"Listing_production","params":"..."}
]}
```
**До 8 подзапросов в одном HTTP-вызове** (конфиг `algolia_multiquery_batch_size`). Это единственный самый большой выигрыш по скорости и по нагрузке на чужой сервис одновременно.

### 5.3. Базовые параметры каждого запроса

```
hitsPerPage        = min(discovered_max, settings.hits_per_page)   # дефолт 200
page               = N
filters            / facetFilters / numericFilters
attributesToHighlight = []      # экономия трафика
attributesToSnippet   = []
getRankingInfo        = false
analytics             = false   # не засоряем чужую аналитику
clickAnalytics        = false
enableABTest          = false
responseFields        = ["hits","nbHits","page","nbPages","hitsPerPage",
                         "exhaustiveNbHits","facets","cursor","queryID"]
```

`attributesToRetrieve`: по умолчанию `["*"]` (нужен `raw_json`). Если объём критичен — режим `lean` с явным списком полей из YAML-конфига (переключатель в settings).

### 5.4. Заголовки

```
x-algolia-application-id: {app_id}
x-algolia-api-key:        {api_key}
Content-Type:             application/json
Origin:                   https://www.grailed.com
Referer:                  https://www.grailed.com/
Accept-Language:          {из браузерной сессии}
User-Agent:               {из браузерной сессии}
```
`x-algolia-agent` передаётся query-параметром, **дословно как перехватили** (это метка клиента Algolia, а не средство обхода защиты).

### 5.5. Обработка кодов ответа

| Код | Класс ошибки | Политика |
|---|---|---|
| 200 | — | ok; проверить `exhaustiveNbHits` |
| 400 | `AlgoliaBadQuery` | не ретраить; залогировать params; пометить задачу `failed_permanent` |
| 401/403 | `AlgoliaAuthError` | инвалидировать credentials → re-discovery (под локом) → 1 повтор → эскалация tier |
| 404 | `AlgoliaIndexNotFound` | пометить индекс мёртвым, перезапустить index_prober |
| 429 | `AlgoliaRateLimited` | respect `Retry-After`, иначе backoff 5→15→45с; снизить глобальный RPS на 50% на 10 минут |
| 5xx / timeout | `AlgoliaTransient` | следующий хост, до `max_retries` |
| не-JSON body | `WafChallenge` | немедленная эскалация на T2 |

---
