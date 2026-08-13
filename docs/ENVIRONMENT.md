# Воспроизводимое окружение и dependency audit

## Runtime contract

Целевые версии: Python `3.11.9`, Node.js `20.19.5`, pnpm `9.15.9`. Python runtime
зависимости находятся в `backend/requirements.txt`, dev/test инструменты — в
`backend/requirements-dev.txt`. Все прямые Python-зависимости закреплены точными
версиями проверенного окружения от 2026-08-13. `scrapling[fetchers]==0.4.11`
управляет совместимыми версиями Camoufox, Playwright и curl_cffi; отдельный pin
Camoufox запрещён. APScheduler не входит в runtime v1.

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
  предлагает переход тестового клиента с `httpx` на `httpx2`, тогда как production
  fallback проекта по контракту использует `httpx[socks]`. Пересмотреть при следующем
  согласованном обновлении FastAPI/Starlette, не позднее 2026-10-01.
- `lxml` предупреждает о неработающем `strip_cdata`; вызов находится внутри
  `scrapling.Selector`. Пересмотреть вместе со следующим pin Scrapling, не позднее
  2026-10-01; обход в коде приложения нарушил бы границы toolchain.

Закрыто в Фазе 0: Alembic получил `path_separator=os`; Vite обновлён до безопасной
ветки через pnpm override; `next lint` заменён прямым вызовом ESLint.
