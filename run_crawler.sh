#!/usr/bin/env bash
# 独立运行爬虫（不依赖后端 API）
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source .venv/bin/activate 2>/dev/null || {
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -q -r requirements.txt
}

echo ">>> 运行爬虫..."
PYTHONPATH="$SCRIPT_DIR" python -m crawler.main "$@"
