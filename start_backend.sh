#!/usr/bin/env bash
# 启动 FastAPI 后端
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查虚拟环境
if [ ! -d ".venv" ]; then
  echo ">>> 创建虚拟环境..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo ">>> 安装/更新依赖..."
pip install -q -r requirements.txt

echo ">>> 安装 CloakBrowser 二进制..."
python -m cloakbrowser install 2>/dev/null || true

echo ">>> 启动后端服务 http://127.0.0.1:8000"
PYTHONPATH="$SCRIPT_DIR" uvicorn backend.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload \
  --loop asyncio
