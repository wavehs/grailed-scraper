# Changelog

Все заметные изменения проекта документируются в этом файле. Формат основан на
Keep a Changelog, версии следуют Semantic Versioning.

## [1.0.0] — release candidate, HOLD

### Добавлено

- Четырёхуровневый mock/HTTP/browser/DOM parser pipeline для 21 бренда.
- Возобновляемые parser runs, coverage reporting, lifecycle и versioned scoring.
- Dashboard, управление брендами, parser runs, scoring rules и settings UI.
- Offline acceptance harness, отдельный Camoufox smoke и релизный runbook.

### Изменено

- Версия backend и OpenAPI синхронизирована на `1.0.0`.
- CI проверяет Python 3.11, offline parser coverage ≥80%, replay, миграции и полный
  frontend gate на Node.js 20.
- PID lifecycle безопасно переживает временную блокировку файла на Windows.

### Статус выпуска

Тег `v1.0.0` ещё не создан. Gate остаётся `HOLD`, пока не выполнены live canary,
зелёный CI на поддерживаемых версиях и проверка чистого Git worktree.
