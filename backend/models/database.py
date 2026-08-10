"""
数据库模型与连接管理（SQLAlchemy 2 + aiosqlite 异步）
"""
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import (
    Boolean, DateTime, Integer, String, Text, event, func
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from crawler.utils.config import get, ROOT_DIR

# ── 数据库路径 ─────────────────────────────────────────────────────────────────

DB_PATH = ROOT_DIR / get("database.path", "data/douyin.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# ── 引擎：开启 WAL 模式，设置 busy_timeout，解决多连接写锁 ────────────────────

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # pool_size=1 避免多个连接同时持有写锁
    # 对于 SQLite aiosqlite，连接复用即可
    connect_args={
        "check_same_thread": False,
        "timeout": 30,          # 等待锁最多 30 秒再抛出，而不是立即失败
    },
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    """每次连接后开启 WAL 模式，大幅减少读写锁冲突"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")  # 30s
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ─── ORM 基类 ─────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── 达人表 ───────────────────────────────────────────────────────────────────

class KOL(Base):
    """带货达人主表"""
    __tablename__ = "kols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 抖音标识
    sec_user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    uid: Mapped[str]         = mapped_column(String(64), nullable=True)
    short_id: Mapped[str]    = mapped_column(String(64), nullable=True)   # 抖音号

    # 基础信息
    nickname: Mapped[str]    = mapped_column(String(128), nullable=True)
    signature: Mapped[str]   = mapped_column(Text, nullable=True)         # 个人简介
    avatar_url: Mapped[str]  = mapped_column(Text, nullable=True)

    # 数据指标
    follower_count: Mapped[int]    = mapped_column(Integer, default=0)    # 粉丝数
    following_count: Mapped[int]   = mapped_column(Integer, default=0)    # 关注数
    total_favorited: Mapped[int]   = mapped_column(Integer, default=0)    # 总获赞
    aweme_count: Mapped[int]       = mapped_column(Integer, default=0)    # 作品数

    # 带货相关
    is_star: Mapped[bool]          = mapped_column(Boolean, default=False)
    commerce_user_level: Mapped[int] = mapped_column(Integer, default=0)  # 带货等级
    has_shop: Mapped[bool]         = mapped_column(Boolean, default=False) # 是否开橱窗

    # 链接
    profile_url: Mapped[str]       = mapped_column(Text, nullable=True)   # 主页链接

    # IP 属地
    ip_location: Mapped[str]       = mapped_column(String(64), nullable=True, default="")  # 如「广东」「北京」

    # 来源 & 状态
    search_keyword: Mapped[str]    = mapped_column(String(128), nullable=True)
    dm_status: Mapped[str]         = mapped_column(String(32), default="pending")
    # pending=待发送, sent=已发送, skip=跳过, failed=失败

    dm_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 评论状态
    comment_status: Mapped[str]        = mapped_column(String(32), default="pending")
    # pending=待评论, sent=已评论, skip=跳过, failed=失败
    comment_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    collected_at: Mapped[datetime]     = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime]       = mapped_column(DateTime, default=func.now(), onupdate=func.now())


# ─── 私信日志表 ───────────────────────────────────────────────────────────────

class DMLog(Base):
    """私信发送日志"""
    __tablename__ = "dm_logs"

    id: Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    sec_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    nickname: Mapped[str]    = mapped_column(String(128), nullable=True)
    message: Mapped[str]     = mapped_column(Text, nullable=True)
    status: Mapped[str]      = mapped_column(String(32), default="sent")   # sent / failed
    error_msg: Mapped[str]   = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ─── 系统配置表 ───────────────────────────────────────────────────────────────

class SystemConfig(Base):
    """
    系统运行时配置覆盖表。
    key/value 形式存储，前端修改后写入此表，优先级高于 settings.yaml。
    """
    __tablename__ = "system_config"

    key: Mapped[str]   = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


# ─── 任务日志表 ───────────────────────────────────────────────────────────────

class TaskLog(Base):
    """采集任务执行日志"""
    __tablename__ = "task_logs"

    id: Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str]     = mapped_column(String(64))                # search / dm / filter
    keywords: Mapped[str]      = mapped_column(Text, nullable=True)
    total_found: Mapped[int]   = mapped_column(Integer, default=0)
    total_passed: Mapped[int]  = mapped_column(Integer, default=0)
    total_sent: Mapped[int]    = mapped_column(Integer, default=0)
    status: Mapped[str]        = mapped_column(String(32), default="running")
    error_msg: Mapped[str]     = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime]  = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

async def init_db():
    """初始化数据库，创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from loguru import logger
    logger.info(f"数据库已初始化：{DB_PATH}")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用的 Session 生成器"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
