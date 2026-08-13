# План полной реализации Grailed Liquidity Analyzer v1

Этот файл — новый последовательный master-plan доведения текущего проекта до стабильного
self-hosted релиза `v1.0.0`. Целевая конфигурация: один пользователь, Windows native,
SQLite, один backend-процесс и доступ только через `127.0.0.1`.

Канонические продуктовые и технические требования находятся в [docs/INDEX.md](docs/INDEX.md).
При расхождении этого плана с тематическим документом сначала обновляется тематический
документ, затем код и этот checklist.

## Границы v1

В обязательный объём входят только Grailed, текущая аналитика, ручное управление parser runs,
Windows self-hosted установка и стабильная локальная эксплуатация.

В `v1.0.0` не входят:

- дополнительные marketplace-источники;
- Grailed GraphQL enrichment;
- автоматический scheduler parser runs;
- CSV/Parquet export;
- WebSocket/SSE вместо существующего polling;
- SaaS, аккаунты, multi-user isolation и публикация приложения в интернет;
- Docker как обязательный способ установки.

## Правила выполнения

- Фазы выполняются по порядку. Следующая фаза начинается только после прохождения gate текущей.
- Каждый пункт закрывается тестом, автоматической проверкой или записанным ручным сценарием.
- Обычный test suite всегда работает без сети и браузера; browser и live проверки запускаются
  отдельными командами.
- Live-доступ требует отдельного разрешения пользователя и выполнения checklist из
  [docs/COMPLIANCE.md](docs/COMPLIANCE.md).
- Camoufox не импортируется вне `browser/`; Scrapling не импортируется вне разрешённых
  transport/browser/DOM модулей.
- Деньги проходят через `Decimal`; листинги upsert-ятся по `grailed_id`; неполнота всегда
  отражается через coverage, `partial` и `truncated`.
- API-ключи, proxy credentials, salt и запрещённые seller data не попадают в API, логи,
  fixtures, backup diagnostics или сообщения об ошибках.
- Release verdict может быть только `SHIP` или `HOLD`. Наличие собранного приложения или
  успешной команды deploy само по себе не означает готовый релиз.

## Проверенный baseline

Следующая основа уже реализована и подтверждена локальным release-candidate gate:

- [x] FastAPI backend, Next.js frontend, async SQLAlchemy, Alembic и SQLite.
- [x] Mock/replay среда для 21 бренда без внешних сетевых запросов.
- [x] Grailed discovery, Algolia T1, browser-mediated T2 и DOM T3.
- [x] Полная пагинация browse/keyset/range split с coverage reporting.
- [x] Нормализация, quality flags, lifecycle, watermarks и batch upsert по `grailed_id`.
- [x] Durable parser runs: progress, cancellation, checkpoints, reconcile и resume.
- [x] Versioned scoring, аналитические API, dashboard, brands, runs, rules и settings UI.
- [x] Маскирование секретов, privacy policy, JSON logs, health и backup/restore команды.
- [x] Полный mock e2e: 21 бренд, 42 задачи, 8 400 уникальных листингов и scoring snapshots.
- [x] 125 offline backend-тестов; coverage согласованного parser stack — 85.75%.
- [x] Отдельный Scrapling/Camoufox browser smoke.
- [x] Frontend lint, typecheck, 8 тестов и production build.
- [x] Миграции применяются до Alembic head `20260813_0007`.

Baseline не является подтверждением live-совместимости Grailed или готовой Windows-установки.
Все последующие задачи остаются открытыми до нового фактического прогона.

## Фаза 0 — Репозиторий и воспроизводимое окружение

- [ ] Восстановить `.git`, зафиксировать исходный commit и добиться чистого worktree.
- [ ] Зафиксировать Python `3.11.x`, Node.js `20.x` и pnpm `9.x` в runtime-файлах,
  README и CI.
- [ ] Разделить Python runtime и dev/test зависимости на отдельные requirements-файлы.
- [ ] Закрепить прямые Python-зависимости версиями из проверенного окружения; сохранить
  `scrapling==0.4.11` и не пиновать Camoufox отдельно.
- [ ] Удалить APScheduler и другие неиспользуемые runtime-зависимости.
- [ ] Проверить backend install в новом Python 3.11 venv без глобальных пакетов.
- [ ] Проверить `pnpm install --frozen-lockfile` и production build на чистом Node.js 20.
- [ ] Добавить dependency audit для Python и npm в CI; high/critical findings блокируют release
  либо получают документированное временное исключение с датой пересмотра.
- [ ] Устранить предупреждения совместимости Starlette/httpx, Alembic, lxml и Vite либо
  занести каждое предупреждение в принятый technical-debt checklist.
- [ ] Проверить, что offline CI выполняет Ruff, mypy, pytest/coverage, replay, миграции,
  frontend lint/typecheck/tests/build на целевых версиях.

**Gate фазы 0:** чистый clone устанавливается без IDE и глобальных зависимостей, затем
полностью проходит offline CI на Python 3.11 и Node.js 20.

## Фаза 1 — Контракты production runtime

- [ ] Сверить реализацию с PRD и документами из `docs/INDEX.md`; удалить неподтверждённые
  заявления или реализовать отсутствующее поведение.
- [ ] Определять `revision` из env, установленного release metadata или Git commit; значение
  `unknown` допустимо только в development.
- [ ] Расширить `GET /api/health` полями `version`, `revision` и `environment`, сохранив
  существующие поля для обратной совместимости.
- [ ] Расширить `GET /api/parser/health` состояниями Alembic schema, data/log directories,
  single-instance lock и production runtime.
- [ ] Проверять соответствие БД Alembic head до запуска API и parser runtime.
- [ ] При отставшей или неизвестной схеме завершать production startup с понятной ошибкой;
  не применять миграции автоматически из lifespan приложения.
- [ ] Оставить применение миграций только bootstrap/update-командам из фазы 4.
- [ ] Добавить TrustedHost middleware и production-валидацию: только `localhost`,
  `127.0.0.1` и `[::1]`.
- [ ] Сузить CORS до точного frontend origin `http://127.0.0.1:3000` и не разрешать wildcard.
- [ ] Добавить production-проверку bind address: backend и frontend не слушают LAN-интерфейсы.
- [ ] Добавить тесты, подтверждающие отсутствие секретов в обоих health endpoints, OpenAPI,
  ошибках startup, CLI doctor и диагностике миграций.

### Изменения публичного API фазы 1

`GET /api/health` сохраняет текущий контракт и добавляет:

```json
{
  "version": "1.0.0",
  "revision": "<commit-or-release-id>",
  "environment": "production"
}
```

`GET /api/parser/health` дополнительно возвращает секцию `runtime` с состояниями schema,
directories и single-instance lock. Денежные поля, listing schema и существующие parser API
не меняются.

**Gate фазы 1:** запущенный production backend сообщает точную release revision, подтверждает
Alembic head и локальную готовность, а при несовместимой БД безопасно отказывается стартовать.

## Фаза 2 — SQLite и устойчивость процессов

- [ ] На каждом SQLite connection включать `PRAGMA journal_mode=WAL`.
- [ ] На каждом SQLite connection включать `PRAGMA synchronous=NORMAL`.
- [ ] На каждом SQLite connection включать `PRAGMA foreign_keys=ON`.
- [ ] Настроить и документировать `busy_timeout`, достаточный для batch upsert и progress updates.
- [ ] Добавить startup-тест, проверяющий фактические значения всех обязательных PRAGMA.
- [ ] Добавить интеграционный тест конкурентного чтения dashboard/progress во время batch upsert.
- [ ] Добавить нагрузочный тест batch upsert и heartbeat, подтверждающий отсутствие постоянных
  `database is locked` и соблюдение обновления progress не реже одного раза в две секунды.
- [ ] Реализовать эксклюзивный cross-process Windows file lock на весь срок жизни backend.
- [ ] Оставить `data/app.pid` информационным маркером; владение runtime определяется lock,
  а не существованием PID-файла.
- [ ] При попытке второго запуска завершаться с понятной ошибкой и не изменять БД/PID первого.
- [ ] Зафиксировать production-команду Uvicorn с `--workers 1` и запретить конфигурацию
  нескольких workers для SQLite/in-process runtime.
- [ ] Гарантировать закрытие browser pages, Camoufox session, HTTP transports и SQLAlchemy
  engine при штатном shutdown, cancellation и исключениях startup/run.
- [ ] Проверить reconcile/resume после принудительного завершения backend в phases fetching,
  normalizing и scoring.
- [ ] Выполнить backup, preview restore, applied restore, retention и `PRAGMA integrity_check`
  на копии реалистичной рабочей БД.

**Gate фазы 2:** второй backend не запускается; чтение UI не блокирует запись parser;
аварийный run возобновляется без потерь и дублей; рабочая и восстановленная SQLite проходят
`integrity_check`.

## Фаза 3 — Полностью рабочий live UI

- [ ] Оставить `APP_SOURCE_MODE=live` только env-настройкой: UI и database settings не могут
  включить или выключить live mode.
- [ ] Оставить `APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true` только неперсистентной env-настройкой.
- [ ] Расширить `GET /api/parser/health` секцией `actions` для `discovery`, `brand_mapping`,
  `parser_run` и `settings`.
- [ ] Для каждого action возвращать `allowed: boolean` и машиночитаемый список `reasons`.
- [ ] В mock/replay разрешать все существующие управляющие действия.
- [ ] В live без compliance acknowledgement блокировать любые обращения к Grailed до создания
  transport/browser и показывать причину `live_compliance_not_acknowledged`.
- [ ] В подтверждённом live разрешать discovery даже при отсутствующих credentials/schema.
- [ ] Разрешать brand mapping только при валидных credentials, index и brand facet.
- [ ] Разрешать dry run/parser run только при валидных credentials, schema и подтверждённых
  brand mappings выбранного scope.
- [ ] Разрешить редактирование безопасных non-secret settings в live; исключить `source_mode`
  из PATCH payload и сделать его read-only в UI.
- [ ] Заменить тексты `Live mode is locked during Stage 10`/`mock required` на причины,
  поступившие из `actions` parser health.
- [ ] Связать disabled-состояния discovery, mapping, dry run, start, cancel, resume и settings
  с соответствующим action capability, а не с `source_mode === mock|replay`.
- [ ] Добавить UI/API-тесты для mock, replay, live-unacknowledged, live-without-credentials,
  live-without-schema, live-ready и degraded состояний.
- [ ] Добавить offline e2e UI-сценарий discovery → mapping → dry run → budget confirmation →
  run → progress → report → cancel/resume.
- [ ] Провести ручной live UI smoke после разрешённого discovery, не используя CLI для
  управляющих действий.

### Изменения публичного API фазы 3

`GET /api/parser/health` добавляет обратно совместимую секцию:

```json
{
  "actions": {
    "discovery": {"allowed": true, "reasons": []},
    "brand_mapping": {"allowed": false, "reasons": ["credentials_missing"]},
    "parser_run": {"allowed": false, "reasons": ["schema_missing"]},
    "settings": {"allowed": true, "reasons": []}
  }
}
```

**Gate фазы 3:** разрешённым live-парсером можно полностью управлять через UI без CLI;
запрещённые действия блокируются до сетевого запроса и объясняются пользователю.

## Фаза 4 — Windows self-hosted упаковка

- [ ] Создать idempotent PowerShell bootstrap для проверки Windows, Python 3.11, Node.js 20,
  pnpm 9 и прав текущего пользователя.
- [ ] Создать PowerShell-команды `install`, `update`, `start`, `stop`, `status`, `rollback`
  и `uninstall` с предсказуемыми exit codes.
- [ ] Install создаёт изолированный Python venv, ставит pinned зависимости и выполняет
  `scrapling install` для совместимого Camoufox engine.
- [ ] Install выполняет `pnpm install --frozen-lockfile`, production frontend build и Alembic
  migrations после создания первоначального backup при наличии БД.
- [ ] Хранить SQLite, logs, backups, cache, PID/lock и secrets в постоянном data-каталоге,
  который не удаляется при update/uninstall без отдельного явного флага.
- [ ] Зарегистрировать Windows Scheduled Task для backend текущего пользователя с restart-on-failure.
- [ ] Зарегистрировать отдельную Windows Scheduled Task для frontend с restart-on-failure.
- [ ] Backend task запускает Uvicorn на `127.0.0.1:8000` с одним worker.
- [ ] Frontend task ждёт успешный backend health, затем запускает production Next.js на
  `127.0.0.1:3000`.
- [ ] Start/status подтверждают не только наличие процессов, но и совпадение ожидаемой
  `revision` с ответом health.
- [ ] Update останавливает процессы, создаёт и проверяет SQLite backup, обновляет код и
  зависимости, применяет миграции, запускает сервисы и проверяет revision/health.
- [ ] При неуспешном update автоматически удерживать статус `HOLD` и предлагать rollback;
  не сообщать об успешном обновлении до проверки работающей revision.
- [ ] Rollback восстанавливает предыдущую release directory; БД восстанавливается только при
  несовместимой миграции и после preview/integrity check.
- [ ] Uninstall удаляет Scheduled Tasks и runtime-файлы, но сохраняет пользовательские данные
  по умолчанию.
- [ ] Обновить README и `docs/RUNBOOK.md` для установки, обновления, диагностики и удаления
  без IDE.

**Gate фазы 4:** новый Windows-профиль устанавливает приложение одной документированной
последовательностью; после перезагрузки доступны backend health и UI на localhost; update и
rollback проверены на реальных release directories.

## Фаза 5 — Реальная проверка Grailed

- [ ] Отдельно проверить актуальные ToS Grailed, `robots.txt`, применимое законодательство
  и допустимость личной read-only аналитики.
- [ ] Зафиксировать ручное разрешение на live-проверки и не повышать лимиты выше 90 req/min
  и трёх одновременных запросов.
- [ ] Выполнить live discovery и проверить извлечение credentials, Algolia agent, indices,
  replicas, ACL, pagination limits, facets и schema sample.
- [ ] Проверить TTL/single-flight discovery и маскирование ключей во всех API/логах.
- [ ] Запустить T1 canary для `Rick Owens` с limit 50; после discovery браузер не используется.
- [ ] Провести ограниченный принудительный T2 canary и проверить валидные данные,
  `degraded_mode=true` и корректное освобождение browser resources.
- [ ] Провести разрешённый T3 canary только для robots-allowed страниц и проверить минимальный
  набор `ListingData`.
- [ ] Последовательно выполнить full run для одного бренда, затем трёх брендов, затем всех
  21 брендов; расширять scope только после зелёного отчёта предыдущего шага.
- [ ] Для каждого scope проверить coverage, `partial/truncated`, tier transitions, warnings,
  lifecycle, price history, normalization, quality flags и scoring snapshots.
- [ ] Проверить schema drift alert на контролируемом fixture/replay; не ломать live schema
  искусственным запросом.
- [ ] Выполнить повторный delta run; offline fixture gate остаётся ≤40% запросов от full,
  live ratio только фиксируется в отчёте из-за изменяемости источника.
- [ ] Проверить health, dashboard и logs после каждого live run.
- [ ] Немедленно остановить live-проверку при CAPTCHA, запрете robots/ToS, повторяющихся 429,
  явном запрете автоматизации или невозможности соблюдать лимиты.
- [ ] Не обходить CAPTCHA вручную и не добавлять Selenium/Puppeteer/undetected-chromedriver.

**Gate фазы 5:** все 21 бренда обрабатываются только разрешёнными read-only механизмами;
неполнота и деградация явно видны в run report; ключи и seller PII не раскрываются.

## Фаза 6 — Soak, recovery и эксплуатация

- [ ] Выполнить 20 последовательных offline full/delta циклов с одной БД без дубликатов,
  необработанных исключений и нарушения lifecycle.
- [ ] Выполнить fault-injection для 401, 403, 429, 5xx, WAF HTML, timeout, slow response,
  dead proxy, pagination truncation и schema drift.
- [ ] Проверить circuit breaker, retry/backoff, host rotation, proxy cooldown и безопасную
  деградацию T1 → T2 → T3 для каждой fault-ситуации.
- [ ] Принудительно завершить backend в phases fetching, normalizing и scoring; Windows task
  восстанавливает сервис, reconcile отмечает run interrupted, resume завершает без дублей.
- [ ] Принудительно завершить Camoufox во время discovery/T2; не должно оставаться zombie
  browser processes или зависшего application lock.
- [ ] Провести 24-часовой soak установленного приложения: минимум один full и три delta runs
  с промежуточными рестартами frontend/backend.
- [ ] Во время soak контролировать memory/handle growth, SQLite size/WAL checkpoint, log size,
  browser restarts, open circuits, proxy failures и progress heartbeat.
- [ ] Проверить ротацию логов `10 MB × 5`, raw-data retention и backup retention.
- [ ] Провести полный restore drill из backup, созданного во время soak, и повторно выполнить
  health, integrity check и mock replay.
- [ ] Проверить `/api/health`, `/api/parser/health`, dashboard warnings и release revision после
  каждого рестарта и восстановления.
- [ ] Сохранить итоговый soak/fault report без credentials и персональных данных.

**Gate фазы 6:** после 24 часов и fault-injection приложение доступно на localhost, SQLite
проходит integrity check, зависших процессов/locks нет, незавершённые runs восстанавливаются.

## Фаза 7 — Финальная приёмка и выпуск v1.0.0

- [ ] На чистом Python 3.11 выполнить Ruff и mypy без ошибок.
- [ ] Выполнить полный offline pytest; все тесты зелёные, coverage согласованного parser stack
  не ниже 80%.
- [ ] На Node.js 20/pnpm 9 выполнить frontend lint, typecheck, tests и production build.
- [ ] Отдельно выполнить Scrapling/Camoufox browser smoke на Python 3.11.
- [ ] Проверить mock e2e для 21 бренда, coverage >5 000 sold, T1/T2/T3 contracts,
  cancellation/resume, crash recovery и delta economy.
- [ ] Пройти чистую Windows-установку, reboot/autostart, update и rollback на отдельном профиле.
- [ ] Закрыть обновлённый [docs/DEFINITION_OF_DONE.md](docs/DEFINITION_OF_DONE.md).
- [ ] Закрыть security/privacy/compliance checklist и проверить отсутствие high/critical
  dependency findings без принятого исключения.
- [ ] Проверить backup/restore, retention, health и 24-hour soak report.
- [ ] Убедиться, что Git worktree чист, CI зелёный и changelog описывает фактический релиз.
- [ ] Сверить intended commit, `app.__version__`, OpenAPI version, `/api/health.revision` и
  запущенную Windows installation revision.
- [ ] Создать annotated tag `v1.0.0` только после полного совпадения release revision.
- [ ] Проверить tag через `git show --verify --stat v1.0.0`; публикация tag выполняется только
  отдельным осознанным действием владельца.

**Gate фазы 7:** verdict `SHIP` допустим только при закрытых фазах 0–7 и совпадении реально
запущенной revision с `v1.0.0`. Любой незакрытый пункт означает `HOLD` с указанной причиной.

## Итоговая Definition of Done v1

Grailed Liquidity Analyzer v1 считается полностью реализованным, когда одновременно верно:

1. Приложение воспроизводимо устанавливается на чистый Windows-профиль и автоматически
   восстанавливается после перезагрузки.
2. Backend/frontend доступны только через localhost; второй backend instance не запускается.
3. SQLite работает с обязательными PRAGMA, выдерживает parser/UI concurrency и проходит
   backup/restore integrity drill.
4. Mock/replay CI полностью офлайн и зелёный на Python 3.11/Node.js 20.
5. Разрешённый live workflow полностью выполняется через UI: discovery → mapping → dry run →
   run → report → cancel/resume.
6. T1/T2/T3, coverage, partial/truncated, lifecycle, scoring и schema alerts подтверждены.
7. Crash recovery и 24-hour soak не оставляют дублей, zombie browsers или зависших locks.
8. Секреты и запрещённые seller data отсутствуют в API, логах, diagnostics и fixtures.
9. Health сообщает version/revision/runtime/schema, а запущенная revision совпадает с tag.
10. Release gate выдал `SHIP`; до этого момента проект остаётся release candidate со статусом
    `HOLD`.
