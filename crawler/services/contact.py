"""按达人协调私信与评论，避免两种联系方式分成两个整批执行。"""

import asyncio
import random
from collections.abc import Callable

from loguru import logger

from crawler.services.comment import CommentService, _get_daily_limit as comment_daily_limit
from crawler.services.comment import _get_interval as comment_interval
from crawler.services.dm import DMService, _get_daily_limit as dm_daily_limit
from crawler.services.dm import _get_interval as dm_interval
from crawler.services.search import KOLProfile
from crawler.utils.captcha import CaptchaBlockedError


ContactStats = dict[str, int]
ProgressCallback = Callable[
    [int, int, KOLProfile, str, str, ContactStats, ContactStats],
    None,
]


def _empty_stats() -> ContactStats:
    return {"sent": 0, "skipped_dup": 0, "skipped_limit": 0, "failed": 0}


class CombinedContactService:
    """对每位达人依次执行私信、评论，然后再处理下一位达人。"""

    def __init__(self, dm_service: DMService, comment_service: CommentService):
        self._dm = dm_service
        self._comment = comment_service

    async def send_batch(
        self,
        profiles: list[KOLProfile],
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, ContactStats]:
        dm_stats = _empty_stats()
        comment_stats = _empty_stats()

        # 批内先去重，防止相同账号在一次任务里重复触发两种联系方式。
        unique_profiles: list[KOLProfile] = []
        seen_user_ids: set[str] = set()
        for profile in profiles:
            if profile.sec_user_id in seen_user_ids:
                dm_stats["skipped_dup"] += 1
                comment_stats["skipped_dup"] += 1
                continue
            seen_user_ids.add(profile.sec_user_id)
            unique_profiles.append(profile)

        dm_slots = max(0, dm_daily_limit() - self._dm._record.today_count())
        comment_slots = max(
            0,
            comment_daily_limit() - self._comment._record.today_count(),
        )

        # 预先分配当日剩余额度，使失败重试不会在同一批次越过原有日配额。
        plans: list[tuple[KOLProfile, str, str]] = []
        for profile in unique_profiles:
            if self._dm._record.already_sent(profile.sec_user_id):
                dm_action = "duplicate"
            elif dm_slots > 0:
                dm_action = "send"
                dm_slots -= 1
            else:
                dm_action = "limit"

            if self._comment._record.already_commented(profile.sec_user_id):
                comment_action = "duplicate"
            elif comment_slots > 0:
                comment_action = "send"
                comment_slots -= 1
            else:
                comment_action = "limit"

            plans.append((profile, dm_action, comment_action))

        total = len(plans)
        dm_interval_min, dm_interval_max = dm_interval()
        comment_interval_min, comment_interval_max = comment_interval()

        for index, (profile, dm_action, comment_action) in enumerate(plans, start=1):
            nickname = profile.nickname or profile.uid or profile.sec_user_id
            dm_status = await self._run_dm(profile, dm_action, dm_stats)
            self._notify(
                on_progress,
                index,
                total,
                profile,
                "dm",
                dm_status,
                dm_stats,
                comment_stats,
            )

            # _send_one 已在主页响应或 DOM 中缓存 latest_aweme_id；评论阶段会
            # 直接进入该作品页，不会再次打开同一位达人的主页。
            comment_status = await self._run_comment(
                profile,
                comment_action,
                comment_stats,
            )
            self._notify(
                on_progress,
                index,
                total,
                profile,
                "comment",
                comment_status,
                dm_stats,
                comment_stats,
            )

            logger.info(
                f"[联系 {index}/{total}] {nickname}："
                f"私信={dm_status}，评论={comment_status}"
            )

            # 仅当本轮确实成功联系、且后面还有待执行动作时等待。两种渠道都
            # 成功时取较长间隔，同时满足私信和评论各自的风控配置。
            if self._has_later_action(plans, index):
                waits: list[int] = []
                if dm_status == "sent":
                    waits.append(random.randint(dm_interval_min, dm_interval_max))
                if comment_status == "sent":
                    waits.append(random.randint(comment_interval_min, comment_interval_max))
                if waits:
                    wait = max(waits)
                    logger.debug(f"[联系] 等待 {wait}s 后处理下一位达人...")
                    await asyncio.sleep(wait)

        logger.info(
            "私信+评论批量完成："
            f"私信 {dm_stats}，评论 {comment_stats}"
        )
        return {"dm": dm_stats, "comment": comment_stats}

    async def _run_dm(
        self,
        profile: KOLProfile,
        action: str,
        stats: ContactStats,
    ) -> str:
        if action == "duplicate":
            stats["skipped_dup"] += 1
            return "duplicate"
        if action == "limit":
            stats["skipped_limit"] += 1
            return "limit"

        try:
            success = await self._dm._send_one(profile)
        except CaptchaBlockedError:
            raise
        except Exception as exc:
            logger.exception(f"私信异常，继续处理评论：{exc}")
            success = False
        if success:
            self._dm._record.record(profile.sec_user_id, profile.nickname)
            stats["sent"] += 1
            return "sent"
        stats["failed"] += 1
        return "failed"

    async def _run_comment(
        self,
        profile: KOLProfile,
        action: str,
        stats: ContactStats,
    ) -> str:
        if action == "duplicate":
            stats["skipped_dup"] += 1
            return "duplicate"
        if action == "limit":
            stats["skipped_limit"] += 1
            return "limit"

        try:
            aweme_id, success = await self._comment._comment_one(profile)
        except CaptchaBlockedError:
            raise
        except Exception as exc:
            logger.exception(f"评论异常，继续处理下一位达人：{exc}")
            aweme_id, success = "", False
        if success:
            self._comment._record.record(
                profile.sec_user_id,
                profile.nickname,
                aweme_id,
            )
            stats["sent"] += 1
            return "sent"
        stats["failed"] += 1
        return "failed"

    @staticmethod
    def _has_later_action(
        plans: list[tuple[KOLProfile, str, str]],
        processed_count: int,
    ) -> bool:
        return any(
            dm_action == "send" or comment_action == "send"
            for _, dm_action, comment_action in plans[processed_count:]
        )

    @staticmethod
    def _notify(
        callback: ProgressCallback | None,
        index: int,
        total: int,
        profile: KOLProfile,
        stage: str,
        status: str,
        dm_stats: ContactStats,
        comment_stats: ContactStats,
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                index,
                total,
                profile,
                stage,
                status,
                dm_stats.copy(),
                comment_stats.copy(),
            )
        except Exception as exc:
            logger.debug(f"联系进度回调失败（忽略）：{exc}")
