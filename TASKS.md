# Live-first план Grailed Liquidity Analyzer

Цель проекта — рабочий parser реального Grailed, а не отдельная offline-симуляция.
Каждая фаза закрывается фактическим live-результатом. Mock/replay, fake Algolia,
синтетические fixtures и offline e2e не являются продуктовой функциональностью и не
доказывают работоспособность parser.

## Обязательные правила

- Сначала доказать минимальный live-путь, затем расширять архитектуру и UI.
- Не создавать T0, mock/replay source modes, fake servers или синтетический parser stack.
- Не подменять live-проверку тестом, snapshot или fixture.
- Небольшие unit-проверки допустимы только для независимой логики денег, privacy и
  persistence; они не могут закрывать live gate.
- Все запросы read-only; соблюдать актуальные ToS, `robots.txt` и применимое право.
- Не обходить CAPTCHA вручную и не применять Selenium/Puppeteer/undetected-chromedriver.
- Не превышать 90 запросов/мин и 3 одновременных запроса без отдельного решения владельца.
- Не логировать и не возвращать API keys, proxy credentials или seller PII.
- Деньги обрабатывать через `Decimal`; upsert выполнять по `grailed_id`.
- Неполную выборку всегда показывать как `partial`/`truncated` с coverage.
- При запрете автоматизации, CAPTCHA или повторяющихся 429 остановить live-run.

## Фаза 0 — Немедленно доказать доступ к Grailed

- [x] Проверить актуальные ToS и `robots.txt`.
- [x] Запустить Camoufox discovery и получить актуальные Algolia `appId`, search key,
  agent, indices, replicas, facets, ACL и pagination limits.
- [x] Маскировать credentials во всех логах, API и ошибках discovery.
- [x] Выполнить один прямой T1-запрос к Algolia для `Rick Owens`, limit 10.
- [x] Подтвердить, что ответ содержит реальные listings и необходимые поля.
- [x] Повторить canary с limit 50 и сохранить только обезличенный диагностический отчёт:
  количество hits, index, tier, latency и schema fields — без ключей и seller PII.
- [x] При 401/403/429 определить реальную причину до написания новых fallback-слоёв.

**Результат 2026-08-14:** `PASS` — T1 вернул 50/50 валидных `Rick Owens` listings,
0 rejected. 401/403/429 не наблюдались; два локальных HTTP 400 устранены в общем query
builder. Обезличенный отчёт: `data/logs/phase0_canary_20260814.json`.

**Gate:** T1 возвращает 50 валидных реальных listings либо зафиксирован честный `HOLD` с
конкретной внешней причиной. Без этого следующие фазы не начинаются.

## Фаза 1 — Удалить offline-направление

- [x] Удалить `mock`/`replay` из runtime config, API и UI.
- [x] Удалить T0, fake Algolia server, генераторы синтетических listings и fixture catalog.
- [x] Удалить offline e2e и тесты, проверяющие поведение вымышленного Grailed.
- [x] Оставить только проверки source-independent инвариантов: `Decimal`, masking,
  migrations, database constraints и upsert без дублей.
- [x] Обновить `README.md`, `docs/PRD.md`, `docs/PARSING.md`, `docs/TESTING.md` и остальные
  документы: продукт работает только с live Grailed.

**Gate:** в коде и документации нет альтернативного mock/replay parser; ни один checklist
не выдаёт offline-прогон за доказательство работы с Grailed.

**Результат 2026-08-14:** `PASS` — runtime/API/UI оставлены live-only, T0 и локальный
источник удалены. Ruff, mypy, 71 backend test, frontend lint/typecheck, 8 test и build
зелёные. Live T1 canary: 10/10 валидных `Rick Owens` listings, 0 rejected.

## Фаза 2 — Полный live-сбор одного бренда

- [x] Выбрать стратегию пагинации по фактическому ACL: `/browse`, keyset или adaptive split.
- [x] Собрать все доступные active и sold listings одного бренда.
- [x] Проверить отсутствие пропусков и дублей; вычислить coverage.
- [x] Реализовать rotation Algolia hosts, bounded retry/backoff и обработку 401/403/429/5xx.
- [x] Инвалидировать credentials на 401/403 через single-flight discovery.
- [x] Не активировать T2 browser-mediated Algolia: фактический T1 canary его не требует.
- [x] Не активировать T3 DOM fallback: T1 достаточен; robots-проверка остаётся обязательной
  при будущей фактически обоснованной активации.
- [x] Проверить освобождение browser pages, HTTP sessions и transports после run/error.

**Gate:** один бренд собран end-to-end из live Grailed с измеренным coverage и без дублей.

**Старт фазы 2026-08-14:** live discovery подтвердил ACL `search` без `browse` и лимит
пагинации 1000. Имена реплик не обеспечили строгий seek-порядок, а `nbHits` оказался
`exhaustiveNbHits=false`, поэтому для active и sold выбран adaptive range split по numeric `id`.
Полный T1-сбор `Rick Owens`: 53 619 active + 58 495 sold, coverage 1/1, 0 дублей в output,
0 missing IDs, `truncated=false`, 220 запросов, 0 HTTP errors/429. Обезличенный отчёт:
`data/logs/phase2_collection_20260814.json`.

**Результат фазы 2026-08-14:** повторный gate после resilience-изменений — `PASS`:
10/10 bounded canary и полный T1-сбор 53 619 active + 58 495 sold; coverage 1/1,
0 дублей, 0 missing IDs, `truncated=false`, 220 запросов, 0 retries/HTTP errors/429.
Ruff/mypy изменённых файлов и 73 backend test зелёные; T2/T3 не запускались.

## Фаза 3 — Реальные данные и lifecycle

- [x] Нормализовать фактическую live schema через `config/sources/grailed.yaml`.
- [x] Сохранять `raw_json`, `schema_version`, fetch tier и parser run id.
- [x] Выполнять batch upsert по `grailed_id`.
- [x] Хранить цены только через `Decimal` и исходную currency/fx metadata.
- [x] Реализовать full и delta watermarks на реальных runs.
- [x] Не считать исчезнувший active listing проданным: сначала `removed_pending`.
- [x] Повторить live run и подтвердить корректные inserts/updates без дублей.
- [x] Проверить schema drift на фактическом изменении ответа, не на синтетическом payload.

**Gate:** два последовательных live-run корректно обновляют одну SQLite DB и lifecycle.

**Старт фазы 2026-08-14:** сохранённая live schema discovery (200 hits) сверена с YAML.
Исправлены единицы `price`/`price_i` (доллары, не центы), отдельный `sold_price`,
`user.*`, `photo_count`, `followerno`, `category_path` и фактические condition slugs;
mapping помечен `schema_version=2`. Ruff, strict mypy и 73 backend test зелёные.
Повторный сетевой canary остановлен штатным gate `live_compliance_not_acknowledged`.

**Результат фазы 2026-08-14:** `PASS`. Full run 1 сохранил 53 616 active +
58 495 sold (112 111 inserts), coverage 1/1, 220 T1-запросов; delta run 3 выполнил
7 updates без inserts за 6 запросов, coverage 1/1. Bounded lifecycle run 6 проверил
500/500 active listings и выполнил 500 updates за 5 запросов при hard cap 20;
исчезнувших listings в выборке не было, переход missing → sold-check →
`removed_pending` защищён общим lifecycle path. В SQLite 0 дублей, все строки имеют
`schema_version=2`, T1 и parser run id; raw seller PII не найден, integrity `ok`.
Фактический drift старого mapping (`seller.*`, cents, human condition labels) к live
schema (`user.*`, currency units, condition slugs) исправлен только в YAML и помечен
новой schema version. Диагностические interrupted runs 2/4/5 не засчитаны в gate.
Отчёт: `data/logs/phase3_lifecycle_20260814.json`.

## Фаза 4 — Live parser runtime и UI

- [x] Оставить единственный source mode: live.
- [x] Реализовать UI workflow discovery → brand mapping → dry run → confirmation → run.
- [x] Показывать progress не реже одного раза в 2 секунды.
- [x] Показывать tier, requests, coverage, partial/truncated, warnings и ошибки источника.
- [x] Реализовать cancel/resume и восстановление незавершённых `parser_run_tasks`.
- [x] Блокировать действия до сетевого запроса, если нет compliance acknowledgement,
  credentials, schema или brand mapping.
- [ ] Проверить весь workflow через UI на одном, затем трёх реальных брендах.

**Gate:** пользователь управляет реальным parser только через UI, без ручного CLI workflow.

**Статус 2026-08-14:** runtime/UI реализованы; добавлены live zero-hit probes и обязательный
confirmation token, восстановление truncated tasks, progress/error contract и seed 21 бренда.
Ruff, strict mypy, 76 backend tests, frontend lint/typecheck, 8 tests и production build зелёные.
Live UI gate остаётся `HOLD`: `APP_LIVE_COMPLIANCE_ACKNOWLEDGED` не задан, поэтому workflow
на одном и трёх брендах намеренно не запускался.

## Фаза 5 — Bounded live scope и устойчивость

- [x] Обрабатывать выбранные бренды строго последовательно, вплоть до scope из 21 бренда.
- [x] Ограничивать сбор 500 listings на бренд и всегда показывать фактические
  `partial`/`truncated` и coverage.
- [x] Проверить pagination, coverage, lifecycle, normalization и scoring каждого бренда.
- [x] Включить SQLite WAL, `synchronous=NORMAL`, foreign keys и busy timeout 5 секунд.
- [x] Реализовать cross-process Windows lock и один Uvicorn worker.
- [x] Проверить crash/reconcile/resume во время fetching, normalizing и scoring.
- [x] Провести ограниченный live soak с контролем памяти, handles, WAL, logs и browser restarts.
- [x] Проверить backup, restore preview/apply и `PRAGMA integrity_check`.

**Gate:** выбранные бренды обрабатываются последовательно в bounded scope без тихой потери
данных, дублей и зависших ресурсов; запуск всех 21 брендов не является обязательным.

**Результат 2026-08-14:** `PASS`. Fresh discovery и auto-map подтвердили 21/21 брендов.
Live run 1 последовательно завершил 42/42 active/sold tasks: ровно 10 500 listings при
лимите 500 на бренд, 464 T1-запроса, 0 failed tasks, retries и 429; все задачи честно
`truncated`, итог `partial`, coverage 0.03517. Scoring создал 134 группы и 268 snapshots.
После run backend использовал 142 MB RAM и 305 handles; SQLite 47 MB, WAL 5 MB, log
0.2 MB. Отдельный live run 2 подтвердил исправленный итоговый статус `partial` на
500 listings за 25 запросов. Ruff, strict mypy, 80 backend tests, frontend
lint/typecheck, 8 tests и production build зелёные. Старые runtime/test/build caches и
логи удалены; освобождено около 3.1 GB.

## Фаза 6 — Windows self-hosted release

- [ ] Создать idempotent PowerShell `install`, `update`, `start`, `stop`, `status`, `rollback`
  и `uninstall`.
- [ ] Установить pinned Python/Node dependencies и Scrapling-managed Camoufox.
- [ ] Применять Alembic migrations только из install/update перед startup.
- [ ] Запускать backend/frontend только на `127.0.0.1`, backend с одним worker.
- [ ] Зарегистрировать Scheduled Tasks с restart-on-failure.
- [ ] Проверять в health точные version, revision, Alembic head и runtime readiness.
- [ ] Проверить clean install, reboot/autostart, update, failed update и rollback.

**Gate:** новый Windows-профиль устанавливает и запускает подтверждённый live parser без IDE.

## Фаза 7 — Выпуск

- [ ] Ruff, mypy, frontend lint/typecheck/build и dependency audits зелёные.
- [ ] Все source-independent проверки зелёные, но не используются вместо live acceptance.
- [ ] Повторить live discovery, T1 canary и UI run перед релизом.
- [ ] Проверить отсутствие credentials и seller PII в API, логах и diagnostics.
- [ ] Проверить backup/restore, crash recovery и Windows revision.
- [ ] Создать tag `v1.0.0` только при совпадении запущенной revision и принятого commit.

**Gate:** `SHIP` возможен только после фактически успешного live workflow. Любая неподтверждённая
работа с Grailed означает `HOLD`.
