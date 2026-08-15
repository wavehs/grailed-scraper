# Scoring specification

## 1. Версия и жизненный цикл

Первая утверждённая модель называется `opportunity-v1`. Скоринг запускается
parser orchestrator после fetching и normalization и до финального статуса run.
Для каждого run сохраняются неизменяемые snapshots на окнах 30 и 90 дней.
`parser_run.started_at` является `as_of`; повторный расчёт того же run допустим
только при совпадении canonical `input_digest`.

Snapshots хранят версию, окно, исходные counts, компоненты, confidence factors,
quality summary, warnings и digest. Старые runs автоматически не пересчитываются.

## 2. Model groups

Rule-based группа принадлежит одному бренду. Сопоставление выполняется по title
после casefold, Unicode NFKD и удаления диакритики:

- все непустые `include_keywords` должны присутствовать;
- ни один `exclude_keywords` не должен присутствовать;
- optional category должна совпасть точно после той же нормализации;
- при нескольких правилах выигрывает правило с большим числом include-фраз,
  затем правило с меньшим `id`.

Листинг без совпавшего правила попадает в стабильную fallback-группу
`brand/category`; пустая category становится `Uncategorized`.

## 3. Выборки и исходные метрики

Для окна `W` относительно `as_of`:

- sold: `status=sold`, `sold_at ∈ [as_of-W, as_of]`;
- active: `status=active`, а `created_at` (fallback `first_seen_at`) попадает в окно;
- `sell_through = sold_count / (sold_count + active_count)`;
- `median_sold_price = median(sold_price ?? price)`;
- `median_days_to_sell` использует только точные `sold_at` и `days_on_market`;
- `median_sold_likes_per_day = median(likes_count / max(days_on_market, 1))`
  по той же точной sold-выборке.

Деньги остаются `Decimal`; scores округляются `ROUND_HALF_UP` до двух знаков.
API сериализует деньги целым количеством центов.

## 4. Компоненты opportunity-v1

Все компоненты ограничены диапазоном 0–100.

| Компонент | Формула | Вес |
|---|---|---:|
| Sell-through | `100 × sell_through` | 0.40 |
| Velocity | `100 × (1 − min(median_days_to_sell / W, 1))` | 0.25 |
| Likes/day | midpoint percentile среди групп бренда | 0.20 |
| Price affordability | обратный midpoint percentile median price среди групп бренда | 0.15 |

При единственном известном значении percentile равен 50; отсутствующее значение
получает 0.

```text
market_opportunity_score =
    0.40 × sell_through_score
  + 0.25 × velocity_score
  + 0.20 × likes_per_day_score
  + 0.15 × price_affordability_score

liquidity_score =
  (40 × sell_through_score + 25 × velocity_score) / 65
```

Confidence публикуется отдельно и не изменяет opportunity score.

## 5. Data confidence

```text
sample = 100 × average(min(sold/20, 1), min(active/20, 1))
confidence = 0.40 × sample
           + 0.35 × coverage
           + 0.15 × quality
           + 0.10 × temporal completeness
```

- `coverage` берётся из задач бренда текущего run;
- `quality` — доля пригодных записей с четвертью штрафа за `no_photos`;
- temporal completeness — доля sold с точным days-to-sell;
- degraded run умножает confidence на `0.90`;
- truncated scope ограничивает confidence максимумом `69`;
- малая выборка не скрывает результат, а добавляет `low_sample` warning.

## 6. Quality policy

| Флаг/связь | Поведение scoring v2 |
|---|---|
| `possible_replica` | полностью исключить |
| подтверждённый `physical_item` | учитывать один раз; приоритет sold, затем active, затем последняя запись |
| `price_outlier` | исключить только из price-компонента |
| `lot_or_bundle` | исключить только из price-компонента |
| estimated `sold_at` | учитывать в sold volume и price, исключить из velocity/likes-day |
| `wrong_brand` | учитывать и добавить warning |
| `no_photos` | учитывать, но дать штраф 0.25 записи в quality confidence |

Incomplete coverage, degraded mode и truncation всегда остаются видимыми в
snapshot/API и не маскируются формулой.

## 7. Analytics API

- `GET /api/analytics/dashboard` и `/model-groups` — рейтинг групп;
- `GET /api/analytics/model-groups/{id}` — snapshot, breakdown и примеры;
- `GET /api/analytics/brands` и `/{id}` — агрегаты бренда;
- `GET /api/analytics/listings/{id}` и `/{id}/price-history` — листинг и цены;
- `GET/POST/PATCH/DELETE /api/model-rules` — правила model groups.

Поддерживаются `window_days=30|90` и optional `run_id`. Без `run_id` выбирается
последний completed/partial run с подходящими snapshots.
