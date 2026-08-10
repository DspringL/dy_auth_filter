#!/usr/bin/env bash
# =============================================================================
#  环境自检脚本 — 不启动服务，仅检测依赖是否满足
#  用法：./check_env.sh
# =============================================================================
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

PASS=0; FAIL=0; WARN_CNT=0

ok()   { echo -e "  ${GREEN}✓${NC}  $*"; ((PASS++)); }
fail() { echo -e "  ${RED}✗${NC}  $*"; ((FAIL++)); }
warn() { echo -e "  ${YELLOW}!${NC}  $*"; ((WARN_CNT++)); }
section() { echo -e "\n${BOLD}${BLUE}[$*]${NC}"; }

echo -e "\n${BOLD}抖音带货达人系统 — 环境自检${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Python ────────────────────────────────────────────────────────────────────
section "Python"
if command -v python3 &>/dev/null; then
    VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
    MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
        ok "python3 $VER"
    else
        fail "python3 $VER（需要 3.10+）"
    fi
else
    fail "python3 未找到"
fi

# ── 虚拟环境 & pip 包 ──────────────────────────────────────────────────────────
section "Python 虚拟环境"
VENV="$ROOT_DIR/.venv"
if [ -d "$VENV" ]; then
    ok ".venv 已存在"
    VENV_PY="$VENV/bin/python"

    for pkg in fastapi uvicorn cloakbrowser playwright sqlalchemy aiosqlite httpx loguru pydantic; do
        if "$VENV_PY" -c "import $pkg" &>/dev/null 2>&1; then
            VER=$("$VENV_PY" -c "import importlib.metadata; print(importlib.metadata.version('$pkg'))" 2>/dev/null || echo "?")
            ok "$pkg $VER"
        else
            fail "$pkg 未安装（运行 ./start.sh 自动安装）"
        fi
    done

    # execjs（可选）
    if "$VENV_PY" -c "import execjs" &>/dev/null 2>&1; then
        ok "execjs（JS 逆向降级方案）"
    else
        warn "execjs 未安装（可选，用于离线 X-Bogus 生成）"
    fi
else
    warn ".venv 不存在（首次运行 ./start.sh 会自动创建）"
fi

# ── CloakBrowser ──────────────────────────────────────────────────────────────
section "CloakBrowser 二进制"
CLOAK_DIR="$HOME/.cloakbrowser"
if [ -d "$CLOAK_DIR" ]; then
    CHROME=$(find "$CLOAK_DIR" \( -name "chrome" -o -name "Chromium" -o -name "Chromium.app" \) 2>/dev/null | head -1)
    if [ -n "$CHROME" ]; then
        ok "已安装：$CHROME"
    else
        warn "目录存在但未找到二进制（运行 python -m cloakbrowser install）"
    fi
else
    warn "~/.cloakbrowser 不存在（运行 ./start.sh 时自动下载）"
fi

# macOS Gatekeeper
if [ "$(uname -s)" = "Darwin" ]; then
    CHROMIUM_APP=$(find "$CLOAK_DIR" -name "Chromium.app" 2>/dev/null | head -1)
    if [ -n "$CHROMIUM_APP" ]; then
        if xattr -l "$CHROMIUM_APP" 2>/dev/null | grep -q "com.apple.quarantine"; then
            warn "Chromium.app 被 Gatekeeper 隔离，运行：xattr -cr '$CHROMIUM_APP'"
        else
            ok "macOS Gatekeeper 隔离已解除"
        fi
    fi
fi

# ── Node.js / npm ─────────────────────────────────────────────────────────────
section "Node.js / 前端"
if command -v node &>/dev/null; then
    NODE_VER=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo $NODE_VER | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 18 ]; then
        ok "node v$NODE_VER"
    else
        warn "node v$NODE_VER（建议 18+）"
    fi
else
    warn "node 未找到（前端将无法启动）"
fi

if command -v npm &>/dev/null; then
    ok "npm $(npm --version)"
else
    warn "npm 未找到"
fi

FRONTEND_MODULES="$ROOT_DIR/frontend/node_modules"
if [ -d "$FRONTEND_MODULES" ]; then
    ok "node_modules 已安装"
else
    warn "node_modules 不存在（运行 ./start.sh 自动安装）"
fi

# ── 端口 ──────────────────────────────────────────────────────────────────────
section "端口检测"
for port_info in "8000:后端 API" "5173:前端 Dev Server"; do
    PORT="${port_info%%:*}"
    NAME="${port_info##*:}"
    if lsof -i ":$PORT" &>/dev/null 2>&1; then
        PROC=$(lsof -i ":$PORT" -sTCP:LISTEN -n -P 2>/dev/null | awk 'NR==2{print $1,$2}')
        warn "端口 $PORT ($NAME) 已被占用：$PROC"
    else
        ok "端口 $PORT ($NAME) 可用"
    fi
done

# ── 配置文件 ──────────────────────────────────────────────────────────────────
section "配置文件"
if [ -f "$ROOT_DIR/config/settings.yaml" ]; then
    ok "config/settings.yaml ✓"

    # 检测私信模板是否已修改（不应还是占位符 XXXXXXXX 才好，提醒用户）
    if grep -q "XXXXXXXX" "$ROOT_DIR/config/settings.yaml"; then
        warn "私信模板中包含 XXXXXXXX 占位符，请修改为真实联系方式"
    fi
else
    fail "config/settings.yaml 不存在"
fi

if [ -f "$ROOT_DIR/data/cookies.json" ]; then
    COOKIE_AGE=$(( ( $(date +%s) - $(stat -f %m "$ROOT_DIR/data/cookies.json" 2>/dev/null || stat -c %Y "$ROOT_DIR/data/cookies.json") ) / 86400 ))
    if [ "$COOKIE_AGE" -lt 30 ]; then
        ok "Cookie 文件存在（${COOKIE_AGE} 天前更新）"
    else
        warn "Cookie 文件已 ${COOKIE_AGE} 天未更新，可能已过期，建议重新登录"
    fi
else
    warn "data/cookies.json 不存在（首次运行需扫码登录）"
fi

# ── 汇总 ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS + FAIL + WARN_CNT))
echo -e "检测完成：共 $TOTAL 项"
echo -e "  ${GREEN}通过${NC} $PASS 项  ${RED}失败${NC} $FAIL 项  ${YELLOW}警告${NC} $WARN_CNT 项"

if [ "$FAIL" -gt 0 ]; then
    echo -e "\n${RED}存在失败项，请根据提示修复后重新运行 ./start.sh${NC}"
    exit 1
elif [ "$WARN_CNT" -gt 0 ]; then
    echo -e "\n${YELLOW}存在警告项，系统可能正常运行，建议检查后启动${NC}"
    exit 0
else
    echo -e "\n${GREEN}环境检测通过，运行 ./start.sh 启动系统${NC}"
    exit 0
fi
