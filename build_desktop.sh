#!/usr/bin/env bash
# =============================================================================
#  桌面端一键构建脚本
#
#  用法：
#    ./build_desktop.sh              # 正式版（release）
#    ./build_desktop.sh --debug      # 调试版（debug）
#    ./build_desktop.sh --skip-backend  # 跳过后端打包（sidecar 已存在时）
#
#  执行顺序：
#    1. 检测环境（Rust / Node / Python）
#    2. 激活 .venv 虚拟环境
#    3. 打包 Python 后端为 sidecar 可执行文件（build_backend.py）
#    4. 验证 sidecar 有效（大于 1MB，不是 stub）
#    5. 安装前端依赖（npm install）
#    6. 构建 Tauri 应用（tauri build）
#    7. 打印产物路径并可选打开 Finder
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

# ── 颜色 ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
step()    { echo -e "\n${BOLD}── Step $* ──────────────────────────────────────${NC}"; }
divider() { echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

clear
divider
echo -e "${BOLD}   🎬  抖音达人联络官 — 桌面端一键构建${NC}"
[ "$DEBUG" = true ] && echo -e "   模式：调试版（debug）" || echo -e "   模式：正式版（release）"
divider

cd "$ROOT_DIR"

# ── Step 1: 检测环境 ─────────────────────────────────────────────────────────
step "1/6  检测构建环境"

command -v rustc &>/dev/null  || err "未找到 Rust，请先安装：curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
command -v node  &>/dev/null  || err "未找到 Node.js，请先安装：https://nodejs.org"
command -v npm   &>/dev/null  || err "未找到 npm"

RUST_VER=$(rustc --version | awk '{print $2}')
NODE_VER=$(node --version)
ok "Rust      $RUST_VER"
ok "Node.js   $NODE_VER"

# 优先用 .venv 的 python
if [ -f "$ROOT_DIR/.venv/bin/python3" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
elif command -v python &>/dev/null; then
  PYTHON="python"
else
  err "未找到 Python，请先安装 Python 3.10+"
fi
ok "Python    $($PYTHON --version 2>&1 | awk '{print $2}')"

# ── Step 2: 激活虚拟环境 ─────────────────────────────────────────────────────
step "2/6  激活 Python 虚拟环境"

if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
  source "$ROOT_DIR/.venv/bin/activate"
  PYTHON="$ROOT_DIR/.venv/bin/python3"
  ok ".venv 已激活"
else
  info "未找到 .venv，正在创建..."
  python3 -m venv "$ROOT_DIR/.venv"
  source "$ROOT_DIR/.venv/bin/activate"
  PYTHON="$ROOT_DIR/.venv/bin/python3"
  info "安装 Python 依赖（首次约 2-3 分钟）..."
  pip install -q -r "$ROOT_DIR/requirements.txt"
  ok ".venv 创建并依赖安装完成"
fi

# ── Step 3: 打包 Python 后端 sidecar ─────────────────────────────────────────
step "3/6  打包 Python 后端 sidecar"

BINARIES_DIR="$ROOT_DIR/frontend/src-tauri/binaries"

if [ "$SKIP_BACKEND" = true ]; then
  warn "已指定 --skip-backend，跳过后端打包"
else
  info "正在打包后端（PyInstaller，约 2-5 分钟）..."
  pip install -q pyinstaller

  # 删除旧的 stub/产物，避免缓存旧文件
  rm -f "$BINARIES_DIR/backend-server-"*

  $PYTHON "$ROOT_DIR/build_backend.py"

  if [ $? -ne 0 ]; then
    err "后端打包失败，请查看上方错误信息"
  fi
fi

# ── Step 4: 验证 sidecar 有效 ────────────────────────────────────────────────
step "4/6  验证 sidecar 完整性"

OS=$(uname -s)
ARCH=$(uname -m)

if   [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
  SIDECAR="$BINARIES_DIR/backend-server-aarch64-apple-darwin"
elif [ "$OS" = "Darwin" ] && [ "$ARCH" = "x86_64" ]; then
  SIDECAR="$BINARIES_DIR/backend-server-x86_64-apple-darwin"
elif [ "$OS" = "Linux" ]; then
  SIDECAR="$BINARIES_DIR/backend-server-x86_64-unknown-linux-gnu"
else
  SIDECAR=$(find "$BINARIES_DIR" -name "backend-server-*" ! -name "*.py" 2>/dev/null | head -1)
fi

if [ ! -f "$SIDECAR" ]; then
  err "sidecar 不存在：$SIDECAR\n  请先运行：python build_backend.py"
fi

SIDECAR_SIZE=$(wc -c < "$SIDECAR" 2>/dev/null || echo 0)
# stub 是 53 字节，真正的 sidecar 应该 > 1MB
if [ "$SIDECAR_SIZE" -lt 1048576 ]; then
  err "sidecar 文件太小（${SIDECAR_SIZE} 字节），可能是 stub 脚本！\n  请先运行：python build_backend.py"
fi

SIDECAR_SIZE_MB=$(( SIDECAR_SIZE / 1024 / 1024 ))
ok "sidecar   ${SIDECAR_SIZE_MB}MB  ✓  $(basename "$SIDECAR")"

# ── Step 5: 安装前端依赖 ─────────────────────────────────────────────────────
step "5/6  安装前端依赖"

info "npm install..."
(cd "$ROOT_DIR/frontend" && npm install --prefer-offline 2>&1 | grep -E "added|updated|warn|error" | head -5 || true)
ok "前端依赖已就绪"

# ── Step 6: 构建 Tauri 应用 ──────────────────────────────────────────────────
step "6/6  构建 Tauri 桌面应用"

if [ "$DEBUG" = true ]; then
  info "调试版构建（输出不压缩，含 source map）..."
  (cd "$ROOT_DIR/frontend" && npx tauri build --debug)
  BUNDLE_DIR="$ROOT_DIR/frontend/src-tauri/target/debug/bundle"
else
  info "正式版构建（首次约 5-15 分钟）..."
  (cd "$ROOT_DIR/frontend" && npx tauri build)
  BUNDLE_DIR="$ROOT_DIR/frontend/src-tauri/target/release/bundle"
fi

# ── 完成，显示产物 ───────────────────────────────────────────────────────────
echo ""
divider
echo -e "${BOLD}${GREEN}   ✅  构建完成！${NC}"
divider
echo ""

FOUND=0
if [ "$OS" = "Darwin" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    SIZE=$(du -sh "$f" 2>/dev/null | awk '{print $1}')
    ok "📦  $f  ($SIZE)"
    FOUND=1
  done < <(find "$BUNDLE_DIR" \( -name "*.dmg" -o -name "*.app" \) 2>/dev/null)
elif [ "$OS" = "Linux" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    SIZE=$(du -sh "$f" 2>/dev/null | awk '{print $1}')
    ok "📦  $f  ($SIZE)"
    FOUND=1
  done < <(find "$BUNDLE_DIR" \( -name "*.AppImage" -o -name "*.deb" \) 2>/dev/null)
fi

if [ "$FOUND" -eq 0 ]; then
  warn "未找到产物，请检查构建日志"
else
  echo ""
  info "产物目录：$BUNDLE_DIR"
  # macOS 自动在 Finder 中打开产物目录
  if [ "$OS" = "Darwin" ] && command -v open &>/dev/null; then
    open "$BUNDLE_DIR" 2>/dev/null || true
  fi
fi

echo ""
