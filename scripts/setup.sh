#!/usr/bin/env bash
# Grailed Liquidity Analyzer - macOS setup.
#   scripts/setup.sh [--force] [--skip-build] [--skip-doctor] [--acknowledge-compliance]
# Idempotent: an existing backend/.venv and .env are kept unless --force is given.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
PYTHON_VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/.python-version")"
PNPM_VERSION="9.15.9"

FORCE=0
SKIP_BUILD=0
SKIP_DOCTOR=0
ACKNOWLEDGE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --skip-doctor) SKIP_DOCTOR=1 ;;
        --acknowledge-compliance) ACKNOWLEDGE=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\033[36m>> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[WARN] %s\033[0m\n' "$1"; }
fail() { printf '\033[31m[ERROR] %s\033[0m\n' "$1" >&2; exit 1; }

step "Проверка инструментов"
HAS_UV=0
if command -v uv >/dev/null 2>&1; then
    HAS_UV=1
elif ! command -v "python${PYTHON_VERSION%.*}" >/dev/null 2>&1; then
    fail "Нужен uv или python${PYTHON_VERSION%.*}: brew install uv"
fi
command -v node >/dev/null 2>&1 || fail "Нужен Node.js 20+: brew install node@20"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 20 ] || fail "Нужен Node.js 20+, найден $(node -v)"
if ! command -v pnpm >/dev/null 2>&1; then
    command -v corepack >/dev/null 2>&1 || fail "Нужен pnpm $PNPM_VERSION: brew install pnpm"
    corepack enable
    corepack prepare "pnpm@$PNPM_VERSION" --activate
fi

step "Каталоги data/"
mkdir -p "$ROOT_DIR"/data/{logs,cache,backups,secrets}

step "Файл .env"
ENV_FILE="$ROOT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    echo "Создан .env из .env.example."
fi
if grep -q '^APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false' "$ENV_FILE" || ! grep -q '^APP_LIVE_COMPLIANCE_ACKNOWLEDGED=' "$ENV_FILE"; then
    if [ "$ACKNOWLEDGE" -eq 0 ] && [ -t 0 ]; then
        echo "Для работы с живым источником Grailed требуется согласие с ToS и правилами доступа."
        read -r -p "Подтвердить соблюдение комплаенса и включить реальный парсинг? (y/N) " reply || reply=""
        case "$reply" in [yYдД]*) ACKNOWLEDGE=1 ;; esac
    fi
    if [ "$ACKNOWLEDGE" -eq 1 ]; then
        if grep -q '^APP_LIVE_COMPLIANCE_ACKNOWLEDGED=' "$ENV_FILE"; then
            sed -i '' 's/^APP_LIVE_COMPLIANCE_ACKNOWLEDGED=.*/APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true/' "$ENV_FILE"
        else
            echo "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true" >> "$ENV_FILE"
        fi
        echo "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true"
    fi
fi

step "Python $PYTHON_VERSION (backend/.venv)"
if [ "$FORCE" -eq 1 ] || [ ! -x "$VENV_PYTHON" ]; then
    rm -rf "$VENV_DIR"
    if [ "$HAS_UV" -eq 1 ]; then
        uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
    else
        "python${PYTHON_VERSION%.*}" -m venv "$VENV_DIR"
    fi
fi
if [ "$HAS_UV" -eq 1 ]; then
    uv pip install --python "$VENV_PYTHON" -r "$BACKEND_DIR/requirements-dev.txt"
else
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$BACKEND_DIR/requirements-dev.txt"
fi

step "Миграции базы данных"
(cd "$BACKEND_DIR" && "$VENV_PYTHON" -m alembic upgrade head)

step "Зависимости frontend"
(cd "$FRONTEND_DIR" && CI=true pnpm install --frozen-lockfile)
if [ "$SKIP_BUILD" -eq 0 ]; then
    step "Сборка интерфейса"
    (cd "$FRONTEND_DIR" && pnpm run build) || warn "Сборка не удалась; можно работать через make dev."
fi

if [ "$SKIP_DOCTOR" -eq 0 ]; then
    step "Диагностика (app.cli doctor)"
    (cd "$BACKEND_DIR" && "$VENV_PYTHON" -m app.cli doctor) || warn "doctor сообщил о проблемах, см. вывод выше."
fi

printf '\033[32mГотово.\033[0m Запуск: make start (или make dev для разработки).\n'
