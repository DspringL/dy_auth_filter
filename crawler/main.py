"""
爬虫主入口（独立运行模式，不依赖 FastAPI）
用法：
  python -m crawler.main --keywords "美妆带货" "零食推荐" --send-dm
  python -m crawler.main --help
"""
import asyncio
import argparse
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from crawler.utils.logger import setup_logger
from crawler.utils.config import get
from crawler.core.browser import get_browser, close_browser
from crawler.core.login import ensure_login
from crawler.core.sign import SignatureHelper
from crawler.services.search import DouyinSearchService
from crawler.services.filter import KOLFilter, FilterConfig
from crawler.services.dm import DMService
from backend.models.database import init_db, AsyncSessionLocal
from backend.models.crud import upsert_kol, update_kol_dm_status, create_task_log, finish_task_log
from datetime import datetime


async def run(
    keywords: list[str],
    min_followers: int = None,
    max_followers: int = None,
    min_total_likes: int = None,
    require_shop: bool = None,
    send_dm: bool = True,
    max_per_keyword: int = None,
):
    """完整采集流程：登录 → 搜索 → 筛选 → 入库 → 私信"""

    # ── 初始化数据库 ─────────────────────────────────────────────────────────
    await init_db()

    # ── 启动浏览器 ───────────────────────────────────────────────────────────
    manager = await get_browser()

    # ── 登录检测 ─────────────────────────────────────────────────────────────
    logged_in = await ensure_login(manager)
    if not logged_in:
        logger.error("登录失败，流程中止")
        await close_browser()
        return

    # ── 打开抖音页（用于签名） ────────────────────────────────────────────────
    page = await manager.new_page()
    await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    async with AsyncSessionLocal() as session:
        task = await create_task_log(session, "cli", keywords)
        await session.commit()
        task_id = task.id

    # ── 搜索 ─────────────────────────────────────────────────────────────────
    search_svc = DouyinSearchService(page)
    max_count = max_per_keyword or int(get("search.max_users_per_keyword", 50))
    raw_profiles = await search_svc.batch_search(keywords, max_count)
    logger.info(f"搜索完成，共 {len(raw_profiles)} 位用户")

    # ── 筛选 ─────────────────────────────────────────────────────────────────
    cfg = FilterConfig(
        min_followers   = min_followers   if min_followers   is not None else int(get("filter.min_followers", 0)),
        max_followers   = max_followers   if max_followers   is not None else int(get("filter.max_followers", 0)),
        min_total_likes = min_total_likes if min_total_likes is not None else int(get("filter.min_total_likes", 0)),
        require_shop    = require_shop    if require_shop    is not None else bool(get("filter.require_shop_window", False)),
    )
    kol_filter = KOLFilter(cfg)
    passed = kol_filter.apply(raw_profiles)
    passed = kol_filter.sort_by_popularity(passed)
    logger.info(f"筛选后：{len(passed)} 位达人")

    # ── 入库 ─────────────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        for profile in passed:
            await upsert_kol(session, profile)
        await session.commit()
    logger.info(f"已将 {len(passed)} 位达人写入数据库")

    # ── 私信 ─────────────────────────────────────────────────────────────────
    sent_count = 0
    if send_dm and passed:
        dm_svc = DMService(page)
        result = await dm_svc.send_batch(passed)
        sent_count = result.get("sent", 0)

        async with AsyncSessionLocal() as session:
            for profile in passed:
                if dm_svc._record.already_sent(profile.sec_user_id):
                    await update_kol_dm_status(session, profile.sec_user_id, "sent", datetime.now())
            await session.commit()

    # ── 结束 ─────────────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        await finish_task_log(session, task_id, len(raw_profiles), len(passed), sent_count, "done")
        await session.commit()

    await search_svc.close()
    await page.close()

    logger.info(
        f"\n{'='*50}\n"
        f"任务完成！\n"
        f"  搜索到：{len(raw_profiles)} 位\n"
        f"  通过筛选：{len(passed)} 位\n"
        f"  发私信：{sent_count} 条\n"
        f"{'='*50}"
    )


def parse_args():
    parser = argparse.ArgumentParser(description="抖音带货达人采集工具")
    parser.add_argument("--keywords", nargs="+", default=None, help="搜索关键词（空格分隔多个）")
    parser.add_argument("--min-followers", type=int, default=None, help="粉丝下限（万）")
    parser.add_argument("--max-followers", type=int, default=None, help="粉丝上限（万）")
    parser.add_argument("--min-likes", type=int, default=None, help="总点赞下限")
    parser.add_argument("--require-shop", action="store_true", help="只要开通橱窗的达人")
    parser.add_argument("--no-dm", action="store_true", help="不发送私信，只采集入库")
    parser.add_argument("--max-per-keyword", type=int, default=None, help="每关键词最多采集数量")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    keywords = args.keywords or get("search.keywords", ["美妆带货"])
    if isinstance(keywords, str):
        keywords = [keywords]

    asyncio.run(run(
        keywords        = keywords,
        min_followers   = args.min_followers,
        max_followers   = args.max_followers,
        min_total_likes = args.min_likes,
        require_shop    = args.require_shop if args.require_shop else None,
        send_dm         = not args.no_dm,
        max_per_keyword = args.max_per_keyword,
    ))
