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

Действующие исключения (`pnpm.auditConfig.ignoreGhsas` в `frontend/package.json`):

| Advisory | Версия | Обоснование | Владелец | Компенсирующая мера | Пересмотр |
|---|---|---|---|---|---|
| GHSA-vfj7-8cjw-p6xm (`braces`, DoS на глубоко вложенных шаблонах) | 3.0.3 — последняя, исправления нет | Только сборка и lint: `tailwindcss` и `@next/eslint-plugin-next` разбирают шаблоны из конфигурации репозитория, не пользовательский ввод; в статический экспорт не попадает | владелец репозитория | шаблоны глобов задаются только в `tailwind.config.ts` и ESLint-конфиге | 2027-01-06 |

Транзитивные исправления закреплены через `pnpm.overrides` (`brace-expansion`, `js-yaml`,
`sharp`, `source-map-js`, `vite`); `next` 15.5.27, `vitest` 4.1.11.

Временное исключение допустимо только отдельным изменением CI вместе с записью в
этом документе: advisory/CVE, затронутая версия, обоснование применимости, владелец,
компенсирующая мера и обязательная дата пересмотра. Исключение без даты пересмотра
запрещено.

## Обновления зависимостей

Dependabot (`.github/dependabot.yml`) раз в неделю открывает сгруппированные PR с minor/patch
обновлениями `backend/requirements*.txt` и `frontend/package.json`; major-обновления приходят
отдельными PR, GitHub Actions обновляются раз в месяц. Каждый такой PR проходит тот же CI,
включая `pip-audit` и `pnpm audit`. `curl_cffi` исключён: это транспорт к Grailed, и его
обновление меняет parser path, поэтому делается вручную вместе с live gate из `TESTING.md`.
Закреплённые точные версии и `pnpm.overrides` сохраняются: Dependabot меняет пин, а не
снимает его.

## Compatibility debt

Открытые предупреждения после gate 2026-08-13:

- `StarletteDeprecationWarning` из `fastapi.testclient`: установленный FastAPI
  предлагает переход тестового клиента с `httpx` на `httpx2`. `httpx` нужен только
  тестам. Пересмотреть при следующем обновлении FastAPI/Starlette.

Закрыто в Фазе 0: Alembic получил `path_separator=os`; Vite обновлён до безопасной
ветки через pnpm override; `next lint` заменён прямым вызовом ESLint.
