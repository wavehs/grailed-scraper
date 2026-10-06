## 12. Rate limiting и запуск сбора

### 12.1. Контроль скорости

```
GlobalTokenBucket(rate = requests_per_minute/60)
   └── HostSemaphore(algolia_host, max_concurrent = 3)
```
Дефолты: 90 запросов/мин, 3 одновременных запроса, multi-query до 8 подзапросов.
Если ключ сообщает `maxQueriesPerIPPerHour`, discovery снижает скорость до безопасной.
Других лимитов нет: сбор ограничивается только скоростью. `parser_max_requests_per_run`
(50 000) — страховка от бесконечной пагинации, а не бюджет.

### 12.2. Одна кнопка «Обновить данные»

`POST /api/parser/run` с `brand_ids` (или `null` для всех брендов с подтверждённым
сопоставлением). Порядок:

1. Если ключ discovery устарел, он обновляется автоматически (один GET страницы и пробы).
2. Активный индекс бренда собирается целиком; после полного прохода объявления, которых
   больше нет, проверяются (`sold`, `removed_pending`).
3. Проданные: для нового бренда — полный сбор за `sold_history_days`, для уже собранного —
   delta от watermark (`parser/incremental.py`) с перекрытием 2 часа.
4. Прогресс (`/parser/runs/{id}/progress`), отмена и продолжение работают через
   `parser_run_tasks`; курсоры сохраняются не реже раза в 2 секунды.

Dry-run, токены подтверждения и пробное зондирование бюджета удалены.

---

## 14. Персистентность и идемпотентность

### 14.1. Новые/изменённые таблицы

```
parser_runs        + mode, status, phase, budget_estimate (бренды/задачи),
                     requests_made, coverage_avg, warnings(json), stats(json)
parser_run_tasks   ← НОВАЯ: id, run_id, brand_id, index_type, bucket_spec(json),
                     cursor, status(pending|running|done|failed|skipped|truncated),
                     attempts, hits_collected, expected_hits, coverage,
                     fetch_tier, error, started_at, finished_at
parser_watermarks  ← НОВАЯ
source_credentials ← НОВАЯ (см. §4.6)
source_schema      ← НОВАЯ: source, observed_fields(json), sample_size,
                     pagination_strategy, detected_at, drift_score
brand_source_map   ← НОВАЯ (см. §11.2)
listing_price_history ← НОВАЯ
fx_rates           ← НОВАЯ: date, currency, rate_to_usd
unmatched_brands   ← НОВАЯ
schema_alerts      ← НОВАЯ
listings           + first_seen_at, last_seen_at, removed_checked_at,
                     quality_flags(json), fetch_tier ('T1'), sold_at_is_estimated,
                     price_original, currency_original, fx_rate, schema_version
```

### 14.2. Upsert

- Батчи по 200, одна транзакция на батч.
- SQLite: `INSERT ... ON CONFLICT(grailed_id) DO UPDATE SET ...` с `excluded.*`.
- **Не затирать** непустое значение пустым: `sold_at = COALESCE(excluded.sold_at, listings.sold_at)`.
- `first_seen_at` пишется только при вставке.
- WAL-режим, `synchronous=NORMAL`, индексы на `(brand_id, status, sold_at)`, `(grailed_id)`, `(status, last_seen_at)`.

### 14.3. Resume

Прогон падает → `parser_run.status='interrupted'`. Кнопка «Resume»: берутся `parser_run_tasks` со статусом `pending|running|failed`, курсоры восстанавливаются, добор продолжается. Повторная запись безопасна благодаря upsert.

### 14.4. Персональные данные

`seller_username` — псевдоним, но всё же идентификатор. Хранение: настройка `store_seller_identity` (дефолт `hashed`) — сохраняем `sha256(username + local_salt)` для дедупа/репостов и **не** храним сам username. Режим `plain` — только если пользователь явно включил. `seller.id`, email, геолокация точнее страны — не сохраняем никогда.

### 14.5. Retention raw data

Нормализованные записи и история цен не удаляются. `raw_json` очищается для
листингов, которые не наблюдались 90 дней; backup-файлы хранятся 30 дней. Очистка
запускается только вручную и по умолчанию показывает preview:

```text
python -m app.cli retention
python -m app.cli retention --apply
```

После очистки `raw_json={}`, а время фиксируется в `raw_json_purged_at`.

### 14.6. SQLite backup и restore

```text
python -m app.cli db-backup
python -m app.cli market-rebuild
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3 --apply
```

Backup использует SQLite online backup API и завершается только после успешного
`PRAGMA integrity_check`; destination ограничен каталогом `data/backups`. Restore
без `--apply` только проверяет источник. Для применения backend должен быть
остановлен; перед заменой текущей БД автоматически создаётся и проверяется
страховочная копия. Восстановленная БД повторно проходит integrity check.
`market-rebuild` также сначала создаёт проверенный backup, затем пересобирает
identity текущего run и сохраняет snapshots текущей версии скоринга.

---
