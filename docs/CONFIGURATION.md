## 17. Конфигурация (полный список)

| Ключ | Default | Описание |
|---|---|---|
| `environment` | `development` | `development` \| `test` \| `production` |
| `revision` | auto | env → `data/release.json` → Git commit; `unknown` запрещён в production |
| `backend_bind_host` / `frontend_bind_host` | `127.0.0.1` | в production разрешены только loopback-адреса |
| `cors_origins` | `["http://127.0.0.1:3000"]` | в production список должен точно совпадать с default |
| `source_mode` | `live` | `live` only |
| `fetch_tier_preferred` | `T1` | стартовый tier |
| `fetch_tier_allow_browser` | `true` | разрешена ли эскалация на T2 |
| `fetch_tier_allow_dom` | `true` | разрешён ли T3 |
| `algolia_app_id` / `algolia_api_key` | null | кэш (маскируются в API-ответах) |
| `algolia_agent` | null | перехваченная строка агента |
| `algolia_active_index` / `algolia_sold_index` | auto | |
| `algolia_brand_facet` | `designers.name` | auto-detect |
| `algolia_hits_per_page` | 200 | ограничивается `maxHitsPerQuery` |
| `algolia_multiquery_batch_size` | 8 | |
| `algolia_pagination_strategy` | `auto` | |
| `algolia_attributes_mode` | `full` | `full` \| `lean` |
| `credentials_ttl_hours` | 12 | нижняя граница; `validUntil` имеет приоритет |
| `parser_mode` | `delta` | `delta` \| `full` |
| `parser_full_refresh_days` | 7 | |
| `parser_request_delay_ms` | 400 | |
| `requests_per_minute` | 90 | жёсткий максимум 90 |
| `max_concurrent_requests` | 3 | жёсткий максимум 3 на host |
| `parser_max_concurrency` | 1 | один worker: бренды и индексы обрабатываются последовательно |
| `parser_progress_interval_s` | 2 | heartbeat/progress для polling API |
| `parser_request_timeout_s` | 15 | |
| `parser_max_retries` | 3 | |
| `parser_max_requests_per_run` | 800 | защита от «убежавшего» прогона; включает adaptive range probes |
| `parser_max_items_per_brand` | 500 | общий bounded-лимит active + sold на бренд; превышение явно даёт `truncated` |
| `parser_refresh_active_limit` | null | ограничение active listings для bounded canary; полный run не задаёт |
| `parser_default_window_days` | 90 | |
| scoring windows | `30, 90` | фиксированы моделью `opportunity-v1` |
| scoring sample target | `20 sold + 20 active` | влияет на confidence, не скрывает score |
| `parser_default_max_per_brand` | 1500 | поднято: keyset снимает старое ограничение |
| `parser_refresh_active_enabled` | true | |
| `parser_removed_confirm_hours` | 48 | |
| `parser_enrich_top_n` | 0 | обогащение со страниц листингов |
| `browser_headless` | true | |
| `browser_humanize` | true | |
| `browser_geoip` | true | |
| `browser_block_images` | true | |
| `browser_solve_challenges` | true | |
| `browser_max_pages` | 2 | вкладок в пуле |
| `browser_restart_every_requests` | 300 | |
| `proxy_enabled` | false | |
| `proxy_list_browser` / `proxy_list_http` | [] | |
| `proxy_rotation_mode` | `weighted` | `round_robin` \| `random` \| `weighted` |
| `proxy_allow_direct_fallback` | true | |
| `quality_price_outlier_mad_k` | 6 | |
| `quality_filter_replicas` | true | |
| `quality_lot_price_multiplier` | 1.5 | минимальное отношение к медиане для lot/bundle |
| `identity_image_requests_per_run` | 100 | максимум cover-image запросов после текстового blocking; 0 отключает |
| `gemini_api_key` | null | только `APP_GEMINI_API_KEY`; UI показывает лишь наличие ключа |
| `parser_watermark_overlap_hours` | 2 | overlap delta-watermark |
| `store_seller_identity` | `hashed` | `none` \| `hashed` \| `plain` |
| `seller_identity_salt` | generated | секрет из env или `data/secrets/`; не доступен через API |
| `live_compliance_acknowledged` | `false` | только env; обязателен для всех live entry points |
| `raw_data_retention_days` | 90 | срок raw JSON; применяется явной CLI-командой |
| `backup_retention_days` | 30 | срок backup-файлов; применяется явной CLI-командой |
| `sqlite_busy_timeout_ms` | 5000 | ожидание SQLite write lock перед ошибкой |
| `fx_provider` | `static` | локальная `fx_rates`; внешний provider оставлен post-MVP |

Через UI редактируется только безопасное подмножество. Соль и live acknowledgement
доступны исключительно через env и не отдаются наружу. `plain` требует отдельного
флага подтверждения в PATCH, который не сохраняется.

Для env-файла ключи этапа fetching имеют префикс `APP_`:
`APP_FETCH_TIER_ALLOW_DOM`, `APP_ALGOLIA_HITS_PER_PAGE`,
`APP_ALGOLIA_MULTIQUERY_BATCH_SIZE`, `APP_ALGOLIA_PAGINATION_STRATEGY` и
`APP_ALGOLIA_ATTRIBUTES_MODE`. Размер multi-query валидируется в диапазоне 1–8;
дефолтные общие лимиты остаются 90 запросов/мин и 3 одновременных запроса.

---
