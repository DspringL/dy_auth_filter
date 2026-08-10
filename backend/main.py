"""
FastAPI 主入口
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.models.database import init_db
from backend.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时初始化数据库"""
    logger.info("后端服务启动中...")
    await init_db()
    logger.info("后端服务已就绪")
    yield
    logger.info("后端服务关闭")


app = FastAPI(
    title="抖音带货达人管理系统",
    description="自动搜索、筛选带货达人，发送私信，管理后台展示",
    version="1.0.0",
    lifespan=lifespan,
)

# 开发阶段允许所有来源（生产环境改为 127.0.0.1）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", tags=["健康检查"])
async def health():
    return {"status": "ok", "service": "抖音带货达人管理系统"}
