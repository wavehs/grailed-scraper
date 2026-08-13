# PARSING.md v2 — Спецификация парсинга Grailed
## Scrapling + Camoufox в тандеме

> Версия 2.0. Заменяет предыдущий PARSING.md целиком.
> Ключевые изменения: добавлен Scrapling как основной фреймворк доступа (HTTP + браузер + парсинг), Camoufox остаётся движком stealth-браузера **под** Scrapling, введена 4-уровневая стратегия добычи данных с автоэскалацией, keyset-пагинация вместо наивных ценовых бакетов, discovery-фаза (индексы/фасеты/схема/ACL ключа), инкрементальный режим с watermark'ами, checkpoint/resume, декларативный маппинг полей.

---

## 0. Что изменилось относительно v1

| # | Было (v1) | Стало (v2) | Зачем |
|---|---|---|---|
| 1 | Camoufox напрямую (`AsyncCamoufox`) | Camoufox **через** `Scrapling.StealthySession` | humanize, geoip, block_resources, solve_cloudflare, пул вкладок, единый API; raw Camoufox остаётся как аварийный fallback |
| 2 | `httpx` для Algolia | `Scrapling.FetcherSession` (curl_cffi, TLS/JA3-impersonation) с fallback на `httpx` | совпадение TLS-отпечатка с реальным браузером, HTTP/2-3, сессии, ретраи |
| 3 | Нет DOM-фоллбэка | Tier 3: DOM-парсинг через `Scrapling.Selector(adaptive=True)` | верстка меняется — селекторы саморелоцируются |
| 4 | Один эндпоинт `/query` | `/1/indexes/*/queries` (multi-query), `/browse` (если ACL позволяет), `searchForFacetValues` | в 5–10 раз меньше HTTP-запросов |
| 5 | Пагинация: ценовые бакеты хардкодом | **Pagination Planner**: keyset (seek) по sortable-реплике + рекурсивное адаптивное деление диапазонов | гарантированная полнота выборки при любом объёме |
| 6 | Индексы захардкожены | Discovery-фаза: индексы, реплики, фасеты, схема, лимиты, ACL ключа | устойчивость к изменениям на стороне Grailed |
| 7 | Маппинг полей в коде | Декларативный `config/sources/grailed.yaml` + детектор schema drift | правка конфига вместо релиза кода |
| 8 | Полный ре-парсинг каждый раз | Watermark'и + delta-режим + периодический full refresh | экономия запросов в 10–20 раз |
| 9 | Нет устойчивости к падениям | `parser_run_tasks` + checkpoint/resume, идемпотентность | падение на 18-м бренде не убивает прогон |
| 10 | Просто "лимитер" | Token bucket + per-host semaphore + circuit breaker + бюджет прогона + dry-run | предсказуемость и вежливость |
| 11 | — | Data Quality слой (outliers, реплики, лоты, репосты) | скоринг не должен считаться на мусоре |
| 12 | — | Record/replay фикстуры + fake Algolia server | тесты и разработка без сети |

---

## 1. Стек: кто за что отвечает

### 1.1. Разделение ролей

| Слой | Инструмент | Ответственность |
|---|---|---|
| **HTTP-транспорт (быстрый путь)** | `scrapling.fetchers.FetcherSession` (curl_cffi, impersonate) | 95% всех запросов к Algolia. Persistent-сессия, cookies, ретраи, прокси, TLS-отпечаток Chrome/Firefox |
| **HTTP-транспорт (fallback)** | `httpx[socks]` | если Scrapling-сессия недоступна/несовместима по версии |
| **Stealth-браузер** | `scrapling.fetchers.StealthySession` / `AsyncStealthySession` → **движок Camoufox** | discovery credentials/индексов, Tier-2 добыча, обход интерстишиалов |
| **Stealth-браузер (аварийный)** | `camoufox.async_api.AsyncCamoufox` напрямую | если Scrapling ломается на новой версии |
| **HTML/DOM-парсинг** | `scrapling.Selector` (`adaptive=True`, `find_similar`, `auto_save`) | Tier-3 DOM-фоллбэк, обогащение со страницы листинга |
| **JSON-парсинг** | pydantic v2 + декларативный маппинг | hit → `ListingData` |

### 1.2. Почему тандем, а не что-то одно

- **Только Camoufox** → медленно (браузер на каждый запрос), нет TLS-impersonation для чистого HTTP, нет adaptive-парсинга DOM.
- **Только Scrapling.Fetcher (HTTP)** → нельзя получить Algolia-ключи, нельзя пройти challenge, нельзя увидеть XHR фронтенда.
- **Тандем**: Camoufox (через Scrapling) — *разведка и тяжёлая артиллерия*; Scrapling HTTP — *рабочая лошадка*; Scrapling Selector — *страховка от смены API*.

### 1.3. Cookie/fingerprint bridge (важно!)

Ключевая точка синергии: **браузерная сессия и HTTP-сессия должны выглядеть как один клиент.**

```
1. StealthySession открывает grailed.com (geoip=True, proxy=P)
2. Из ответа берём: cookies, user-agent, accept-language, sec-ch-ua, timezone
3. FetcherSession создаётся с ТЕМ ЖЕ proxy=P, теми же cookies,
   impersonate-профилем, соответствующим UA браузера (firefox/chrome),
   и теми же Accept-Language / Sec-* заголовками
4. Origin/Referer для Algolia-запросов = https://www.grailed.com
```

Правило: **прокси, гео, язык, таймзона и UA внутри одного `SourceSession` всегда согласованы.** Рассинхрон (например, немецкий прокси + `Accept-Language: en-US` + таймзона Europe/Moscow) — главная причина детекта.

### 1.4. Управление версиями и API-дрейфом

Scrapling активно развивается и переименовывает классы (`PlayWrightFetcher` → `DynamicFetcher`, `Adaptor` → `Selector`, `auto_match` → `adaptive`). Поэтому:

- **Все вызовы Scrapling инкапсулированы в `app/services/transport/` и `.../grailed/browser/`.** Остальной код знает только про внутренние протоколы `HttpTransport` / `BrowserSession`.
- Версии **жёстко пинятся** в requirements (`scrapling==X.Y.Z`, camoufox — той версии, которую тянет Scrapling; **не пиновать camoufox отдельно**, чтобы не поймать конфликт).
- Есть задача-«капабилити-проб» на старте приложения: проверяет наличие нужных классов/аргументов и пишет в лог `capabilities report`; при несовместимости включает fallback-реализации.

---

## 2. Четырёхуровневая стратегия добычи

### 2.1. Уровни

| Tier | Название | Механизм | Скорость | Когда используется |
|---|---|---|---|---|
| **T0** | Mock / Replay | Локальные фикстуры или fake Algolia server | мгновенно | разработка, тесты, CI |
| **T1** | Direct Algolia API | `FetcherSession` → `POST /1/indexes/*/queries` | ~30–80 hits/сек | **основной режим** |
| **T2** | Browser-mediated Algolia | Camoufox: `page.evaluate(fetch)` из origin grailed.com **или** перехват `page.on("response")` | ~3–10 hits/сек | T1 отдаёт 401/403/429 подряд, WAF, challenge |
| **T3** | DOM / embedded JSON | Scrapling Selector: `__NEXT_DATA__`, `window.__PRELOADED_STATE__`, adaptive CSS | ~0.5–2 hits/сек | Algolia недоступен полностью |

### 2.2. Правила эскалации / деэскалации (Tier State Machine)

```
STATE: current_tier = T1 (или T0 в mock-режиме)

ЭСКАЛАЦИЯ T1 → T2, если выполняется ЛЮБОЕ:
  - 3 подряд ответа 401/403 после успешного refresh credentials
  - 5 подряд 429 несмотря на backoff
  - 3 подряд ответа с Content-Type != json (WAF-страница / challenge HTML)
  - CircuitBreaker(T1) открыт

ЭСКАЛАЦИЯ T2 → T3, если:
  - 2 подряд неудачных попытки получить Algolia JSON из браузера
  - страница отдаёт challenge, который solve_cloudflare не проходит

ДЕЭСКАЛАЦИЯ T2 → T1:
  - раз в 5 минут делается 1 «пробный» запрос через T1 (canary)
  - 2 успешных canary подряд → возврат на T1, CircuitBreaker закрывается

ПРАВИЛО: понижение tier'а НЕ прерывает прогон.
parser_run.degraded_mode = true, в отчёте фиксируется, какие задачи
собраны каким tier'ом (поле fetch_tier у каждой FetchTask).
```

### 2.3. Circuit Breaker

На каждый (tier, host, proxy) — свой брейкер:
`closed → open` после N=5 ошибок за окно 60с; `open` держится 120с; затем `half-open` — 1 пробный запрос.

---

## 3. Архитектура модулей

```text
backend/app/services/
  transport/
    __init__.py
    protocols.py            # HttpTransport, BrowserSession (Protocol / ABC)
    scrapling_http.py       # FetcherSession-обёртка (impersonate, retries, proxy)
    httpx_http.py           # fallback-реализация HttpTransport
    rate_limiter.py         # token bucket + per-host semaphore + jitter
    circuit_breaker.py
    proxy_manager.py        # пулы, health-scoring, sticky sessions
    response_cache.py       # dev-кэш по hash(query), TTL
    capabilities.py         # проб доступного Scrapling/Camoufox API

  sources/
    base/
      protocols.py          # SourceAdapter, DiscoveryResult, FetchPlan, FetchTask
      models.py             # ListingData, RawHit, BrandRef
      exceptions.py
    grailed/
      adapter.py            # GrailedAdapter: реализует SourceAdapter
      discovery/
        __init__.py
        credential_discovery.py   # Camoufox/Scrapling: ключи + индексы + agent
        js_bundle_fallback.py     # regex по JS-бандлу
        key_introspection.py      # GET /1/keys/{key} → ACL, validUntil, лимиты
        index_prober.py           # проб индексов/реплик/paginationLimitedTo
        facet_prober.py           # facets:["*"] → имена фасетов, searchForFacetValues
        schema_sampler.py         # выборка N hits → карта полей, drift-детект
      algolia/
        client.py           # search / multi-query / browse / SFFV
        hosts.py            # -dsn.algolia.net + -1/-2/-3.algolianet.com, shuffle+retry
        query_builder.py
        pagination.py       # PaginationPlanner (keyset + adaptive range split)
      browser/
        session_pool.py     # AsyncStealthySession(max_pages=N) поверх Camoufox
        interceptor.py      # page.on("response") → сбор Algolia JSON
        inpage_client.py    # page.evaluate(fetch) — предпочтительный T2
        raw_camoufox.py     # аварийный прямой Camoufox
      dom/
        search_page.py      # Scrapling Selector, adaptive=True
        listing_page.py     # обогащение: __NEXT_DATA__ / measurements / фото
      mapping.py            # RawHit → ListingData по YAML-конфигу
      quality.py            # валидация + data-quality фильтры
  parser/
    orchestrator.py         # ParserService: цикл прогона
    planner.py              # построение FetchPlan (бренды × индексы × бакеты)
    worker_pool.py          # bounded async workers
    checkpoint.py           # persist/resume FetchTask
    progress.py             # обновление parser_run + SSE/polling
    writers.py              # батчевый upsert, price history
    budget.py               # оценка кол-ва запросов и времени до старта
    mock/
      generator.py
      fake_algolia_server.py
      fixtures/*.json

config/sources/grailed.yaml  # индексы, фасеты, маппинг полей, бакеты
```

---
