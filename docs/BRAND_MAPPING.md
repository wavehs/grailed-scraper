## 11. Автоматическое сопоставление брендов

v1 требовал ручного заполнения `grailed_designer_name`. v2 делает это автоматически.

### 11.1. Алгоритм

```
для каждого бренда B из нашего списка:
    candidates = searchForFacetValues(index=active, facet=brand_facet,
                                      facetQuery=B.name, maxFacetHits=20)
    # candidates = [{value: "Rick Owens", count: 12043}, ...]
    score(c) = 0.7 * fuzzy_ratio(normalize(B.name), normalize(c.value))
             + 0.3 * log_scale(c.count)
    best = argmax score
    if score(best) >= 0.95:  автоматически принять
    elif score(best) >= 0.75: предложить в UI на подтверждение
    else: пометить unresolved
```
`normalize` — lowercase, unicode NFKD (снимает диакритику: Déprimés → deprimes), удаление пунктуации.

### 11.2. Таблица `brand_source_map`

```
brand_id, source, source_designer_name, source_slug, source_designer_id,
listings_count, match_score, match_method ("auto"|"manual"|"seed"), verified, updated_at
```
Один бренд может иметь **несколько** записей (Grailed часто дублирует: «Bape» и «A Bathing Ape», «Comme des Garcons» / «Comme des Garçons» / «CDG Homme Plus»). Собираем по **всем** маппингам бренда, объединяя `facetFilters: [["designers.name:A", "designers.name:B"]]` (внутри массива — OR).

Это важно: у Grailed **сабрбренды отдельные** — `Comme des Garcons Homme Plus`, `Comme des Garcons Play`, `Rick Owens DRKSHDW`, `Maison Margiela MM6`. Решение: в UI бренда — чекбокс «включать сабрбренды», при включении в маппинг добавляются все фасет-значения с префиксом.

### 11.3. Обновлённые seed-данные

```
name                     | grailed_designer_name(s)                                  | aliases
Chrome Hearts            | Chrome Hearts                                             | CH
Enfants Riches Déprimés  | Enfants Riches Deprimes                                   | ERD
Rick Owens               | Rick Owens; Rick Owens DRKSHDW (opt)                      | RO, DRKSHDW
Raf Simons               | Raf Simons                                                | 
Undercover               | Undercover                                                | UC
Number (N)ine            | Number (N)ine                                             | Number Nine, N(N)
Vetements                | Vetements                                                 | VTMNTS
Balenciaga               | Balenciaga                                                | Bala
Vivienne Westwood        | Vivienne Westwood                                         | VW
Yohji Yamamoto           | Yohji Yamamoto; Yohji Yamamoto Pour Homme (opt)           | Yohji, Y's
Comme des Garçons        | Comme des Garcons; CDG Homme Plus (opt); CDG Play (opt)   | CDG
Stone Island             | Stone Island; Stone Island Shadow Project (opt)           | SI, SISP
Arc'teryx                | Arc'teryx                                                 | Arcteryx
Arc'teryx Veilance       | Arc'teryx Veilance                                        | Veilance
Kapital                  | Kapital; Kapital Kountry (opt)                            | 
Visvim                   | Visvim                                                    | 
Carol Christian Poell    | Carol Christian Poell                                     | CCP
Maison Margiela          | Maison Margiela; MM6 Maison Margiela (opt)                | Margiela, MMM
Bape                     | A Bathing Ape                                             | Bape, BAPE, AAPE
Hysteric Glamour         | Hysteric Glamour                                          | HG
Jean Paul Gaultier       | Jean Paul Gaultier                                        | JPG, Gaultier
```
**Seed — только стартовая гипотеза.** При первом запуске discovery проверяет каждое значение через `searchForFacetValues` и корректирует (в т.ч. диакритику: у Grailed часто `Enfants Riches Deprimes` без акцентов).

### 11.4. API подтверждения

- `GET /api/brands` возвращает mappings, score, состояние и число листингов.
- `POST /api/brands/auto-map` обрабатывает все бренды или переданные `brand_ids`.
- `PATCH /api/brands/{id}` меняет aliases и `include_subbrands`.
- `PATCH /api/brands/{id}/mappings/{mapping_id}` с `confirm|reject` фиксирует
  ручное решение; отклонённый кандидат не предлагается повторно автоматически.

Frontend показывает состояния `verified`, `review`, `unresolved`, не получает
Algolia credentials и общается только с backend API.

---
