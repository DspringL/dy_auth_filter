#!/usr/bin/env bash
# 启动 Vue3 前端开发服务器
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/frontend"

echo ">>> 安装前端依赖..."
npm install

echo ">>> 启动前端开发服务器 http://localhost:5173"
npm run dev
