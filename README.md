# Grailed Liquidity Analyzer

## Prerequisites
Python 3.11.9, Node.js 20.19.5 и pnpm 9.15.9. Эти же версии заданы в
`.python-version`, `.nvmrc`, `frontend/package.json` и CI.

## Backend
cp .env.example .env                              # Windows: Copy-Item .env.example .env
cd backend
python -m venv .venv && source .venv/bin/activate # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt     # production runtime
# Для разработки и полного offline gate вместо предыдущей команды:
python -m pip install -r requirements-dev.txt

# Скачать браузерные движки (Camoufox/Firefox + зависимости Playwright).
# Вариант 1 — через Scrapling (рекомендуется, ставит всё сразу):
scrapling install
# Вариант 2 — только Camoufox:
python -m camoufox fetch

# Проверка окружения:
python -m app.cli doctor        # версии, наличие браузера, capability report

alembic upgrade head
python -m app.cli seed
python -m app.cli replay       # offline smoke-test of the bundled T0 fixtures
python -m app.cli canary --brand "Rick Owens" --limit 50

# Canary is offline and non-persistent in APP_SOURCE_MODE=mock. A live canary is
# manual-only: set APP_SOURCE_MODE=live after ToS approval and refresh discovery.
# --limit accepts values from 1 through 200.

# SQLite operations (retention is a preview unless --apply is supplied):
python -m app.cli retention
python -m app.cli retention --apply
python -m app.cli db-backup
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3
# Stop the backend first; restore makes a safety backup of the current database.
python -m app.cli db-restore data/backups/grailed-YYYYMMDDTHHMMSSZ.sqlite3 --apply

## Frontend
corepack enable
cd frontend && pnpm install --frozen-lockfile

## Run
uvicorn app.main:app --reload --port 8000     # backend
pnpm run dev                                  # frontend → http://localhost:3000

## Проверки
cd backend && ruff check app tests && mypy && pytest && python -m app.cli replay
cd frontend && pnpm run lint && pnpm run typecheck && pnpm run test && pnpm run build

# Dependency audit: high/critical findings блокируют CI.
cd backend && pip-audit -r requirements-dev.txt
cd frontend && pnpm audit --audit-level high

Обычный `pytest` полностью офлайн, исключает `browser`/`integration` и требует не менее
80% coverage для всего parser stack. Реальный Camoufox smoke запускается отдельно после
`scrapling install`:

```text
cd backend
python -m app.cli doctor
pytest -m browser --no-cov
```

Финальный порядок миграций, backup/rollback, разрешённого live canary и создания
annotated tag `v1.0.0` описан в [release runbook](docs/RUNBOOK.md). Текущий release
candidate остаётся `HOLD`, пока live canary, CI на Python 3.11/Node 20 и Git tag не
подтверждены.

Политика версий, dependency audit и временных исключений описана в
[environment runbook](docs/ENVIRONMENT.md).

## Первый запуск парсера
1. Settings → Parser → «Refresh discovery» (поднимет Camoufox один раз)
2. Brands → «Auto-map to Grailed» → подтвердить неоднозначные
3. Parser → «Dry run» → проверить бюджет → «Run»

## Live mode и ответственность пользователя

Live-доступ включается только после самостоятельной проверки применимых ToS,
`robots.txt` и местного законодательства. Пользователь отвечает за законность и
допустимость сбора данных. После такой проверки задайте одновременно
`APP_SOURCE_MODE=live` и `APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true`; без env-подтверждения
discovery, parser run и canary блокируются до любого сетевого запроса. Лимиты
жёстко ограничены 90 запросами/мин и тремя одновременными запросами.

Идентификатор продавца по умолчанию хранится как SHA-256 от нормализованного имени
и локальной соли. Режим `none` отключает хранение, а `plain` требует отдельного
подтверждения в Settings и постоянно отображается как предупреждение. Соль,
сырой seller ID, email и точная геолокация никогда не возвращаются через API.

## Разработка без сети
Settings → source_mode = mock   (или переменная APP_SOURCE_MODE=mock)

`source_mode=mock` использует встроенный ASGI fake Algolia и не делает внешних HTTP
запросов. Детерминированный набор T0 расположен в `data/fixtures/grailed/v1`: его
manifest хранит версию и seed, а сам каталог содержит по 200 active и sold листингов
для каждого из 21 бренда. `seed` идемпотентно добавляет эти 21 бренд в SQLite; `replay`
прогоняет эталонные Algolia-запросы полностью в памяти.

## Frontend этапа 10

- Интерфейс доступен на English и Русском; первый запуск — English, выбор хранится
  локально в браузере.
- Управляющие действия доступны только при `APP_SOURCE_MODE=mock|replay`. В `live`
  frontend показывает состояние, но блокирует запуск, mapping и изменение настроек.
- `GET/PATCH /api/settings` хранит только разрешённые несекретные overrides в
  `app_settings`; env остаётся источником defaults. Новые значения применяются к
  следующим запускам, активный run продолжает работать со своим snapshot.
