## 16. Тестирование

| Уровень | Что | Инструмент |
|---|---|---|
| Unit | mapping (все ветки YAML-кандидатов), нормализация цены/времени/размера, condition, quality-флаги | pytest, golden-фикстуры |
| Unit | `PaginationPlanner`: property-based — при любом распределении hits план покрывает 100% диапазона без пропусков | `hypothesis` |
| Unit | RateLimiter (freezegun), CircuitBreaker, ProxyManager ротация/cooldown |  |
| Contract | схема Algolia-ответа: обязательные поля присутствуют; при добавлении новых — тест не падает, но логирует | сохранённые фикстуры |
| Integration | **fake Algolia server** (FastAPI): реализует `/query`, `/queries`, `/browse`, `/facets/*/query`, отдаёт сгенерированные данные, умеет имитировать 429/403/5xx/лимит 1000 | httpx ASGI transport |
| Integration | record/replay: реальные ответы один раз записываются в `tests/cassettes/*.json`, дальше тесты идут офлайн | собственный `RecordingTransport` |
| Integration | Camoufox smoke: поднять браузер, открыть example.com, закрыть (маркер `@pytest.mark.browser`, не в обычном CI) |  |
| E2E | полный прогон против fake-сервера: 21 бренд → БД → нормализация → скоринг → метрики | pytest + временная SQLite |
| Canary (ручной) | 1 бренд, `max_listings=50`, реальная сеть — проверка, что живой Grailed ещё совместим | CLI `python -m app.cli canary` |

**Обязательное требование:** весь CI зелёный **без сети и без браузера**. Реальные сетевые тесты — только по маркеру.

### T0 mock/replay

`data/fixtures/grailed/v1/manifest.json` фиксирует версию и seed детерминированного
каталога. `MockHttpTransport` обслуживает только `http://mock-algolia.local` через
ASGI fake Algolia; любой иной URL отклоняется до попытки сетевого подключения.

```bash
python -m app.cli seed
python -m app.cli replay
```

`seed` идемпотентно создаёт 21 бренд в SQLite. `replay` выполняет встроенный smoke
транскрипт query, multi-query, browse, facets и key introspection. Запись живых
cassette-ответов намеренно остаётся частью следующего Test Harness этапа.

---
