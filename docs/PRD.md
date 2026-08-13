# PRD — Grailed Liquidity Analyzer

## Назначение

Проект собирает данные активных и проданных листингов Grailed, приводит их к единой модели и передаёт их в слои нормализации, скоринга и аналитики. Канонические продуктовые и технические требования разнесены по тематическим документам этого каталога.

## Границы

- Источник MVP: Grailed через Algolia, browser-mediated и DOM fallback tiers.
- Разработка и CI должны работать в mock/replay режиме без сети и браузера.
- Пагинация обязана сообщать coverage и не может молча терять записи.
- Секреты, персональные данные продавцов и антибот-ограничения обрабатываются по правилам compliance.

## Навигация

- Архитектура и tiers: [PARSING.md](PARSING.md), [BROWSER_FALLBACKS.md](BROWSER_FALLBACKS.md).
- Discovery и Algolia: [DISCOVERY.md](DISCOVERY.md), [ALGOLIA.md](ALGOLIA.md), [PAGINATION.md](PAGINATION.md).
- Данные: [DATA_MODEL.md](DATA_MODEL.md), [LIFECYCLE.md](LIFECYCLE.md), [BRAND_MAPPING.md](BRAND_MAPPING.md).
- Эксплуатация: [OPERATIONS.md](OPERATIONS.md), [OBSERVABILITY.md](OBSERVABILITY.md), [TESTING.md](TESTING.md).
- Приёмка: [TASKS.md](TASKS.md), [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md).
