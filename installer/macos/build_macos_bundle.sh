#!/usr/bin/env bash
# ==============================================================================
# Script to create macOS distribution bundle / DMG
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
BUNDLE_NAME="GrailedLiquidityAnalyzer-v1.0.0-macOS"
PACKAGE_DIR="${DIST_DIR}/${BUNDLE_NAME}"

echo "===================================================================="
echo "         PACKAGING MACOS BUNDLE                                    "
echo "===================================================================="

mkdir -p "$DIST_DIR"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

echo "Копирование файлов проекта..."
rsync -av --progress "$ROOT_DIR/" "$PACKAGE_DIR/" \
    --exclude '.git' \
    --exclude '.github' \
    --exclude 'backend/.venv' \
    --exclude 'frontend/node_modules' \
    --exclude 'frontend/.next' \
    --exclude 'data/backups' \
    --exclude 'data/cache' \
    --exclude 'data/logs' \
    --exclude 'dist'

chmod +x "${PACKAGE_DIR}/start.command"
chmod +x "${PACKAGE_DIR}/scripts/"*.sh

# Create DMG if hdiutil is available (macOS)
if command -v hdiutil &>/dev/null; then
    echo "Создание DMG образа..."
    DMG_PATH="${DIST_DIR}/${BUNDLE_NAME}.dmg"
    rm -f "$DMG_PATH"
    hdiutil create -volname "Grailed Analyzer" -srcfolder "$PACKAGE_DIR" -ov -format UDZO "$DMG_PATH"
    echo "[OK] DMG образ готов: $DMG_PATH"
else
    echo "Создание tar.gz архива (для сборки вне macOS)..."
    TAR_PATH="${DIST_DIR}/${BUNDLE_NAME}.tar.gz"
    tar -czf "$TAR_PATH" -C "$DIST_DIR" "$BUNDLE_NAME"
    echo "[OK] Архив готов: $TAR_PATH"
fi

rm -rf "$PACKAGE_DIR"
echo "===================================================================="
echo "         СБОРКА MACOS ПАКЕТА ЗАВЕРШЕНА                             "
echo "===================================================================="
