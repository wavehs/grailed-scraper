#!/usr/bin/env bash
# ==============================================================================
# Grailed Liquidity Analyzer - Universal Zero-Dependency Linux / macOS Installer
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOOLS_DIR="${ROOT_DIR}/.tools"

# --- Color Helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step() { echo -e "${CYAN}[$1]${NC} $2"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}     GRAILED LIQUIDITY ANALYZER - ZERO-DEPENDENCY SMART INSTALLER   ${NC}"
echo -e "${CYAN}====================================================================${NC}"
echo ""

# --- Helper Functions ---

find_python() {
    for cmd in python3.12 python3.11 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
            MAJOR=$(echo "$VER" | cut -d. -f1)
            MINOR=$(echo "$VER" | cut -d. -f2)
            if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

install_python_auto() {
    warn "Python 3.11+ не найден. Попытка автоматической установки..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            echo "Установка Python 3.11 через Homebrew..."
            brew install python@3.11
            find_python && return 0
        fi
    elif [ -f /etc/debian_version ]; then
        echo "Установка Python 3.11 через apt-get..."
        sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip || true
        find_python && return 0
    fi
    return 1
}

find_node() {
    if command -v node &>/dev/null; then
        VER=$(node -v | sed 's/v//' | cut -d. -f1)
        if [ "$VER" -ge 20 ]; then
            echo "node"
            return 0
        fi
    fi
    if [ -f "${TOOLS_DIR}/node/bin/node" ]; then
        export PATH="${TOOLS_DIR}/node/bin:${PATH}"
        echo "${TOOLS_DIR}/node/bin/node"
        return 0
    fi
    return 1
}

install_node_auto() {
    warn "Node.js (v20+) не найден. Попытка автоматической установки..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            echo "Установка Node.js 20 через Homebrew..."
            brew install node@20
            find_node && return 0
        fi
    fi

    # Fallback: Download official portable node tar.gz
    mkdir -p "${TOOLS_DIR}"
    local OS_TYPE="linux"
    [[ "$OSTYPE" == "darwin"* ]] && OS_TYPE="darwin"
    local ARCH_TYPE="x64"
    [[ "$(uname -m)" == "arm64" || "$(uname -m)" == "aarch64" ]] && ARCH_TYPE="arm64"

    local NODE_URL="https://nodejs.org/dist/v20.18.3/node-v20.18.3-${OS_TYPE}-${ARCH_TYPE}.tar.gz"
    echo "Загрузка портативного Node.js с ${NODE_URL}..."
    curl -fsSL "$NODE_URL" -o "${TOOLS_DIR}/node.tar.gz"
    mkdir -p "${TOOLS_DIR}/node_tmp"
    tar -xzf "${TOOLS_DIR}/node.tar.gz" -C "${TOOLS_DIR}/node_tmp"
    rm -rf "${TOOLS_DIR}/node"
    mv "${TOOLS_DIR}/node_tmp/"node-v20* "${TOOLS_DIR}/node"
    rm -rf "${TOOLS_DIR}/node_tmp" "${TOOLS_DIR}/node.tar.gz"

    export PATH="${TOOLS_DIR}/node/bin:${PATH}"
    find_node && return 0
    return 1
}

setup_pnpm_auto() {
    if command -v pnpm &>/dev/null; then
        echo "pnpm"
        return 0
    fi
    corepack enable 2>/dev/null || true
    corepack prepare pnpm@9.15.9 --activate 2>/dev/null || true
    if command -v pnpm &>/dev/null; then
        echo "pnpm"
        return 0
    fi
    npm install -g pnpm@9.15.9 2>/dev/null || true
    if command -v pnpm &>/dev/null; then
        echo "pnpm"
        return 0
    fi
    return 1
}

# --- 1. System Requirements & Auto-Bootstrap ---
step "1/8" "Проверка и автоматическая подготовка среды..."

PYTHON_CMD=$(find_python || install_python_auto || true)
if [ -z "$PYTHON_CMD" ]; then
    err "Не удалось автоматически установить Python 3.11+!"
    echo "Пожалуйста, установите Python 3.11+ вручную."
    exit 1
fi
success "Python готов: $($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

NODE_CMD=$(find_node || install_node_auto || true)
if [ -z "$NODE_CMD" ]; then
    err "Не удалось автоматически установить Node.js 20+!"
    exit 1
fi
success "Node.js готов: $(node -v)"

PNPM_CMD=$(setup_pnpm_auto || true)
if [ -z "$PNPM_CMD" ]; then
    err "Не удалось настроить pnpm!"
    exit 1
fi
success "pnpm готов: $(pnpm -v)"

# --- 2. Directories ---
step "2/8" "Инициализация директорий..."
mkdir -p "${ROOT_DIR}/data" "${ROOT_DIR}/data/logs" "${ROOT_DIR}/data/cache" "${ROOT_DIR}/data/backups" "${ROOT_DIR}/data/secrets"
success "Директории data/ готовы."

# --- 3. Configuration (.env) ---
step "3/8" "Настройка .env..."
ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        success "Создан .env из .env.example."
    fi
else
    success "Файл .env уже существует."
fi

# Prompt for compliance acknowledgment if not set
if grep -q "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo -e "${YELLOW}ВНИМАНИЕ: Для работы с живым источником Grailed требуется согласие с ToS и правилами доступа.${NC}"
    read -p "Подтвердить соблюдение комплаенса и включить реальный парсинг? (Y/n): " -r REPLY
    if [[ -z "$REPLY" || "$REPLY" =~ ^[YyДд]$ ]]; then
        sed -i.bak "s/APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false/APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true/" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
        success "Комплаенс подтвержден: APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true"
    fi
fi

# --- 4. Python Virtual Environment ---
step "4/8" "Настройка виртуального окружения backend/.venv..."
VENV_DIR="${ROOT_DIR}/backend/.venv"
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    success "Виртуальное окружение создано."
else
    success "Виртуальное окружение уже существует."
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# --- 5. Backend Dependencies ---
step "5/8" "Установка зависимостей Backend и движков браузера..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel --quiet

REQ_FILE="${ROOT_DIR}/backend/requirements-dev.txt"
if [ ! -f "$REQ_FILE" ]; then
    REQ_FILE="${ROOT_DIR}/backend/requirements.txt"
fi

"$VENV_PIP" install -r "$REQ_FILE" --disable-pip-version-check
success "Python зависимости установлены."

# Scrapling install
if [ -f "${VENV_DIR}/bin/scrapling" ]; then
    "${VENV_DIR}/bin/scrapling" install || true
    success "Браузерные движки Scrapling готовы."
fi

# --- 6. Migrations ---
step "6/8" "Применение миграций базы данных..."
cd "${ROOT_DIR}/backend"
"${VENV_DIR}/bin/alembic" upgrade head
success "Миграции успешно применены."

# --- 7. Frontend ---
step "7/8" "Установка зависимостей и сборка Frontend..."
cd "${ROOT_DIR}/frontend"
pnpm install --frozen-lockfile || pnpm install
pnpm run build
success "Frontend успешно собран."

# --- 8. Doctor & Permissions ---
step "8/8" "Диагностика системы..."
cd "${ROOT_DIR}/backend"
"${VENV_PYTHON}" -m app.cli doctor || true

chmod +x "${ROOT_DIR}/scripts/start.sh" 2>/dev/null || true
chmod +x "${ROOT_DIR}/scripts/install.sh" 2>/dev/null || true
chmod +x "${ROOT_DIR}/start.command" 2>/dev/null || true

echo ""
echo -e "${GREEN}====================================================================${NC}"
echo -e "${GREEN}                 УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!                       ${NC}"
echo -e "${GREEN}====================================================================${NC}"
echo ""
echo "Для запуска приложения используйте:"
echo "  -> ./scripts/start.sh"
echo "  -> или ./start.command (на macOS)"
echo ""
