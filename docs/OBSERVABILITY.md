## 15. Наблюдаемость

### 15.1. Структурные логи

JSON-строки пишутся одновременно в stdout и `data/logs/parser.jsonl`; события
уровня error дополнительно попадают в `data/logs/errors.log`. Оба файла ротируются
по схеме 10 МБ × 5. Обязательные поля: `ts, level, request_id, run_id, task_id,
source, brand, index, tier, event, duration_ms, msg`; неприменимые поля равны `null`.

`X-Request-ID` принимается только в безопасном формате из 1–128 символов, иначе
генерируется UUID. Контекст фоновых worker-задач привязывает `run_id/task_id`
независимо для каждой asyncio-задачи. Централизованный redactor обрабатывает
вложенные структуры, строки, URL, query parameters, заголовки и exception details.
API-ключи и credentials маскируются как `abc1****`; bearer/basic credentials,
пароли URL и неизвестные поля с `token/secret/password/key` скрываются полностью.

| Event | Level | Назначение |
|---|---:|---|
| `request_complete` | info | HTTP method/path/status/latency и request ID |
| `parser_run_started` | info | старт resumable run |
| `parser_run_completed` | info | terminal outcome run |
| `parser_run_failed` | error | необработанная ошибка run без секретных details |
| `parser_task_started` | info | начало task с run/task context |
| `parser_task_completed` | info | task завершена |
| `parser_task_failed` | error | изолированная ошибка task |
| `listing_normalization_rejected` | warning | invalid hit и безопасные коды причин |

### 15.2. Метрики прогона (в `parser_runs.stats`)

Snapshot хранится в `parser_runs.stats.observability` минимум раз в 2 секунды и
восстанавливается при resume. Поля: `requests_total, requests_by_tier,
http_errors_by_code, retries, rate_limit_hits, avg_latency_ms, p95_latency_ms,
cache_hits, cache_misses, cache_hit_rate, hits_fetched, listings_inserted,
listings_updated, listings_invalid, quality_flags_counts, coverage_by_brand,
browser_restarts, proxy_failures, duration_s`.

`GET /api/parser/runs/{id}/report` возвращает это же значение типизированным полем
`metrics`, сохраняя исходный `stats` для обратной совместимости.

AI-run отдельно показывает listings/unique inputs, progress, ambiguous и safe-unique,
input/output tokens, прогноз/фактическую стоимость и санитизированный код ошибки.
Provider payload и API key не сохраняются и не логируются.

### 15.3. Прогресс для UI

`GET /api/parser/runs/{id}/progress` (polling 2с) + опционально SSE `GET /api/parser/runs/{id}/stream`:
```json
{
  "status":"running","phase":"fetching","tier":"T1","degraded":false,
  "brands_total":21,"brands_completed":8,"current_brand":"Rick Owens",
  "tasks_total":64,"tasks_done":27,
  "sold_count":1840,"active_count":3110,
  "requests_made":142,"eta_seconds":210,
  "warnings":[{"brand":"Chrome Hearts","code":"partial_coverage","coverage":0.86}]
}
```
Фазы: `discovery → planning → fetching → normalizing → scoring → done`.

### 15.4. Health-эндпоинт

`GET /api/health` сохраняет базовые поля и дополнительно возвращает `version`, точный
`revision` и `environment` запущенного release.

`GET /api/parser/health` отражает живое состояние credentials, доступных tiers и
версий, schema alerts, circuit breakers по `(tier, host, proxy)`, proxy health,
активных и последнего run, compliance и последних метрик. `unavailable` означает
отсутствие обязательного ресурса или env-compliance acknowledgement; `degraded` —
stale credentials, schema drift, fallback tier, открытый circuit, plain seller mode
или деградировавший последний run. Поле `reasons` содержит машиночитаемые причины,
а `schema.alerts` — подробности активных alerts. Секция `runtime` отдельно сообщает
Alembic current/head, доступность data/log directories, production bind validation и
состояние single-instance lock. Production startup не применяет миграции и завершается
до запуска API/parser runtime при неизвестной revision или несовпадении Alembic head.

---
