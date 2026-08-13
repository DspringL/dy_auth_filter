"""
FastAPI 主入口
"""
import os
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.models.database import init_db
from backend.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时初始化数据库和浏览器，退出时释放浏览器进程。"""
    logger.info("后端服务启动中...")
    await init_db()
    from crawler.core.browser import get_browser
    try:
        await get_browser()
        logger.info("浏览器进程已就绪，自动恢复看门狗已启动")
    except Exception as exc:
        # 浏览器看门狗已经启动，会继续重试；API 保持可用以便前端展示状态。
        logger.error(f"浏览器首次启动失败，将在后台自动重试：{exc}")
    logger.info("后端服务已就绪")
    try:
        yield
    finally:
        from crawler.core.browser import close_browser
        await close_browser()
        logger.info("后端服务关闭")


app = FastAPI(
    title="抖音达人联络官",
    description="自动搜索、筛选达人，发送私信，管理后台展示",
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
    return {"status": "ok", "service": "抖音达人联络官"}


@app.post("/api/system/shutdown", include_in_schema=False)
async def prepare_desktop_shutdown(
    x_dyauth_shutdown_token: str | None = Header(default=None),
):
    """供桌面壳在退出前关闭浏览器；随后由桌面壳终止后端进程树。"""
    expected = os.environ.get("DYAUTH_SHUTDOWN_TOKEN", "")
    if (
        not expected
        or not x_dyauth_shutdown_token
        or not secrets.compare_digest(expected, x_dyauth_shutdown_token)
    ):
        # 不暴露关停端点是否可用，避免普通网页或其他本地进程关闭服务。
        raise HTTPException(status_code=404, detail="Not Found")

    from crawler.core.browser import close_browser

    await close_browser()
    logger.info("已收到桌面应用退出通知，浏览器资源释放完成")
    return {"status": "ready"}
