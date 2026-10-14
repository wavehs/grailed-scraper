#!/usr/bin/env bash
# Grailed Liquidity Analyzer - macOS launcher.
#   scripts/start.sh       -> build UI if sources changed, migrate DB, serve app on :8000
#   scripts/start.sh dev   -> backend :8000 + Next.js dev server :3000
# NO_BROWSER=1 skips opening the browser.
set -euo pipefail

MODE="${1:-run}"
case "$MODE" in run | dev) ;; *) echo "Usage: $0 [run|dev]" >&2; exit 2 ;; esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
NO_BROWSER="${NO_BROWSER:-0}"

step() { printf '\033[36m>> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[WARN] %s\033[0m\n' "$1"; }

# Free a port only from our own stale backend or Next.js process; never kill anything else.
free_port() {
    local port="$1" pid cmd
    for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
        cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
        if [[ "$cmd" == *"uvicorn app.main:app"* || "$cmd" == *next-server* || "$cmd" == *"next dev"* || "$cmd" == */next/dist/* ]]; then
            warn "Освобождаю порт $port (PID $pid)"
            kill "$pid" 2>/dev/null || true
            # Wait for graceful shutdown so the port and the single-instance lock are free.
            for _ in $(seq 1 30); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$pid" 2>/dev/null; then
                echo "PID $pid не завершился за 30 с." >&2
                exit 1
            fi
        else
            echo "Порт $port занят чужим процессом (PID $pid): $cmd" >&2
            exit 1
        fi
    done
}

frontend_stale() {
    local index="$FRONTEND_DIR/out/index.html"
    [ ! -f "$index" ] && return 0
    [ -n "$(find "$FRONTEND_DIR/src" "$FRONTEND_DIR/public" "$FRONTEND_DIR"/*.config.* \
        "$FRONTEND_DIR/package.json" "$FRONTEND_DIR/pnpm-lock.yaml" \
        -type f -newer "$index" -print -quit 2>/dev/null)" ]
}

open_url() {
    if [ "$NO_BROWSER" != "1" ] && command -v open >/dev/null 2>&1; then
        (sleep 3 && open "$1") &
    fi
}

if [ ! -x "$VENV_PYTHON" ]; then
    warn "Окружение не установлено, запускаю setup."
    "$ROOT_DIR/scripts/setup.sh"
fi

step "Миграции базы данных"
(cd "$BACKEND_DIR" && "$VENV_PYTHON" -m alembic upgrade head)

if [ "$MODE" = "run" ] && frontend_stale; then
    step "Сборка интерфейса"
    (cd "$FRONTEND_DIR" && pnpm run build)
fi

free_port 8000
[ "$MODE" = "dev" ] && free_port 3000

PIDS=()
cleanup() {
    trap - INT TERM EXIT
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

# One worker, no --reload: the backend holds a single-instance lock and SQLite has one writer.
(cd "$BACKEND_DIR" && exec "$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
PIDS+=("$!")

if [ "$MODE" = "dev" ]; then
    (cd "$FRONTEND_DIR" && exec pnpm exec next dev --hostname 127.0.0.1 --port 3000) &
    PIDS+=("$!")
    step "Backend http://127.0.0.1:8000, UI http://127.0.0.1:3000"
    open_url "http://127.0.0.1:3000"
else
    step "Запуск http://127.0.0.1:8000"
    open_url "http://127.0.0.1:8000"
fi
echo "Ctrl+C для остановки."

# Stop everything as soon as any child exits.
while :; do
    for pid in "${PIDS[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            warn "Процесс $pid остановился."
            exit 1
        fi
    done
    sleep 1
done
