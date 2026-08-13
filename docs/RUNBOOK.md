# Runbook выпуска v1.0.0

## Статус

Текущий вердикт: **HOLD**. Offline release candidate подготовлен, но выпуск нельзя
объявлять завершённым до разрешённого live canary, зелёного CI и создания тега в
настоящем Git-репозитории.

## 1. Pre-flight

1. Использовать Python 3.11 и Node.js 20; установить зависимости из lock/requirements.
2. Убедиться, что `git status --short` пуст и текущий commit является кандидатом на релиз.
3. Проверить `.env`: секреты не закоммичены, лимиты не выше 90 req/min и 3 concurrent.
4. Сделать SQLite backup и проверить его читаемость:

   ```powershell
   cd backend
   python -m app.cli db-backup
   ```

5. Применить и проверить миграции:

   ```powershell
   alembic upgrade head
   alembic current
   ```

## 2. Offline gate

```powershell
cd backend
ruff check app tests
mypy
pytest
python -m app.cli replay
python -m app.cli doctor

cd ../frontend
pnpm install --frozen-lockfile
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
```

Обычный `pytest` исключает browser/integration маркеры и применяет порог 80% ко всему
parser stack: parser, Grailed sources, transport, normalization и scoring.

## 3. Browser gate

После `scrapling install` запустить отдельно:

```powershell
cd backend
python -m app.cli doctor
pytest -m browser --no-cov
```

Smoke должен открыть `https://example.com` через `BrowserSessionPool` и корректно закрыть
страницу и Scrapling-managed Camoufox session. Отсутствующий/неисправный движок означает
`HOLD`, а не пропуск проверки.

## 4. Разрешённый live canary

Этот шаг выполняется вручную только после актуальной проверки ToS, `robots.txt` и
применимого законодательства. Не повышать лимиты и не обходить CAPTCHA.

```powershell
$env:APP_SOURCE_MODE='live'
$env:APP_LIVE_COMPLIANCE_ACKNOWLEDGED='true'
cd backend
python -m app.cli doctor
# Сначала refresh discovery через API/UI.
python -m app.cli canary --brand "Rick Owens" --limit 50
```

Canary принимается, если возвращает валидные данные через T1 без браузера после discovery,
ключи не появляются в выводе/логах, а `/api/parser/health` остаётся зелёным.

## 5. Запуск и проверка revision

```powershell
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверить `/api/health`, `/api/parser/health` и `/openapi.json`; поле `info.version` должно
быть `1.0.0`. Успешный старт сам по себе не доказывает, что запущен нужный commit — сравнить
его с release candidate в Git/CI.

## 6. Tag

Только после всех предыдущих пунктов:

```powershell
git tag -a v1.0.0 -m "Grailed Liquidity Analyzer v1.0.0"
git show --verify --stat v1.0.0
git push origin v1.0.0
```

Не создавать и не отправлять тег при `HOLD`.

## Rollback

1. Остановить backend.
2. Переключить приложение на предыдущий проверенный tag/commit.
3. Если данные несовместимы, сначала выполнить preview, затем восстановить созданный backup:

   ```powershell
   python -m app.cli db-restore data/backups/<backup>.sqlite3
   python -m app.cli db-restore data/backups/<backup>.sqlite3 --apply
   ```

4. Запустить backend и повторно проверить health, OpenAPI version и логи первых запросов.
