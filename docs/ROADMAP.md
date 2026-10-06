## 19. Roadmap

1. **Browse-режим** — активируется автоматически, если ключ имеет ACL (уже заложено).
2. **Grailed GraphQL** — обогащение (измерения, история цены, offers). Отдельный `SourceEnricher`.
3. **Мульти-источники** через `SourceAdapter`: eBay (Browse/Marketplace Insights API — легально и с sold-данными), Depop, Vinted, Yahoo! Auctions / Mercari (через прокси-сервисы), Rakuten. Каждый возвращает тот же `ListingData`.
4. **Планировщик** (APScheduler): ночной delta-прогон + недельный full.
5. **WebSocket-прогресс** вместо polling.
6. **Экспорт** датасета (CSV/Parquet) и снапшоты метрик.

