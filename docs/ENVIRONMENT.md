# Воспроизводимое окружение и dependency audit

## Runtime contract

Целевые версии: Python `3.11.9`, Node.js `20.19.5`, pnpm `9.15.9`. Python runtime
зависимости находятся в `backend/requirements.txt`, dev/test инструменты — в
`backend/requirements-dev.txt`. Все прямые Python-зависимости закреплены точными
версиями проверенного окружения. HTTP-клиент — `curl_cffi` (TLS-отпечаток Chrome);
браузеры, Scrapling, Playwright и Camoufox не используются. APScheduler не входит в runtime.

Чистая установка обязана использовать `python -m pip install`, `corepack` и
`pnpm install --frozen-lockfile`. Глобальные Python/Node-пакеты не считаются частью
окружения.

## Security gate

CI запускает `pip-audit -r backend/requirements-dev.txt` и
`pnpm audit --audit-level high`. Любая high/critical уязвимость останавливает gate.
Текущее состояние не содержит audit-исключений.

Временное исключение допустимо только отдельным изменением CI вместе с записью в
этом документе: advisory/CVE, затронутая версия, обоснование применимости, владелец,
компенсирующая мера и обязательная дата пересмотра. Исключение без даты пересмотра
запрещено.

## Compatibility debt

Открытые предупреждения после gate 2026-08-13:

- `StarletteDeprecationWarning` из `fastapi.testclient`: установленный FastAPI
  предлагает переход тестового клиента с `httpx` на `httpx2`. `httpx` нужен только
  тестам. Пересмотреть при следующем обновлении FastAPI/Starlette.

Закрыто в Фазе 0: Alembic получил `path_separator=os`; Vite обновлён до безопасной
ветки через pnpm override; `next lint` заменён прямым вызовом ESLint.
