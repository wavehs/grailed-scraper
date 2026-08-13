## 6. Pagination Planner — ядро парсера

### 6.1. Проблема

Algolia ограничивает глубину: `page × hitsPerPage ≤ paginationLimitedTo` (обычно 1000). Наивное деление по фиксированным ценовым бакетам (v1) **не гарантирует полноту**: у Chrome Hearts в бакете «$200–500» легко больше 1000 sold за 90 дней → часть данных теряется молча, а скоринг ликвидности получает смещённую выборку. Это критично: молчаливая потеря данных хуже явной ошибки.

### 6.2. Три стратегии, в порядке приоритета

#### Стратегия A — **Browse** (если `acl` содержит `browse`)
Полный обход без лимита 1000. Курсорная пагинация, до 1000 hits за вызов, релевантность игнорируется (нам не нужна).
```
POST /1/indexes/{idx}/browse  {"params": "<filters>", "cursor": "<prev>"}
→ пока в ответе есть "cursor"
```
Это идеальный путь. Проверяется один раз в discovery.

#### Стратегия B — **Keyset (seek) pagination** по отсортированной реплике
Требует: индекс-реплику, отсортированную по числовому атрибуту (`created_at` для active, `sold_at`/`created_at` для sold, `price_i` как запасной ключ).

```
key_attr = "sold_at"  (desc-реплика)
cursor   = +inf
loop:
    q = filters(brand) AND numericFilters=[f"{key_attr} < {cursor}"]
    page 0..(limit/hitsPerPage - 1), собираем hits
    if получено < paginationLimit:  → диапазон исчерпан, brand done
    else:
        cursor = min(key_attr) среди полученных hits
        # tie-break: если у >1 hit одинаковый key_attr на границе —
        # ставим cursor = boundary_value + 1 и добираем ровно этот
        # boundary отдельным запросом с numericFilters key_attr = boundary
        # (там почти всегда < 1000 записей); дедуп по objectID
```

Свойства: полнота гарантирована, монотонный прогресс, естественная точка checkpoint (`cursor` сохраняем в `parser_run_tasks`), **идеально ложится на инкрементальный режим** (нижняя граница = watermark прошлого прогона).

#### Стратегия C — **Adaptive recursive range split** (если нет подходящей реплики)
```
def plan(range_lo, range_hi, attr, depth=0):
    n = probe_nbHits(attr in [lo, hi))        # hitsPerPage=0 → дёшево
    if n == 0: return []
    if n <= pagination_limit or depth >= MAX_DEPTH or (hi-lo) <= MIN_WIDTH:
        return [Bucket(lo, hi, expected=n)]
    mid = split_point(lo, hi, attr)   # для price — геометрическая середина,
                                      # для времени — арифметическая
    return plan(lo, mid, depth+1) + plan(mid, hi, depth+1)
```
Пробы делаются пачкой через multi-query (8 диапазонов за 1 HTTP-запрос) — планирование стоит 2–4 запроса на бренд.
Оси деления, по порядку: `sold_at`/`created_at` → `price_i` → `category` → `size`.
Если после MAX_DEPTH бакет всё ещё > лимита — задача помечается `truncated=true`, и это **явно попадает в отчёт прогона и в `confidence` бренда**. Никакой тихой потери.

### 6.3. Выбор стратегии

```
if key.acl contains "browse":            → A
elif sorted replica exists for key_attr: → B
else:                                    → C
```
Решение принимается в discovery и сохраняется в `source_schema.pagination_strategy`.

### 6.4. Дедупликация на лету

Внутри одного `FetchTask` — `set[objectID]`. Между задачами — БД (upsert по `grailed_id`). Перекрытия на границах бакетов/курсоров нормальны и ожидаемы (закладываем overlap 1 единица ключа, чтобы не потерять пограничные записи).

### 6.5. Проверка полноты (Coverage Check)

После сбора бренда:
```
expected = nbHits первого планировочного запроса (без разбиения)
collected = уникальных objectID
coverage = collected / max(expected, 1)
```
- `coverage ≥ 0.98` → `complete`
- `0.7 ≤ coverage < 0.98` → `partial` (warning в отчёт, флаг в метриках)
- `< 0.7` → `poor` (бренд помечен, confidence снижен, в UI — предупреждение)

Coverage сохраняется в `parser_run_tasks` и агрегируется в `brand_metrics.data_coverage`.

### 6.6. Реализованный контракт этапа 6

- `PaginationPlanner.fetch()` возвращает одноразовый async-итератор; итоговый
  `CoverageReport` доступен после его полного завершения.
- Логические ключи берутся из `config/sources/grailed.yaml` упорядоченными
  списками: сначала timestamp, затем стабильный числовой `id/objectID` и `price_i`.
- Плотная группа с одинаковым timestamp не выбирается одним equality-запросом:
  она рекурсивно делится по вторичному ключу, поэтому группа более 1000 записей
  не обрезается лимитом Algolia.
- Пустой диапазон получает `skipped` и исключается из средней coverage. Для
  непустых диапазонов статусы: `complete` при coverage ≥ 0.98, `partial` при
  0.70–0.98 и `poor` ниже 0.70. Неразделимый диапазон всегда `truncated`.
- `parser_run_tasks` хранит cursor, counts, coverage и tier; агрегат
  `parser_runs` хранит `coverage_avg`, `degraded_mode` и предупреждения.

---
