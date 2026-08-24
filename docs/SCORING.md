# Scoring specification

## 1. Версии и снимки

Текущие версии: `identity-v5` для группировки и `market-v5` для оценки рынка.
Для каждого parser run сохраняются неизменяемые snapshots на окнах 30 и 90 дней;
`parser_run.started_at` является `as_of`. Повторный расчёт одной версии допустим
только при совпадении canonical `input_digest`.

## 2. Группировка объявлений

`IdentityResolver` является единственным владельцем назначений. Scoring использует
только готовые `listing_model_assignments` текущей версии и останавливается при
пропуске. Ключ линейки имеет вид `line-v5:<brand>:<family>:<anchor>`.

Нормализация удаляет бренд, размер, цвет, состояние и рекламные слова, приводит
синонимы и формы множественного числа к одному токену и сохраняет отличительные
части модели. Поэтому `Dagger Pendant`, `Dagger Charm` и `Dagger Necklace`
объединяются, а `Double Dagger`, `Dagger Dog Tag` и `Dagger #5` остаются отдельно.

Внутри одного бренда и source product family наблюдаемый короткий корень
присоединяет расширенные названия по включению токенов. Fuzzy-проверка с порогом
90 применяется только к словам длиной от пяти символов и всегда напрямую к
корню, без транзитивных цепочек. `identity_matches` хранит только автоматический
журнал распознавания физических перевыставлений.

## 3. Выборки

Для окна `W` относительно `as_of`:

- sold: `status=sold`, `sold_at ∈ [as_of-W, as_of]`;
- current active: все `status=active`, опубликованные не позднее `as_of`, без
  нижней границы окна;
- `sell_through = sold_count / (sold_count + current_active_count)`;
- `median_days_to_sell` и `median_sold_likes` используют только продажи с точным
  `sold_at` и `days_on_market`;
- `median_sold_price = median(sold_price ?? price)` и не влияет на оценки.

Набор групп и current active count обязаны совпадать в 30- и 90-дневных snapshots,
а `sold_30 <= sold_90`. Нарушение любого инварианта останавливает расчёт.

Для той же очищенной выборки snapshot хранит отдельные рейтинги цветов и размеров:
`sold_count`, `active_count`, `sell_through`. Они сортируются по продажам, затем
по sell-through; неизвестные значения находятся в конце.

## 4. Market-v5

Оценки используют только число продаж, скорость продажи, likes проданных вещей и
текущее число активных объявлений. Все компоненты ограничены диапазоном 0–100:

```text
monthly_sales = sold_count × 30 / W
frequency     = 100 × monthly_sales / (monthly_sales + 3)
velocity      = 100 × 30 / (30 + median_days_to_sell)
sell_through  = 100 × sold_count / (sold_count + current_active_count)
likes         = 100 × median_sold_likes / (median_sold_likes + 20)
volume_cap    = min(100, 100 × monthly_sales / 3)

liquidity = min(volume_cap,
  0.50 × frequency + 0.30 × velocity + 0.15 × sell_through + 0.05 × likes)

demand = min(volume_cap,
  0.40 × frequency + 0.30 × likes + 0.20 × sell_through + 0.10 × velocity)
```

`market_opportunity_score` временно хранит то же значение, что `demand_score`,
для совместимости старых клиентов. Новое имя в API/UI — «Спрос».

## 5. Минимум данных

- меньше трёх продаж в окне: `insufficient_sales`, числовые liquidity/demand равны
  `NULL`;
- три и более продаж, но меньше трёх точных days-to-sell:
  `insufficient_temporal_data`, числовые оценки также `NULL`;
- одна продажа никогда не получает высокую ликвидность: даже до порога её
  `volume_cap` был бы не выше 33.33 для 30 дней и 11.11 для 90 дней.

Confidence публикуется отдельно как показатель полноты данных и не меняет demand
или liquidity.

## 6. Data quality

`possible_replica` полностью исключается. Подтверждённый physical item учитывается
один раз. `price_outlier` и `lot_or_bundle` исключаются только из справочной цены.
Estimated `sold_at` учитывается в sold volume, но не в velocity/likes. Incomplete
coverage, degraded mode, truncation и `wrong_brand` остаются видимыми в warnings и
confidence.

## 7. Analytics API

- `GET /api/analytics/dashboard?window_days=30|90&search=...&brand_id=...&product_type=...`
  применяет поиск, бренд и тип товара (`footwear`, `clothing`, `accessories`) в БД
  до ranking/pagination;
- `GET /api/analytics/model-groups/{id}` принимает `window_days` и `run_id`;
- переход из dashboard передаёт выбранные окно и run;
- без `run_id` выбирается последний completed/partial run, содержащий обе пары
  snapshots 30/90 текущей версии.
