# План реализации парсера
> Заменяет старую Phase 3 целиком. Порядок обязателен. Каждая задача — отдельный PR.

### Task 3.0 — Toolchain + capability probe
Установить `scrapling[fetchers]`, зафиксировать версии, реализовать `transport/capabilities.py`.
**AC:** `pip install` проходит; `scrapling install` (или `python -m camoufox fetch`) описан в README; `capabilities.py` при старте пишет в лог доступные классы/аргументы Scrapling и Camoufox и версии; при отсутствии `StealthySession` приложение стартует, но помечает T2 недоступным; smoke-тест поднимает Camoufox и закрывает (маркер `browser`).

### Task 3.1 — Transport protocols + Scrapling HTTP engine
`HttpTransport` Protocol; `ScraplingHttpTransport` (FetcherSession, impersonate, proxy, cookies, timeouts, retries); `HttpxTransport` fallback; фабрика с автовыбором.
**AC:** обе реализации проходят один и тот же контрактный тест-сьют; смена реализации — одна настройка; куки переносятся между запросами; таймауты и прокси работают; unit-тесты офлайн через ASGI-transport.

### Task 3.2 — Rate limiter, circuit breaker, budget
Token bucket + host semaphore + jitter; AIMD-автоподстройка; circuit breaker; `budget.py`.
**AC:** заданный RPS соблюдается (тест с freezegun, погрешность <5%); 429 → снижение RPS и respect `Retry-After`; брейкер открывается/полуоткрывается по спецификации; `estimate_budget(plan)` возвращает кол-во запросов и ETA.

### Task 3.3 — Proxy Manager v2
Пулы browser/http, health-scoring, cooldown, sticky-сессии, гео-консистентность, тест-эндпоинт.
**AC:** поддержка http/https/socks5 c auth; weighted-выбор; 3 ошибки → cooldown; `POST /api/settings/proxies/test` возвращает статус каждого прокси; при `proxy_enabled=false` всё работает напрямую; все прокси мертвы → поведение по `allow_direct_fallback`.

### Task 3.4 — Browser session pool (Camoufox через Scrapling)
`AsyncStealthySession` пул вкладок, `page_action`-хелперы, hard-restart, graceful shutdown, аварийный `raw_camoufox`.
**AC:** один браузер на прогон; N вкладок используются параллельно; рестарт по счётчику/таймеру; отсутствие зомби-процессов после падения (тест на завершение); переключение на raw Camoufox по флагу.

### Task 3.5 — Credential & index discovery
Перехват request/response, извлечение app_id/api_key/agent/индексов/фасетов, JS-bundle fallback, сохранение в `source_credentials`, лок на конкурентный re-discovery.
**AC:** ключи извлекаются с реального grailed.com (ручной canary-тест); при перехвате пусто — срабатывает bundle-fallback; ключи маскируются в логах и API; повторный вызов при валидном кэше не поднимает браузер; параллельные вызовы приводят ровно к одному запуску браузера.

### Task 3.6 — Key introspection + index/facet/schema prober
`GET /1/keys/{key}`, проб индексов и реплик, `paginationLimitedTo`, max `hitsPerPage`, `facets:["*"]`, `schema_sampler`.
**AC:** ACL/validUntil/лимиты сохраняются и влияют на TTL и rate limiter; список живых индексов и стратегия пагинации записываются в `source_schema`; при 403 на `/1/keys` — graceful degrade; drift-детектор создаёт `schema_alerts` при исчезновении обязательного поля.

### Task 3.7 — Algolia client
`search`, `multi_query`, `browse`, `search_facet_values`, host-retry, классификация ошибок.
**AC:** multi-query группирует до 8 подзапросов; ротация хостов при 5xx/timeout; корректные исключения на 400/401/403/404/429/5xx; не-JSON → `WafChallenge`; все параметры сериализуются в `params`-строку правильно (тест с URL-encoding кавычек и не-ASCII).

### Task 3.8 — Pagination Planner
Три стратегии + coverage check + property-тесты.
**AC:** browse-режим при наличии ACL; keyset корректно обрабатывает тай-брейки на границе (тест с 1500 записями одинакового timestamp); range-split рекурсивно доводит бакеты до ≤ лимита; `hypothesis`-тест: план покрывает диапазон без пропусков и с дублями ≤ 5%; `truncated` выставляется явно; coverage считается и сохраняется.

### Task 3.9 — Field mapping (YAML) + ListingData
`config/sources/grailed.yaml`, маппер с цепочками кандидатов, `Decimal`-деньги, timestamps s/ms, размеры, condition, FX.
**AC:** маппинг всех полей из фикстур; отсутствующие поля → дефолты по спеке; ms-timestamps распознаются; `price_i` приоритетнее `price`; валюта ≠ USD конвертируется и `fx_rate` сохраняется; переименование поля в источнике чинится правкой YAML (тест).

### Task 3.10 — Data quality layer
Валидация + флаги outlier/replica/lot/repost/wrong_brand/no_photos.
**AC:** невалидные hits отбрасываются с логом причины; MAD-outlier детект по группе бренд+категория; репосты схлопываются; пороги читаются из settings; каждый флаг покрыт unit-тестом.

### Task 3.11 — Brand auto-mapping
`searchForFacetValues` + fuzzy scoring + `brand_source_map` + UI-подтверждение + сабрбренды.
**AC:** 21 бренд автоматически резолвится с score ≥ 0.95 (canary-тест); диакритика нормализуется; неоднозначные попадают в UI на подтверждение; несколько маппингов на бренд объединяются в OR-фасет; чекбокс «включать сабрбренды» работает.

### Task 3.12 — Tier 2: browser-mediated Algolia
In-page `fetch()` + passive interception.
**AC:** тот же интерфейс, что у T1 client (drop-in); in-page fetch возвращает валидный JSON с реальной страницы; при провале — переход на interception; количество открытий браузера минимально.

### Task 3.13 — Tier 3: DOM fallback (Scrapling adaptive)
`__NEXT_DATA__`/`ld+json` → adaptive CSS → `find_similar` → regex; robots.txt-чек.
**AC:** парсит сохранённую HTML-фикстуру страницы поиска и страницы листинга; при подмене классов в фикстуре adaptive-режим всё равно находит карточки; robots.txt уважается; результат — тот же `ListingData`.

### Task 3.14 — Tier state machine + escalation
**AC:** эскалация/деэскалация по правилам §2.2; canary-возврат на T1; `parser_run.degraded_mode` и `fetch_tier` у задач заполняются; тесты на fake-сервере, имитирующем 403/429/WAF.

### Task 3.15 — Watermarks + incremental + lifecycle
delta/full режимы, `refresh_active`, `removed_pending → removed/sold`, `listing_price_history`.
**AC:** второй прогон делает существенно меньше запросов (тест на fake-сервере: ≥60% экономии); переходы статусов покрыты табличными тестами; изменение цены пишет строку истории; исчезнувший active не становится sold мгновенно.

### Task 3.16 — Persistence: batch upsert + checkpoints
Батчи 200, `ON CONFLICT`, `COALESCE`-семантика, `parser_run_tasks`, resume.
**AC:** 10k листингов сохраняются за < 20с локально; повторный прогон не создаёт дублей; `first_seen_at` не перезаписывается; kill-9 посреди прогона → «Resume» доводит прогон до конца без потерь и дублей.

### Task 3.17 — Parser orchestrator
Фазы discovery → planning → fetching → normalization → scoring; worker pool; изоляция ошибок бренда; отчёт прогона.
**AC:** ошибка на одном бренде не роняет остальные; параллелизм ограничен настройкой; статусы `pending/running/completed/partial/failed/interrupted`; после сбора автоматически запускаются нормализация и скоринг; отчёт содержит coverage и warnings.

### Task 3.18 — Parser API
`POST /api/parser/run` (+`dry_run`), `POST /api/parser/runs/{id}/cancel`, `POST /api/parser/runs/{id}/resume`, `GET /api/parser/runs`, `/{id}`, `/{id}/progress`, `/{id}/report`, `GET /api/parser/health`, `POST /api/parser/discovery/refresh`.
**AC:** запуск асинхронный; отмена корректно останавливает воркеры (не kill); progress обновляется ≥ раз в 2с; секреты маскированы; dry-run возвращает план и бюджет, ничего не пишет.

### Task 3.19 — Mock generator + fake Algolia server + fixtures
**AC:** fake-сервер реализует `/query`, `/queries`, `/browse`, `/facets/*/query`, `/1/keys/*`; умеет имитировать лимит 1000, 429, 403, 5xx, медленные ответы; генератор даёт ≥ 200 sold и ≥ 200 active на каждый из 21 бренда с реалистичными ценами/датами/лайками (степенное распределение цен, всплески продаж); режим `source_mode=mock` не делает ни одного внешнего запроса; фронтенд полностью разрабатывается на моках.

### Task 3.20 — Observability
JSON-логи с маскированием, метрики прогона, health, schema alerts, баннер в UI.
**AC:** каждое событие из таблицы §17.1 логируется на нужном уровне; ключи никогда не попадают в логи целиком; `stats` заполняется; `/api/parser/health` отражает реальное состояние.

### Task 3.21 — Test harness
Record/replay transport, кассеты, property-тесты, e2e против fake-сервера, canary-CLI.
**AC:** `pytest` зелёный без сети и браузера; coverage парсер-модулей ≥ 80%; `pytest -m browser` и `python -m app.cli canary --brand "Rick Owens" --limit 50` описаны в README.

### Task 3.22 — Scheduler (опционально, после MVP)
APScheduler: ночной delta, недельный full, авто-обновление credentials.
**AC:** расписание настраивается в UI; пропущенный запуск не дублируется; ручной запуск не конфликтует с плановым (лок).

