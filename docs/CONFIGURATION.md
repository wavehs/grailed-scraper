## Конфигурация

Настройки читаются из корневого `.env` (префикс `APP_`, см. `.env.example`) и класса
`Settings` в `backend/app/core/config.py`. Через интерфейс («Настройки») меняются только
скорость, глубина истории продаж, ценовой диапазон и режим хранения продавца;
переопределения сохраняются в `app_settings` и проверяются тем же классом `Settings`.

### Редактируемые в интерфейсе

| Ключ | Default | Описание |
|---|---|---|
| `requests_per_minute` | 90 | жёсткий максимум 90 |
| `max_concurrent_requests` | 3 | жёсткий максимум 3 |
| `sold_history_days` | 365 | проданные объявления старше не собираются (фильтр `sold_at_i`); активные собираются все |
| `collect_price_min_usd` / `collect_price_max_usd` | null | необязательный ценовой диапазон для обоих индексов |
| `store_seller_identity` | `hashed` | `none` \| `hashed` \| `plain`; `plain` требует флага подтверждения в PATCH |
| `live_compliance_acknowledged` | `false` | одноразовая галочка на странице «Сбор данных»; без неё live-запросы заблокированы |

### Только env (константы приложения)

| Ключ | Default | Описание |
|---|---|---|
| `environment` | `development` | `development` \| `test` \| `production` |
| `revision` | auto | env → `data/release.json` → Git commit; `unknown` запрещён в production. `data/release.json` — локальный файл для установки без Git, в репозиторий не коммитится |
| `backend_bind_host` / `frontend_bind_host` | `127.0.0.1` | в production только loopback |
| `cors_origins` | `127.0.0.1:3000`, `localhost:3000` | в production только loopback-origin |
| `source_mode` | `live` | `live` only |
| `database_url` | `data/grailed.db` | SQLite |
| `data_directory` / `log_directory` | `data/`, `data/logs/` | здесь же `app.lock`; тесты подменяют на временную папку |
| `algolia_hits_per_page` | 1000 | ограничивается `max_hits_per_page` из discovery (максимум Algolia 1000) |
| `algolia_multiquery_batch_size` | 8 | 1–8 |
| `algolia_pagination_strategy` | `auto` | `auto` \| `browse` \| `keyset` \| `range_split` |
| `parser_request_timeout_s` | 15 | |
| `parser_max_retries` | 3 | повторы только в `algolia/client.py`; транспорт сам не повторяет |
| `parser_max_concurrency` | 1 | один worker: бренды и индексы последовательно |
| `parser_max_requests_per_run` | 50000 | страховка от «убежавшей» пагинации, не бюджет сбора |
| `parser_progress_interval_s` | 2 | heartbeat не реже раза в 2 секунды |
| `parser_removed_confirm_hours` | 48 | `removed_pending` → `removed` |
| `parser_watermark_overlap_hours` | 2 | overlap delta-watermark |
| `parser_refresh_active_limit` | null | только для bounded canary |
| `discovery_ttl_hours` | 12 | нижняя граница; `validUntil` ключа имеет приоритет |
| `discovery_sample_size` | 200 | объём выборки для схемы |
| `quality_price_outlier_mad_k` | 6 | |
| `quality_filter_replicas` | true | |
| `quality_lot_price_multiplier` | 1.5 | |
| `fx_provider` | `static` | локальная `fx_rates` |
| `seller_identity_salt` | generated | секрет из env или `data/secrets/`; не доступен через API |
| `raw_data_retention_days` | 90 | применяется явной CLI-командой |
| `backup_retention_days` | 30 | применяется явной CLI-командой |
| `sqlite_busy_timeout_ms` | 5000 | |

Соль никогда не отдаётся наружу. Источник данных, словари типов и моделей описаны в
`config/sources/grailed.yaml`, `config/taxonomy.yaml`, `config/grouping.yaml` и
`config/models/*.yaml`.
