"""
数据库 CRUD 操作层
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import KOL, DMLog, TaskLog
from crawler.services.search import KOLProfile


# ─── KOL CRUD ─────────────────────────────────────────────────────────────────

async def upsert_kol(session: AsyncSession, profile: KOLProfile) -> KOL:
    """
    插入或更新达人记录（按 sec_user_id upsert）。
    已存在的记录更新数据指标，不覆盖 dm_status。
    """
    stmt = sqlite_insert(KOL).values(
        sec_user_id        = profile.sec_user_id,
        uid                = profile.uid,
        short_id           = profile.short_id,
        nickname           = profile.nickname,
        signature          = profile.signature,
        avatar_url         = profile.avatar_url,
        follower_count     = profile.follower_count,
        following_count    = profile.following_count,
        total_favorited    = profile.total_favorited,
        aweme_count        = profile.aweme_count,
        is_star            = profile.is_star,
        commerce_user_level= profile.commerce_user_level,
        has_shop           = profile.has_shop,
        profile_url        = profile.profile_url,
        ip_location        = profile.ip_location,
        search_keyword     = profile.search_keyword,
        collected_at       = datetime.fromisoformat(profile.collected_at) if profile.collected_at else datetime.now(),
    ).on_conflict_do_update(
        index_elements=["sec_user_id"],
        set_={
            "nickname":           profile.nickname,
            "signature":          profile.signature,
            "avatar_url":         profile.avatar_url,
            "follower_count":     profile.follower_count,
            "following_count":    profile.following_count,
            "total_favorited":    profile.total_favorited,
            "aweme_count":        profile.aweme_count,
            "has_shop":           profile.has_shop,
            "commerce_user_level":profile.commerce_user_level,
            "ip_location":        profile.ip_location,
            "updated_at":         datetime.now(),
        }
    )
    await session.execute(stmt)
    await session.flush()

    result = await session.execute(
        select(KOL).where(KOL.sec_user_id == profile.sec_user_id)
    )
    return result.scalar_one()


async def get_kols(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    dm_status: Optional[str] = None,
    keyword: Optional[str] = None,
    min_followers: Optional[int] = None,
) -> tuple[int, list[KOL]]:
    """分页查询达人列表，返回 (total, items)"""
    q = select(KOL)

    if dm_status:
        q = q.where(KOL.dm_status == dm_status)
    if keyword:
        q = q.where(KOL.nickname.ilike(f"%{keyword}%"))
    if min_followers is not None:
        q = q.where(KOL.follower_count >= min_followers)

    # 总数
    count_q = select(func.count()).select_from(q.subquery())
    total = (await session.execute(count_q)).scalar_one()

    # 分页
    q = q.order_by(KOL.follower_count.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    items = (await session.execute(q)).scalars().all()

    return total, list(items)


async def get_kol_by_id(session: AsyncSession, kol_id: int) -> Optional[KOL]:
    return await session.get(KOL, kol_id)


async def get_kol_by_sec_uid(session: AsyncSession, sec_user_id: str) -> Optional[KOL]:
    result = await session.execute(
        select(KOL).where(KOL.sec_user_id == sec_user_id)
    )
    return result.scalar_one_or_none()


async def update_kol_dm_status(
    session: AsyncSession,
    sec_user_id: str,
    status: str,
    sent_at: Optional[datetime] = None,
):
    values: dict = {"dm_status": status}
    if sent_at:
        values["dm_sent_at"] = sent_at
    await session.execute(
        update(KOL).where(KOL.sec_user_id == sec_user_id).values(**values)
    )


async def update_kol_comment_status(
    session: AsyncSession,
    sec_user_id: str,
    status: str,
    sent_at: Optional[datetime] = None,
):
    values: dict = {"comment_status": status}
    if sent_at:
        values["comment_sent_at"] = sent_at
    await session.execute(
        update(KOL).where(KOL.sec_user_id == sec_user_id).values(**values)
    )


async def get_kols_by_dm_status(
    session: AsyncSession,
    status: str = "pending",
    limit: int = 50,
) -> list[KOL]:
    result = await session.execute(
        select(KOL)
        .where(KOL.dm_status == status)
        .order_by(KOL.follower_count.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ─── DMLog CRUD ───────────────────────────────────────────────────────────────

async def create_dm_log(
    session: AsyncSession,
    sec_user_id: str,
    nickname: str,
    message: str,
    status: str = "sent",
    error_msg: str = "",
) -> DMLog:
    log = DMLog(
        sec_user_id=sec_user_id,
        nickname=nickname,
        message=message,
        status=status,
        error_msg=error_msg,
    )
    session.add(log)
    await session.flush()
    return log


# ─── TaskLog CRUD ─────────────────────────────────────────────────────────────

async def create_task_log(
    session: AsyncSession,
    task_type: str,
    keywords: list[str],
) -> TaskLog:
    task = TaskLog(
        task_type=task_type,
        keywords=",".join(keywords),
    )
    session.add(task)
    await session.flush()
    return task


async def finish_task_log(
    session: AsyncSession,
    task_id: int,
    total_found: int,
    total_passed: int,
    total_sent: int,
    status: str = "done",
    error_msg: str = "",
):
    await session.execute(
        update(TaskLog)
        .where(TaskLog.id == task_id)
        .values(
            total_found=total_found,
            total_passed=total_passed,
            total_sent=total_sent,
            status=status,
            error_msg=error_msg,
            finished_at=datetime.now(),
        )
    )
