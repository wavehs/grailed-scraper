## 7. Tier 2 — Browser-mediated Algolia

Два механизма, оба через Camoufox под Scrapling.

### 7.1. In-page fetch (предпочтительный)

Запрос выполняется **из контекста страницы grailed.com** — те же cookies, тот же origin, тот же TLS, никаких расхождений отпечатка.

```python
async def action(page):
    result = await page.evaluate("""async (p) => {
        const r = await fetch(p.url, {
            method: 'POST',
            headers: p.headers,
            body: JSON.stringify(p.body),
            credentials: 'omit'
        });
        return { status: r.status, text: await r.text() };
    }""", payload)
    return result
```
Одна открытая вкладка обслуживает десятки запросов — браузер поднимается один раз на прогон, а не на запрос.

### 7.2. Passive interception (запасной)

`page.on("response")` + навигация по реальным URL поиска Grailed (`/designers/{slug}`, `/shop?...&sort=...`, `?page=N`). Собираем JSON-ответы Algolia, которые фронтенд запрашивает сам. Медленно, но выглядит как обычный пользователь.

Перехваченный ответ принимается только для ожидаемого Algolia index/path. Если
browser API предоставляет POST body исходного request, дополнительно сверяется
стабильный fingerprint тела. Несовпадение считается неудачей T2, а не данными
текущей задачи.

### 7.3. Пул сессий

`AsyncStealthySession(max_pages=N)` — N вкладок (дефолт 2, максимум 4). Один браузер на прогон, вкладки переиспользуются. Обязательно: `block_images=True`, `disable_resources` только для T2-fetch (для DOM-режима ресурсы нужны частично), `humanize=True`, `geoip=True` при включённом прокси.

Жизненный цикл: сессия закрывается по завершении прогона, по таймауту простоя (5 мин) или при `memory_rss > threshold`. Утечки браузера — частая причина падений долгих прогонов, поэтому: **hard restart браузера каждые K=300 запросов или 20 минут.**

---

## 8. Tier 3 — DOM fallback на Scrapling

### 8.1. Порядок попыток на странице

1. **Embedded JSON** — самое надёжное: `script#__NEXT_DATA__`, `window.__PRELOADED_STATE__`, `application/ld+json`.
   ```python
   data = page.css_first('script#__NEXT_DATA__::text').json()
   ```
2. **Adaptive CSS-селекторы** со Scrapling:
   ```python
   cards = page.css('div[data-testid="listing-item"]', auto_save=True, adaptive=True)
   ```
   `auto_save=True` при первом успешном матче сохраняет «слепок» элемента; при смене вёрстки `adaptive=True` находит наиболее похожий элемент → парсер не падает от рефакторинга фронтенда.
3. **`find_similar()`** — от одной найденной карточки получить все остальные, даже если классы рандомизированы.
4. **Текстовый поиск/regex** — `page.find_by_regex(r'\$[\d,]+')` для цены как последний рубеж.

Перед любой T3-навигацией проверяется `robots.txt`; правила кэшируются на 24 часа.
Ошибка проверки не трактуется как разрешение. Результат T3 — тот же сырой
`RawHit`, что у T1/T2; YAML-преобразование в `ListingData` выполняется следующим
слоем и не дублируется в DOM-коде.

### 8.2. Обогащение (не только fallback)

Страница листинга даёт то, чего нет в Algolia: полные измерения (`measurements`), полное описание, все фото в высоком разрешении, дата последнего обновления цены, счётчик просмотров. Это **опциональный шаг обогащения** для топ-N листингов (например, для тех, что попали в «Market Opportunities»), а не для всех — включается флагом `enrich_top_n`.

### 8.3. Schema drift alarm

Если T3 отработал, но >30% полей пустые, либо `schema_sampler` зафиксировал исчезновение обязательного поля — создаётся запись в `schema_alerts` и показывается баннер в UI: «Структура источника изменилась, проверьте маппинг».

---
