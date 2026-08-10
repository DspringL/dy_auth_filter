#!/usr/bin/env bash
# =============================================================================
#  桌面端一键构建脚本
#  用法：./build_desktop.sh [--debug] [--skip-backend]
#  输出：frontend/src-tauri/target/[debug|release]/bundle/
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEBUG=false
SKIP_BACKEND=false

for arg in "$@"; do
  case $arg in
    --debug)        DEBUG=true ;;
    --skip-backend) SKIP_BACKEND=true ;;
  esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo -e "\n${BOLD}╔══════════════════════════════════════╗"
echo -e "║   抖音达人系统 — 桌面端构建          ║"
echo -e "╚══════════════════════════════════════╝${NC}\n"

cd "$ROOT_DIR"

# ── 1. 检测环境 ─────────────────────────────────────────────────────────────
info "检测构建环境..."

command -v rustc &>/dev/null || err "未找到 Rust，请先安装：https://rustup.rs"
RUST_VER=$(rustc --version | awk '{print $2}')
ok "Rust $RUST_VER"

command -v node &>/dev/null || err "未找到 Node.js"
ok "Node.js $(node --version)"

PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"; break
  fi
done
[ -z "$PYTHON" ] && err "未找到 Python"
ok "Python $($PYTHON --version 2>&1 | awk '{print $2}')"

# ── 2. 激活虚拟环境 ──────────────────────────────────────────────────────────
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
  source "$ROOT_DIR/.venv/bin/activate"
  ok "虚拟环境已激活"
else
  info "未找到 .venv，尝试创建..."
  $PYTHON -m venv "$ROOT_DIR/.venv"
  source "$ROOT_DIR/.venv/bin/activate"
  pip install -q -r "$ROOT_DIR/requirements.txt"
fi

# ── 3. 打包 Python 后端 ──────────────────────────────────────────────────────
if [ "$SKIP_BACKEND" = false ]; then
  info "打包 Python 后端为可执行文件..."
  pip install -q pyinstaller
  $PYTHON build_backend.py && ok "后端打包完成" || err "后端打包失败"
else
  warn "跳过后端打包（--skip-backend）"
fi

# ── 4. 安装前端依赖 ──────────────────────────────────────────────────────────
info "安装前端依赖..."
(cd frontend && npm install --prefer-offline 2>&1 | tail -3)
ok "前端依赖已就绪"

# ── 5. 构建 Tauri 应用 ──────────────────────────────────────────────────────
info "开始构建 Tauri 桌面应用（首次需要下载 Rust 依赖，约 5~10 分钟）..."

if [ "$DEBUG" = true ]; then
  warn "调试模式：生成未压缩的 debug 包"
  (cd frontend && npx tauri build --debug)
  BUNDLE_DIR="frontend/src-tauri/target/debug/bundle"
else
  (cd frontend && npx tauri build)
  BUNDLE_DIR="frontend/src-tauri/target/release/bundle"
fi

# ── 6. 显示产物 ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════╗"
echo -e "║          构建完成！                  ║"
echo -e "╚══════════════════════════════════════╝${NC}"

OS=$(uname -s)
if [ "$OS" = "Darwin" ]; then
  find "$BUNDLE_DIR" -name "*.dmg" -o -name "*.app" 2>/dev/null | while read f; do
    ok "macOS 包：$f"
  done
elif [ "$OS" = "Linux" ]; then
  find "$BUNDLE_DIR" -name "*.AppImage" -o -name "*.deb" 2>/dev/null | while read f; do
    ok "Linux 包：$f"
  done
fi

echo ""
info "产物目录：$ROOT_DIR/$BUNDLE_DIR"
