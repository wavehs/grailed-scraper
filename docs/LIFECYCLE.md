## 10. Инкрементальность и жизненный цикл листинга

После сбора затронутые бренды перегруппировываются ([GROUPING.md](GROUPING.md)),
затем пересчитываются метрики. Перевыставление того же продавца связывается с первым
объявлением через `listings.relist_of_id`.

### 10.1. Watermarks

Таблица `parser_watermarks(source, brand_id, index_type, last_key_value, last_run_at, full_refresh_at)`.

| Режим | Что делает | Когда |
|---|---|---|
| `delta` (дефолт) | sold: `numericFilters: sold_at_i > watermark − overlap`; active-индекс **всегда** собирается целиком | каждый прогон |
| `full` | игнорирует watermark, полный обход окна | по флажку «Полный прогон» / API; по воскресеньям — планировщик (см. ниже) |
| `refresh_active` | перепроверка ранее собранных active по их `grailed_id` (батчами через `filters: objectID:X OR objectID:Y ...`, до 100 за запрос) | отдельный режим прогона, только вручную |

Так как active-индекс собирается полностью в каждом прогоне, отсутствующие в нём
active-объявления переводятся в `removed_pending` без отдельного `refresh_active`.
Регулярные прогоны (и недельный full) запускает планировщик `services/scheduler.py`
(выключен по умолчанию, включается в Settings); без повторных прогонов переход
`removed_pending → removed` (≥ 48 ч) не подтверждается.

`overlap` = 2 часа — страховка от гонок и правок задним числом.

Watermark продвигается только после полного, непрерванного и нетранкированного
scope. При partial/truncated старое значение сохраняется, чтобы следующий прогон
не создал тихий пропуск. `refresh_active` отправляет не более 100 `objectID` в
одном фильтре и проверяет отсутствующие active ID в sold-индексе до перехода в
`removed_pending`.

### 10.2. Переходы статусов

```
не в БД + в active-индексе                → INSERT status=active, first_seen_at=now
в БД active + снова в active              → UPDATE price, likes, last_seen_at (+ price_history если цена изменилась; при вставке — базовая строка)
в БД active + появился в sold-индексе     → UPDATE status=sold, sold_at, sold_price,
                                             days_to_sell = sold_at − created_at
не в БД + сразу в sold                    → INSERT status=sold (days_to_sell из created_at)
в БД active + НЕ найден при refresh_active→ status=removed_pending, removed_checked_at=now
removed_pending + через 48ч нет в sold    → status=removed (снят с продажи, НЕ продан)
removed_pending + нашёлся в sold          → status=sold
```

Это критично для скоринга: **исчезнувший листинг ≠ проданный**. Смешивание этих кейсов завышает ликвидность. Отдельная метрика `removal_rate` (доля снятых без продажи) — полезный сигнал сама по себе.

### 10.3. История цен

Таблица `listing_price_history(listing_id, price, observed_at, source_run_id)` — пишется при первой вставке листинга (база истории) и при каждом изменении цены. Цены до первого наблюдения — в `price_initial`/`price_drop_count` (из `price_drops`). Даёт: скорость уценки, эластичность спроса, «сколько скинули до продажи» (`discount_to_sale`).

---
