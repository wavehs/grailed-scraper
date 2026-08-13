## 9. Модель данных и маппинг

### 9.1. Декларативный маппинг — `config/sources/grailed.yaml`

```yaml
source: grailed
indices:
  active: Listing_production
  active_sorted_by_date: Listing_by_date_added_production
  sold: Listing_sold_production
facets:
  brand: designers.name
  category: category_path
pagination:
  strategy: auto          # auto | browse | keyset | range_split
  key_attr_sold: sold_at
  key_attr_active: created_at
  hits_per_page: 200
fields:
  grailed_id:      ["id", "objectID"]
  title:           ["title"]
  description:     ["description"]
  brand_name:      ["designers[0].name", "designer_names[0]"]
  brand_slug:      ["designers[0].slug"]
  category:        ["category", "category_path"]
  subcategory:     ["subcategory", "category_path_root"]
  size:            ["size"]
  condition:       ["condition"]
  price_cents:     ["price_i"]
  price_float:     ["price"]
  currency:        ["currency", "_default:USD"]
  likes_count:     ["hearts_count", "hearts", "_default:0"]
  created_at:      ["created_at", "created_at_i"]
  sold_at:         ["sold_at", "sold_at_i"]
  updated_at:      ["updated_at", "updated_at_i"]
  cover_photo_url: ["cover_photo.url", "photos[0].url"]
  photo_urls:      ["photos[*].url"]
  seller_id:       ["seller.id"]
  seller_username: ["seller.username"]
  sticker:         ["sticker", "sticker.type"]
  location:        ["location", "seller.country"]
conditions:
  is_new: "New/Never worn"
  is_gently_used: "Gently used"
  is_used: "Used"
  is_worn: "Worn"
  is_not_specified: "Not specified"
```

Маппер идёт по списку кандидатов слева направо, первый непустой выигрывает. Это делает парсер устойчивым к переименованиям полей: правится YAML, не код.

### 9.2. `ListingData` (pydantic v2)

```
source: str = "grailed"
grailed_id: int
status: Literal["active","sold","removed"]
url: str
title: str
description: str | None
brand_name_raw: str
brand_slug: str | None
brand_id: int | None            # заполняется нормализацией
category: str | None
subcategory: str | None
size_raw: str | None
size_normalized: str | None     # XS..XXL / числовые / OS
condition_raw: str | None
condition: str | None
price: Decimal                  # всегда в USD
price_original: Decimal | None
currency_original: str
fx_rate: Decimal | None
sold_price: Decimal | None
likes_count: int = 0
created_at: datetime | None
sold_at: datetime | None
updated_at: datetime | None
first_seen_at: datetime         # когда МЫ впервые увидели
last_seen_at: datetime
days_on_market: int | None
cover_photo_url: str | None
photo_urls: list[str] = []
photo_count: int = 0
seller_id: int | None
seller_username_hash: str | None   # см. §14
seller_country: str | None
quality_flags: list[str] = []      # ["outlier_price","possible_replica","lot",...]
fetch_tier: Literal["T0","T1","T2","T3"]
parser_run_id: int
raw_json: dict
schema_version: int
```

### 9.3. Нормализации

**Цена.** Приоритет `price_i / 100` (целые центы, без float-погрешности), иначе `Decimal(str(price))`. Всё считается в `Decimal`, **никогда в float**. Валюта ≠ USD → конверсия по курсу на `sold_at` (кэш курсов, таблица `fx_rates`, дневная гранулярность); `fx_rate` сохраняется, чтобы результат был воспроизводим.

**Время.** Unix seconds vs milliseconds автоопределяются: `> 10^11` → ms. Всё в UTC, timezone-aware. Отсутствует `created_at` → `first_seen_at`.

**Sold без `sold_at`.** Fallback-цепочка: `sold_at → updated_at → last_seen_at`. Поле `sold_at_is_estimated=true` → такой листинг **исключается из расчёта `days_to_sell`**, но участвует в объёме продаж.

**Размер.** Таблица нормализации по категориям (tops: XS/S/M/L/XL; bottoms: waist 28–40; footwear: US/EU/UK/JP → US). Не распознан → `size_normalized=None`, `size_raw` сохраняется.

**Бренд.** `brand_name_raw` → matching через `brand_source_map` (точное совпадение) → aliases → fuzzy (rapidfuzz, порог 92) → `unmatched` + запись в `unmatched_brands` для ручного разбора.

### 9.4. Data Quality (`quality.py`)

Отсекаем мусор **до** скоринга, но не удаляем — помечаем флагами:

| Флаг | Правило | Влияние |
|---|---|---|
| `invalid` | нет id / title / price ≤ 0 / пустые designers | не сохраняется |
| `price_outlier` | цена вне [median × 0.05, median × 20] в группе (бренд+категория) по MAD | исключается из медиан/скоринга |
| `possible_replica` | ключевые слова в title/description: `rep`, `replica`, `inspired`, `1:1`, `unauthorized`, `dhgate` | исключается |
| `lot_or_bundle` | `bundle`, `lot of`, `x2`, `set of`, `read description` + цена ≫ | исключается из цен |
| `repost` | тот же `seller_id` + fuzzy title ≥ 95 + Δцена < 10% + Δвремя < 30д | схлопывается в один «товар» для volume-метрик |
| `wrong_brand` | бренд в title не совпадает с `designers[0]` | warning |
| `no_photos` | `photo_count == 0` | понижает confidence |

Все пороги — в `app_settings`, чтобы крутить без релиза.

Реализация этапа 7 возвращает из нормализатора `NormalizationResult`: либо строгий
`ListingData`, либо список безопасных причин отклонения. Невалидный hit не пишется
в `listings`; остальные флаги сохраняются в `quality_flags`, чтобы последующие
версии scoring могли исключать или понижать данные без потери исходного `raw_json`.
Перед persistence из `raw_json` рекурсивно удаляются поля `username` и
`seller_username`; seller username в открытом виде не сохраняется.

Курсы валют в MVP берутся только из локальной `fx_rates` за точную календарную
дату. Если курс отсутствует, hit получает причину `missing_fx_rate` и не вызывает
скрытый сетевой запрос.

---
