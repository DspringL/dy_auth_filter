"""
系统配置持久化读写层
优先级：数据库覆盖 > settings.yaml 默认值
"""
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import SystemConfig
from crawler.utils.config import get as yaml_get

# ── 配置 key 定义 ──────────────────────────────────────────────────────────────
# 每个 key 对应 settings.yaml 中的路径（用于降级读取默认值）

CONFIG_KEYS: dict[str, dict] = {
    # 筛选规则（粉丝数/点赞数均为实际数量，不再以万为单位）
    "filter.min_followers":    {"yaml": "filter.min_followers",    "type": "int",   "default": 10000},
    "filter.max_followers":    {"yaml": "filter.max_followers",    "type": "int",   "default": 0},
    "filter.min_total_likes":  {"yaml": "filter.min_total_likes",  "type": "int",   "default": 10000},
    "filter.require_shop":     {"yaml": "filter.require_shop_window", "type": "bool", "default": False},
    "filter.min_videos":       {"yaml": "filter.min_recent_videos","type": "int",   "default": 0},

    # 私信配置
    "dm.template":             {"yaml": "dm.template",             "type": "str",   "default": "你好 {nickname}，我是某某品牌的商务负责人，看了您的内容非常专业，想和您洽谈品牌合作事宜。有意向的话请留下联系方式，期待合作！"},
    "dm.daily_limit":          {"yaml": "dm.daily_limit",          "type": "int",   "default": 20},
    "dm.interval_min":         {"yaml": "dm.interval_min",         "type": "int",   "default": 60},
    "dm.interval_max":         {"yaml": "dm.interval_max",         "type": "int",   "default": 180},

    # 评论配置
    "comment.template":        {"yaml": "comment.template",        "type": "str",   "default": "你好 {nickname}，看了您的内容非常专业，想和您洽谈品牌合作，有意向请私信我！"},
    "comment.daily_limit":     {"yaml": "comment.daily_limit",     "type": "int",   "default": 30},
    "comment.interval_min":    {"yaml": "comment.interval_min",    "type": "int",   "default": 45},
    "comment.interval_max":    {"yaml": "comment.interval_max",    "type": "int",   "default": 120},
}


def _yaml_default(key: str) -> Any:
    """从 settings.yaml 读取默认值，读不到则用 CONFIG_KEYS 中的 default"""
    meta = CONFIG_KEYS.get(key, {})
    raw = yaml_get(meta.get("yaml", key))
    if raw is None:
        raw = meta.get("default")
    # 类型转换
    t = meta.get("type", "str")
    if t == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return meta.get("default", 0)
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("true", "1", "yes")
    return str(raw) if raw is not None else meta.get("default", "")


def _coerce(key: str, value: str) -> Any:
    """将数据库中存储的字符串转为正确的 Python 类型"""
    t = CONFIG_KEYS.get(key, {}).get("type", "str")
    if t == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    if t == "bool":
        return value.lower() in ("true", "1", "yes")
    return value


# ── 读取所有配置（合并 yaml 默认值 + DB 覆盖） ────────────────────────────────

async def get_all_config(session: AsyncSession) -> dict[str, Any]:
    """返回完整配置字典（DB 覆盖 > yaml 默认）"""
    # 先从 yaml 构建默认值
    result = {k: _yaml_default(k) for k in CONFIG_KEYS}

    # 用 DB 中的值覆盖
    rows = (await session.execute(select(SystemConfig))).scalars().all()
    for row in rows:
        if row.key in CONFIG_KEYS:
            result[row.key] = _coerce(row.key, row.value)

    return result


async def get_config_value(session: AsyncSession, key: str) -> Any:
    """读取单个配置项（DB 优先，降级 yaml）"""
    row = await session.get(SystemConfig, key)
    if row is not None:
        return _coerce(key, row.value)
    return _yaml_default(key)


# ── 保存配置 ──────────────────────────────────────────────────────────────────

async def set_config_values(session: AsyncSession, updates: dict[str, Any]):
    """
    批量写入配置，仅允许 CONFIG_KEYS 中定义的 key。
    值统一转为字符串存储。
    """
    from datetime import datetime
    for key, value in updates.items():
        if key not in CONFIG_KEYS:
            continue
        stmt = sqlite_insert(SystemConfig).values(
            key=key,
            value=str(value),
            updated_at=datetime.now(),
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": str(value), "updated_at": datetime.now()},
        )
        await session.execute(stmt)
    await session.flush()


# ── 便捷函数：同步读取（用于爬虫任务内，不走 async session） ─────────────────

_config_cache: dict[str, Any] | None = None


async def load_config_cache(session: AsyncSession):
    """在任务启动时缓存一次配置，供同步代码直接访问"""
    global _config_cache
    _config_cache = await get_all_config(session)


def get_runtime(key: str, fallback=None) -> Any:
    """
    同步读取运行时配置（任务中使用）。
    需要在任务启动前调用过 load_config_cache()。
    降级顺序：内存缓存 > yaml 默认 > fallback
    """
    if _config_cache is not None and key in _config_cache:
        return _config_cache[key]
    val = _yaml_default(key)
    return val if val is not None else fallback
