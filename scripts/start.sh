#!/usr/bin/env bash
# ==============================================================================
# Grailed Liquidity Analyzer - Interactive Launcher (Linux / macOS)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENV_PYTHON="${ROOT_DIR}/backend/.venv/bin/python"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

show_banner() {
    clear
    echo -e "${CYAN}====================================================================${NC}"
    echo -e "${CYAN}         GRAILED LIQUIDITY ANALYZER - CONTROL CENTER                ${NC}"
    echo -e "${CYAN}====================================================================${NC}"
    echo ""
}

test_prerequisites() {
    if [ ! -f "$VENV_PYTHON" ]; then
        echo -e "${YELLOW}[WARN] Виртуальное окружение не обнаружено! Запуск установки...${NC}"
        bash "${SCRIPT_DIR}/install.sh"
        if [ ! -f "$VENV_PYTHON" ]; then
            echo -e "${RED}[ERROR] Установка не завершена.${NC}"
            exit 1
        fi
    fi
}

kill_ports() {
    for port in 8000 3000; do
        if command -v lsof &>/dev/null; then
            pids=$(lsof -ti :$port 2>/dev/null || true)
            if [ -n "$pids" ]; then
                echo -e "${YELLOW}[WARN] Освобождение порта $port (PID: $pids)...${NC}"
                kill -9 $pids 2>/dev/null || true
            fi
        fi
    done
}

open_browser() {
    local url="$1"
    if command -v xdg-open &>/dev/null; then
        xdg-open "$url" &>/dev/null &
    elif command -v open &>/dev/null; then
        open "$url" &>/dev/null &
    fi
}

start_prod() {
    show_banner
    echo -e "${GREEN}>> Запуск приложения в Production режиме...${NC}"
    kill_ports

    trap 'echo -e "\n${YELLOW}Остановка служб...${NC}"; kill 0; exit 0' SIGINT SIGTERM EXIT

    echo -e "${CYAN}1. Запуск Backend (FastAPI :8000)...${NC}"
    (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m uvicorn app.main:app --port 8000 --host 127.0.0.1) &
    BACKEND_PID=$!

    echo -e "${CYAN}2. Запуск Frontend (Next.js :3000)...${NC}"
    (cd "${ROOT_DIR}/frontend" && pnpm run start) &
    FRONTEND_PID=$!

    sleep 3
    echo ""
    echo -e "${GREEN}[OK] Backend: http://127.0.0.1:8000${NC}"
    echo -e "${GREEN}[OK] Frontend: http://127.0.0.1:3000${NC}"
    echo ""
    open_browser "http://127.0.0.1:3000"

    echo -e "${YELLOW}====================================================================${NC}"
    echo -e "${YELLOW} Приложение активно! Нажмите Ctrl+C для остановки всех служб.       ${NC}"
    echo -e "${YELLOW}====================================================================${NC}"

    wait $BACKEND_PID $FRONTEND_PID
}

start_dev() {
    show_banner
    echo -e "${YELLOW}>> Запуск приложения в режиме РАЗРАБОТКИ (Dev mode)...${NC}"
    kill_ports

    trap 'echo -e "\n${YELLOW}Остановка dev-серверов...${NC}"; kill 0; exit 0' SIGINT SIGTERM EXIT

    echo -e "${CYAN}1. Запуск Backend (FastAPI)...${NC}"
    (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m uvicorn app.main:app --port 8000 --host 127.0.0.1) &

    echo -e "${CYAN}2. Запуск Frontend (Next.js Dev)...${NC}"
    (cd "${ROOT_DIR}/frontend" && pnpm run dev) &

    sleep 3
    echo ""
    echo -e "${GREEN}[OK] Backend dev: http://127.0.0.1:8000${NC}"
    echo -e "${GREEN}[OK] Frontend dev: http://127.0.0.1:3000${NC}"
    echo ""
    open_browser "http://127.0.0.1:3000"

    echo -e "${YELLOW}====================================================================${NC}"
    echo -e "${YELLOW} Dev-режим активен! Нажмите Ctrl+C для остановки.                   ${NC}"
    echo -e "${YELLOW}====================================================================${NC}"

    wait
}

run_doctor() {
    show_banner
    echo -e "${CYAN}>> Диагностика и проверка тестов...${NC}"
    echo ""
    echo -e "${CYAN}--- 1. Backend Doctor ---${NC}"
    (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m app.cli doctor) || true

    echo ""
    echo -e "${CYAN}--- 2. Pytest ---${NC}"
    (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m pytest tests -q) || true

    echo ""
    echo -e "${CYAN}--- 3. Vitest ---${NC}"
    (cd "${ROOT_DIR}/frontend" && pnpm run test) || true

    echo ""
    read -p "Нажмите Enter для возврата в меню..."
}

update_deps() {
    show_banner
    echo -e "${CYAN}>> Обновление зависимостей и миграций...${NC}"
    (cd "${ROOT_DIR}/backend" && "${ROOT_DIR}/backend/.venv/bin/pip" install -r requirements-dev.txt)
    (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m alembic upgrade head)
    (cd "${ROOT_DIR}/frontend" && pnpm install && pnpm run build)
    echo -e "${GREEN}[OK] Обновление завершено!${NC}"
    read -p "Нажмите Enter для возврата в меню..."
}

manage_db() {
    show_banner
    echo -e "${CYAN}>> Управление базой данных SQLite${NC}"
    echo "  [1] Создать резервную копию (db-backup)"
    echo "  [2] Применить политику retention"
    echo "  [3] Перестроить модели ликвидности (market-rebuild)"
    echo "  [0] Назад"
    echo ""
    read -p "Выберите действие [0-3]: " db_choice
    case $db_choice in
        1) (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m app.cli db-backup) ;;
        2) (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m app.cli retention --apply) ;;
        3) (cd "${ROOT_DIR}/backend" && "$VENV_PYTHON" -m app.cli market-rebuild) ;;
    esac
    if [ "$db_choice" != "0" ]; then
        read -p "Нажмите Enter для возврата в меню..."
    fi
}

test_prerequisites

while true; do
    show_banner
    echo -e "  ${GREEN}[1]${NC} Запустить приложение (Production)"
    echo -e "  ${YELLOW}[2]${NC} Запустить в режиме разработки (Dev mode)"
    echo -e "  ${CYAN}[3]${NC} Проверка здоровья (Doctor) и запуск тестов"
    echo -e "  ${CYAN}[4]${NC} Обновить зависимости и накат миграций"
    echo -e "  ${MAGENTA}[5]${NC} Управление базой данных (Бэкап / Retention / Rebuild)"
    echo -e "  ${RED}[0]${NC} Выход"
    echo ""
    read -p "Выберите пункт меню [1, 2, 3, 4, 5, 0]: " choice
    case $choice in
        1) start_prod ;;
        2) start_dev ;;
        3) run_doctor ;;
        4) update_deps ;;
        5) manage_db ;;
        0) exit 0 ;;
        *) echo -e "${YELLOW}Неверный выбор.${NC}"; sleep 1 ;;
    esac
done
