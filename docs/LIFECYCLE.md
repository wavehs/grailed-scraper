## 10. Инкрементальность и жизненный цикл листинга

### 10.1. Watermarks

Таблица `parser_watermarks(source, brand_id, index_type, last_key_value, last_run_at, full_refresh_at)`.

| Режим | Что делает | Когда |
|---|---|---|
| `delta` (дефолт) | `numericFilters: key_attr > watermark − overlap` | каждый прогон |
| `full` | игнорирует watermark, полный обход окна | раз в 7 дней или по кнопке |
| `refresh_active` | перепроверка ранее собранных active по их `grailed_id` (батчами через `filters: objectID:X OR objectID:Y ...`, до 100 за запрос) | каждый прогон |

`overlap` = 2 часа — страховка от гонок и правок задним числом.

Watermark продвигается только после полного, непрерванного и нетранкированного
scope. При partial/truncated старое значение сохраняется, чтобы следующий прогон
не создал тихий пропуск. `refresh_active` отправляет не более 100 `objectID` в
одном фильтре и проверяет отсутствующие active ID в sold-индексе до перехода в
`removed_pending`.

### 10.2. Переходы статусов

```
не в БД + в active-индексе                → INSERT status=active, first_seen_at=now
в БД active + снова в active              → UPDATE price, likes, last_seen_at (+ price_history если цена изменилась)
в БД active + появился в sold-индексе     → UPDATE status=sold, sold_at, sold_price,
                                             days_to_sell = sold_at − created_at
не в БД + сразу в sold                    → INSERT status=sold (days_to_sell из created_at)
в БД active + НЕ найден при refresh_active→ status=removed_pending, removed_checked_at=now
removed_pending + через 48ч нет в sold    → status=removed (снят с продажи, НЕ продан)
removed_pending + нашёлся в sold          → status=sold
```

Это критично для скоринга: **исчезнувший листинг ≠ проданный**. Смешивание этих кейсов завышает ликвидность. Отдельная метрика `removal_rate` (доля снятых без продажи) — полезный сигнал сама по себе.

### 10.3. История цен

Таблица `listing_price_history(listing_id, price, observed_at, source_run_id)` — пишется только при изменении цены. Даёт: скорость уценки, эластичность спроса, «сколько скинули до продажи» (`discount_to_sale`).

---
