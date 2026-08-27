## 12. Rate limiting, конкурентность, бюджет

### 12.1. Многоуровневый контроль

```
GlobalTokenBucket(rate = requests_per_minute/60, burst = 5)
   └── HostSemaphore(algolia_host, max_concurrent = 3)
        └── PerTaskDelay(base = request_delay_ms) + jitter(±30%)
```
Дефолты транспорта: `request_delay_ms=400`, `max_requests_per_minute=90`, `max_concurrency=3`, `multiquery_batch=8`.
Parser использует один worker и обрабатывает бренды последовательно. Один run берёт не
более 500 listings на бренд суммарно по active/sold; превышение всегда отражается как
`truncated` с фактическим coverage.

Автоподстройка: если `key.maxQueriesPerIPPerHour` известен → `max_requests_per_minute = min(setting, limit/60 × 0.5)`.

Адаптивный троттлинг: p95 latency > 2с или доля 429 > 1% → RPS × 0.6 на 5 минут; 5 минут чисто → RPS × 1.2 (до потолка). Классический AIMD.

### 12.2. Бюджет прогона (pre-flight)

Перед стартом планировщик считает и показывает в UI:
```
Бренды: 21 | Индексы: 2 | Оценка запросов: ~310 | Оценка времени: ~6 мин
Оценка новых листингов: ~4 200 | Режим: delta | Tier: T1 | Прокси: off
```
Если оценка > `max_requests_per_run` (дефолт 5000) — предупреждение и требование
подтверждения. Тот же лимит проверяется перед каждым фактическим T1 transport call,
включая retries: после исчерпания следующий сетевой запрос не отправляется.

Gemini grouping имеет отдельный ручной preflight: 10 000 canary items, первые 100 как
schema gate, максимум `$0.50`; remaining запускается отдельной кнопкой, а совокупный
исторический лимит равен `$5.00`. Оценка резервирует максимальный structured output;
input оценивается безопасной верхней границей по UTF-8 bytes. Следующий provider job
не создаётся, если он может превысить Decimal-лимит; неизвестная стоимость резервируется
и блокирует дальнейшую отправку до ручного разрешения.

### 12.3. Dry-run

Флаг `dry_run=true`: выполняется discovery + планирование + пробы `hitsPerPage=0`, но **ничего не пишется в БД**. Возвращает план и оценку. Обязателен как способ безопасно проверить конфиг.

---

## 13. Proxy Manager v2

```
proxy_pools:
  browser: [...]    # для Camoufox — предпочтительно резидентные
  http:    [...]    # для Algolia — датацентр ок
  (если один список — используется для обоих)
```

| Возможность | Описание |
|---|---|
| Форматы | `http://`, `https://`, `socks5://`, с/без `user:pass@` |
| Health-scoring | у каждого прокси: `success_rate`, `p95_latency`, `last_error_at`, `cooldown_until` |
| Выбор | взвешенный random по score; прокси с 3 подряд ошибками → cooldown 10 мин |
| Sticky | одна `SourceSession` (браузер + HTTP) живёт на **одном** прокси весь прогон бренда — не прыгать гео посреди сессии |
| Гео-консистентность | `geoip=True` в Camoufox выставляет locale/timezone/WebRTC по IP прокси; для HTTP берём тот же `Accept-Language` |
| Валидация | кнопка «Test proxies» → параллельная проверка через `https://api.ipify.org` + пробный Algolia-запрос; таблица результатов в UI |
| Деградация | все прокси мертвы → если `allow_direct_fallback=true`, идём напрямую с предупреждением, иначе прогон `failed` |

---

## 14. Персистентность и идемпотентность

### 14.1. Новые/изменённые таблицы

```
parser_runs        + mode, dry_run, degraded_mode, tier_used, budget_estimate,
                     requests_made, coverage_avg, warnings(json)
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
                     quality_flags(json), fetch_tier, sold_at_is_estimated,
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
