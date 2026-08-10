#!/usr/bin/env bash
# ============================================================
#  One-click startup script for Douyin KOL Automation System
#  Usage: ./start.sh [--no-frontend] [--no-browser] [--skip-update]
# ============================================================
set -euo pipefail

# -- Colors --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}>>> $*${NC}"; }

# -- Parse arguments --
NO_FRONTEND=false
NO_BROWSER=false
SKIP_UPDATE=false

for arg in "$@"; do
  case $arg in
    --no-frontend)  NO_FRONTEND=true ;;
    --no-browser)   NO_BROWSER=true  ;;
    --skip-update)  SKIP_UPDATE=true ;;
    --help|-h)
      echo "Usage: ./start.sh [options]"
      echo "  --no-frontend    Skip frontend (backend only)"
      echo "  --no-browser     Skip CloakBrowser binary download"
      echo "  --skip-update    Skip pip/npm update"
      exit 0 ;;
  esac
done

# -- Root dir --
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo -e "\n${BOLD}+------------------------------------------+"
echo -e "|   \xe6\x8a\x96\xe9\x9f\xb3\xe5\xb8\xa6\xe8\xb4\xa7\xe8\xbe\xbe\xe4\xba\xba\xe7\xb3\xbb\xe7\xbb\x9f \xe2\x80\x94 \xe4\xb8\x80\xe9\x94\xae\xe5\x90\xaf\xe5\x8a\xa8        |"
echo -e "+------------------------------------------+${NC}\n"

# ============================================================
# 1. Check system dependencies
# ============================================================
step "Step 1: \xe6\xa3\x80\xe6\xb5\x8b\xe7\xb3\xbb\xe7\xbb\x9f\xe7\x8e\xaf\xe5\xa2\x83"

# Python 3.10+
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PY_VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)")
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
      success "Python $PY_VER OK"
      PYTHON="$cmd"
      break
    else
      warn "Python $PY_VER found but need 3.10+"
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  error "Python 3.10+ not found. Install: https://www.python.org/downloads/"
fi

# Node.js 18+ (frontend only)
if [ "$NO_FRONTEND" = false ]; then
  if command -v node &>/dev/null; then
    NODE_VER=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 18 ]; then
      success "Node.js v$NODE_VER OK"
    else
      warn "Node.js $NODE_VER is older than recommended 18+"
    fi
  else
    warn "Node.js not found, skipping frontend. Install: https://nodejs.org"
    NO_FRONTEND=true
  fi

  if ! command -v npm &>/dev/null; then
    warn "npm not found, skipping frontend."
    NO_FRONTEND=true
  else
    success "npm $(npm --version) OK"
  fi
fi

OS_TYPE=$(uname -s)
if [ "$OS_TYPE" = "Darwin" ]; then
  success "OS: macOS"
elif [ "$OS_TYPE" = "Linux" ]; then
  success "OS: Linux"
else
  warn "OS: $OS_TYPE (not fully tested)"
fi

# ============================================================
# 2. Python virtual environment
# ============================================================
step "Step 2: \xe9\x85\x8d\xe7\xbd\xae\xe8\x99\x9a\xe6\x8b\x9f\xe7\x8e\xaf\xe5\xa2\x83"

VENV_DIR="$ROOT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
  info "Creating .venv ..."
  "$PYTHON" -m venv "$VENV_DIR"
  success "Virtual env created"
else
  success "Virtual env exists, skipping"
fi

source "$VENV_DIR/bin/activate"
PY_VENV=$(which python)
info "Using Python: $PY_VENV"

# ============================================================
# 3. Install Python dependencies
# ============================================================
step "Step 3: \xe5\xae\x89\xe8\xa3\x85 Python \xe4\xbe\x9d\xe8\xb5\x96"

PIP_STAMP="$ROOT_DIR/.venv/.pip_installed"
REQ_FILE="$ROOT_DIR/requirements.txt"

# Re-install if stamp is missing OR requirements.txt is newer than stamp
NEED_INSTALL=false
if [ "$SKIP_UPDATE" = false ]; then
  NEED_INSTALL=true
elif [ ! -f "$PIP_STAMP" ]; then
  NEED_INSTALL=true
elif [ "$REQ_FILE" -nt "$PIP_STAMP" ]; then
  info "requirements.txt changed, reinstalling..."
  NEED_INSTALL=true
fi

if [ "$NEED_INSTALL" = true ]; then
  info "Upgrading pip..."
  pip install -q --upgrade pip
  info "Installing from requirements.txt ..."
  pip install -r "$REQ_FILE"
  date > "$PIP_STAMP"
  success "Python dependencies installed"
else
  success "Python dependencies already up-to-date (use --skip-update to force skip)"
fi

# Verify key packages
for pkg in fastapi uvicorn playwright cloakbrowser sqlalchemy; do
  if python -c "import $pkg" &>/dev/null 2>&1; then
    success "  $pkg OK"
  else
    warn "  $pkg import failed, reinstalling..."
    pip install -r "$REQ_FILE"
    break
  fi
done

# ============================================================
# 4. CloakBrowser binary
# ============================================================
if [ "$NO_BROWSER" = false ]; then
  step "Step 4: CloakBrowser \xe4\xba\x8c\xe8\xbf\x9b\xe5\x88\xb6"

  CLOAK_CACHE="$HOME/.cloakbrowser"
  CHROME_BIN=$(find "$CLOAK_CACHE" -name "chrome" -o -name "Chromium" 2>/dev/null | head -1 || true)

  if [ -n "$CHROME_BIN" ]; then
    success "CloakBrowser binary found: $CHROME_BIN"
  else
    info "Downloading CloakBrowser binary (~200MB, first time only)..."
    python -m cloakbrowser install || warn "Download failed. Use --no-browser to skip."
    success "CloakBrowser installed"
  fi

  # macOS: remove quarantine flag
  if [ "$OS_TYPE" = "Darwin" ]; then
    CHROMIUM_APP=$(find "$HOME/.cloakbrowser" -name "Chromium.app" 2>/dev/null | head -1 || true)
    if [ -n "$CHROMIUM_APP" ]; then
      xattr -cr "$CHROMIUM_APP" 2>/dev/null && info "Removed macOS Gatekeeper quarantine" || true
    fi
  fi
fi

# ============================================================
# 5. Frontend dependencies
# ============================================================
if [ "$NO_FRONTEND" = false ]; then
  step "Step 5: \xe5\x89\x8d\xe7\xab\xaf\xe4\xbe\x9d\xe8\xb5\x96 (npm)"

  FRONTEND_DIR="$ROOT_DIR/frontend"
  NODE_MODULES="$FRONTEND_DIR/node_modules"

  if [ "$SKIP_UPDATE" = false ] || [ ! -d "$NODE_MODULES" ]; then
    info "Running npm install..."
    (cd "$FRONTEND_DIR" && npm install --prefer-offline 2>&1 | tail -3)
    success "Frontend dependencies installed"
  else
    success "node_modules exists, skipping"
  fi
fi

# ============================================================
# 6. Init data directories
# ============================================================
step "Step 6: \xe5\x88\x9d\xe5\xa7\x8b\xe5\x8c\x96\xe6\x95\xb0\xe6\x8d\xae\xe7\x9b\xae\xe5\xbd\x95"

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs" "$ROOT_DIR/data/browser_profile"
success "data/ and logs/ ready"

# ============================================================
# 7. Check port availability
# ============================================================
step "Step 7: \xe7\xab\xaf\xe5\x8f\xa3\xe6\xa3\x80\xe6\xb5\x8b"

BACKEND_PORT=8000
FRONTEND_PORT=5173

check_port() {
  local port=$1 name=$2
  if lsof -i ":$port" &>/dev/null 2>&1; then
    warn "Port $port ($name) is in use, continuing..."
  else
    success "Port $port ($name) available"
  fi
}

check_port $BACKEND_PORT "Backend API"
if [ "$NO_FRONTEND" = false ]; then
  check_port $FRONTEND_PORT "Frontend"
fi

# ============================================================
# 8. Start services
# ============================================================
step "Step 8: \xe5\x90\xaf\xe5\x8a\xa8\xe6\x9c\x8d\xe5\x8a\xa1"

PIDS=()

cleanup() {
  echo -e "\n${YELLOW}Shutting down all services...${NC}"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  echo -e "${GREEN}All services stopped.${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

# Start backend
BACKEND_LOG="$ROOT_DIR/logs/backend.log"
info "Starting backend (log -> logs/backend.log)"

PYTHONPATH="$ROOT_DIR" uvicorn backend.main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --reload \
  --loop asyncio \
  --log-level info \
  > "$BACKEND_LOG" 2>&1 &

BACKEND_PID=$!
PIDS+=("$BACKEND_PID")

# Wait for backend
info "Waiting for backend to be ready..."
BACKEND_READY=false
for i in $(seq 1 25); do
  if curl -sf "http://127.0.0.1:${BACKEND_PORT}/" >/dev/null 2>&1; then
    success "Backend ready -> http://127.0.0.1:${BACKEND_PORT}"
    success "API docs    -> http://127.0.0.1:${BACKEND_PORT}/docs"
    BACKEND_READY=true
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo "---- backend.log (last 20 lines) ----"
    tail -20 "$BACKEND_LOG" 2>/dev/null || true
    error "Backend process exited. Check logs/backend.log"
  fi
  sleep 1
done

if [ "$BACKEND_READY" = false ]; then
  warn "Backend startup timeout. Check logs/backend.log"
fi

# Start frontend
if [ "$NO_FRONTEND" = false ]; then
  FRONTEND_LOG="$ROOT_DIR/logs/frontend.log"
  info "Starting frontend (log -> logs/frontend.log)"

  (cd "$ROOT_DIR/frontend" && npm run dev -- --port "$FRONTEND_PORT") \
    > "$FRONTEND_LOG" 2>&1 &

  FRONTEND_PID=$!
  PIDS+=("$FRONTEND_PID")

  info "Waiting for frontend to be ready..."
  for i in $(seq 1 25); do
    if curl -sf "http://localhost:${FRONTEND_PORT}/" >/dev/null 2>&1; then
      success "Frontend ready -> http://localhost:${FRONTEND_PORT}"
      break
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      warn "Frontend process exited. Check logs/frontend.log"
      break
    fi
    sleep 1
    if [ "$i" -eq 25 ]; then
      warn "Frontend startup timeout. Check logs/frontend.log"
    fi
  done
fi

# ============================================================
# 9. Summary
# ============================================================
echo ""
echo -e "${BOLD}${GREEN}+==================================================+"
if [ "$NO_FRONTEND" = false ]; then
echo -e "| \xe7\xae\xa1\xe7\x90\x86\xe5\x90\x8e\xe5\x8f\xb0 -> http://localhost:${FRONTEND_PORT}             |"
fi
echo -e "| \xe5\x90\x8e\xe7\xab\xaf API  -> http://127.0.0.1:${BACKEND_PORT}          |"
echo -e "| API \xe6\x96\x87\xe6\xa1\xa3 -> http://127.0.0.1:${BACKEND_PORT}/docs       |"
echo -e "| \xe6\x97\xa5\xe5\xbf\x97\xe7\x9b\xae\xe5\xbd\x95 -> ./logs/                           |"
echo -e "| Press Ctrl+C to stop all services               |"
echo -e "+==================================================+${NC}"
echo ""

# Auto-open browser
if [ "$NO_FRONTEND" = false ]; then
  if command -v open &>/dev/null; then
    sleep 1 && open "http://localhost:${FRONTEND_PORT}" &
  elif command -v xdg-open &>/dev/null; then
    sleep 1 && xdg-open "http://localhost:${FRONTEND_PORT}" &
  fi
fi

# Tail backend log
info "Live backend log (Ctrl+C to exit):"
echo "----------------------------------------------------"
tail -f "$BACKEND_LOG" &
TAIL_PID=$!
PIDS+=("$TAIL_PID")

wait
