"""
评论联系服务
策略：
  1. 访问达人主页，拦截 XHR 获取最新一条作品的 aweme_id
  2. 导航到该作品详情页（/video/{aweme_id}）
  3. 找到评论输入框，输入模板内容，点击发送
  4. 记录已评论，避免重复，控制每日频率
"""
import asyncio
import json
import random
import time
from datetime import date
from pathlib import Path

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeout

from crawler.services.search import KOLProfile
from crawler.services.browser_actions import (
    editor_text,
    fill_editor,
    find_first_visible,
    first_video_id,
    render_single_line,
)
from crawler.utils.config import get, ROOT_DIR

# 仅路径保持模块级常量
SENT_FILE: Path = ROOT_DIR / get("comment.sent_record_file", "data/comment_sent.json")
POST_LIST_API   = "/aweme/v1/web/aweme/post/"


async def _fill_input(el, text: str, page):
    """兼容旧调用点，实际使用整段快速填充。"""
    await fill_editor(el, text, page)


def _get_template() -> str:
    from backend.models.config_store import get_runtime
    return get_runtime("comment.template", get("comment.template",
        "你好 {nickname}，看了您的内容非常专业，想和您洽谈品牌合作，有意向请私信我！"))


def _get_daily_limit() -> int:
    from backend.models.config_store import get_runtime
    return int(get_runtime("comment.daily_limit", int(get("comment.daily_limit", 30))))


def _get_interval() -> tuple[int, int]:
    from backend.models.config_store import get_runtime
    return (
        int(get_runtime("comment.interval_min", int(get("comment.interval_min", 45)))),
        int(get_runtime("comment.interval_max", int(get("comment.interval_max", 120)))),
    )


# ── 已评论记录 ────────────────────────────────────────────────────────────────

class CommentRecord:
    """持久化已评论记录，防止重复，按日计数"""

    def __init__(self, path: Path = SENT_FILE):
        self._path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"commented_users": {}, "daily_count": {}}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def already_commented(self, sec_user_id: str) -> bool:
        return sec_user_id in self._data.get("commented_users", {})

    def today_count(self) -> int:
        today = str(date.today())
        return self._data.get("daily_count", {}).get(today, 0)

    def record(self, sec_user_id: str, nickname: str, aweme_id: str):
        today = str(date.today())
        self._data.setdefault("commented_users", {})[sec_user_id] = {
            "nickname": nickname,
            "aweme_id": aweme_id,
            "commented_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        counts = self._data.setdefault("daily_count", {})
        counts[today] = counts.get(today, 0) + 1
        self._save()


# ── 评论服务 ──────────────────────────────────────────────────────────────────

class CommentService:
    """通过浏览器自动化在达人最新作品下发评论"""

    def __init__(self, page: Page):
        self._page = page
        self._record = CommentRecord()

    async def send_batch(self, profiles: list[KOLProfile]) -> dict:
        """
        批量评论。
        返回统计：{"sent": n, "skipped_dup": n, "skipped_limit": n, "failed": n}
        """
        stats = {"sent": 0, "skipped_dup": 0, "skipped_limit": 0, "failed": 0}
        daily_limit = _get_daily_limit()
        interval_min, interval_max = _get_interval()

        candidates: list[KOLProfile] = []
        seen_user_ids: set[str] = set()
        for profile in profiles:
            if (
                profile.sec_user_id in seen_user_ids
                or self._record.already_commented(profile.sec_user_id)
            ):
                logger.debug(f"已评论过「{profile.nickname}」，跳过")
                stats["skipped_dup"] += 1
                continue
            seen_user_ids.add(profile.sec_user_id)
            candidates.append(profile)

        remaining = max(0, daily_limit - self._record.today_count())
        targets = candidates[:remaining]
        stats["skipped_limit"] = len(candidates) - len(targets)
        if stats["skipped_limit"]:
            logger.warning(
                f"今日评论额度仅剩 {remaining} 条，"
                f"另有 {stats['skipped_limit']} 条因达到上限跳过"
            )

        for index, profile in enumerate(targets):
            aweme_id, ok = await self._comment_one(profile)
            if ok:
                self._record.record(profile.sec_user_id, profile.nickname, aweme_id)
                stats["sent"] += 1
                logger.info(
                    f"✅ 已评论 → 「{profile.nickname}」作品 {aweme_id}"
                    f"（今日 {self._record.today_count()}/{daily_limit}）"
                )
                # 最后一条完成后无需再等待一个发送间隔。
                if index < len(targets) - 1:
                    await asyncio.sleep(random.randint(interval_min, interval_max))
            else:
                stats["failed"] += 1

        logger.info(
            f"评论批量完成：发送 {stats['sent']} / 跳过重复 {stats['skipped_dup']} / "
            f"达限 {stats['skipped_limit']} / 失败 {stats['failed']}"
        )
        return stats

    # ── 获取最新作品 ID ───────────────────────────────────────────────────────

    async def _get_latest_aweme_id(self, sec_user_id: str) -> tuple[str, str]:
        """
        访问达人主页，拦截 /aweme/v1/web/aweme/post/ 接口获取最新作品 ID。
        """
        page = self._page
        result: list[str] = []
        ip_result: list[str] = []

        async def on_response(response):
            # 捕获最新作品 ID
            if POST_LIST_API in response.url and response.status == 200:
                try:
                    body = await response.body()
                    data = json.loads(body)
                    aweme_list = data.get("aweme_list") or []
                    if aweme_list:
                        aweme_id = aweme_list[0].get("aweme_id", "")
                        if aweme_id:
                            result.append(str(aweme_id))
                except Exception as e:
                    logger.debug(f"解析作品列表失败: {e}")
                return

            # 顺带捕获 IP 属地（路过就拿）
            if "/aweme/v1/web/user/profile/other/" in response.url and response.status == 200:
                try:
                    body = await response.body()
                    data = json.loads(body)
                    user = data.get("user") or {}
                    ip = user.get("ip_location") or user.get("province") or ""
                    city = user.get("city") or ""
                    if ip and city and city not in ip:
                        ip = f"{ip} {city}"
                    if ip.strip():
                        ip_result.append(ip.strip())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            profile_url = f"https://www.douyin.com/user/{sec_user_id}"
            current_url = page.url.split("?", 1)[0].rstrip("/")
            if current_url != profile_url.rstrip("/"):
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=25_000)

            # 接口可能返回空 aweme_list（本次 js-reverse 实测即为此情况），
            # 因此同时读取主页已经渲染出的 /video/{id} 链接作为可靠兜底。
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and not result:
                dom_aweme_id = await first_video_id(page)
                if dom_aweme_id:
                    result.append(dom_aweme_id)
                    break
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.debug(f"访问主页失败：{e}")
        finally:
            page.remove_listener("response", on_response)

        return result[0] if result else "", ip_result[0] if ip_result else ""

    # ── 发布评论 ──────────────────────────────────────────────────────────────

    async def _comment_one(self, profile: KOLProfile) -> tuple[str, bool]:
        """
        对单个达人的最新作品发评论。
        基于 js-reverse 分析结论：
        - 评论输入框是 div.notranslate.public-DraftEditor-content（Draft.js）
        - 激活方式：点击 comment-input-inner-container 占位容器
        - 输入方式：keyboard.insert_text 整段输入
        - 发送方式：点击发布或按 Enter，触发 POST /aweme/v1/web/comment/publish
        返回 (aweme_id, success)
        """
        page = self._page
        nickname = profile.nickname or profile.uid

        started = time.monotonic()

        # Step 1：优先复用搜索/私信阶段已经拿到的作品 ID；仅缺失时访问主页。
        aweme_id = profile.latest_aweme_id
        ip = ""
        if not aweme_id:
            aweme_id, ip = await self._get_latest_aweme_id(profile.sec_user_id)
            profile.latest_aweme_id = aweme_id
        if ip and not profile.ip_location:
            profile.ip_location = ip
            logger.info(f"[评论] 顺带采集 IP：{nickname} → {ip}")
        if not aweme_id:
            logger.warning(f"「{nickname}」未获取到最新作品，跳过评论")
            return "", False

        # Step 2：导航到作品页
        video_url = f"https://www.douyin.com/video/{aweme_id}"
        logger.info(f"[评论] 导航到作品页：{video_url}")
        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=25_000)
        except Exception as e:
            logger.warning(f"「{nickname}」导航到作品页失败：{e}")
            return aweme_id, False

        # Step 3：构造评论文本
        comment_text = render_single_line(_get_template(), nickname)
        logger.info(f"[评论] 准备发送（{len(comment_text)} 字）：{comment_text[:50]}...")

        # Step 4：找到评论输入框；新版页面需先点击占位容器完成激活。
        direct_input_selectors = [
            'input[placeholder*="留下你的精彩评论吧"]',
            'input[placeholder*="友善评论"]',
            'input[placeholder*="评论"]',
            'input[placeholder*="说点什么"]',
            '[data-e2e="comment-input"]',
            '[class*="commentInput"] input',
            '[class*="comment-input"] input',
            'div[contenteditable="true"].public-DraftEditor-content',
            'div[contenteditable="true"][class*="DraftEditor-content"]',
        ]

        # 新版页面初始只有一个“留下你的精彩评论吧”占位容器，点击后才创建
        # Draft.js contenteditable。先做一次即时探测，再等待并激活占位容器。
        input_el = await find_first_visible(page, direct_input_selectors, timeout=0.2)
        if input_el is None:
            placeholder = await find_first_visible(page, [
                'div.comment-input-inner-container',
                '[class*="comment-input-inner-container"]',
                '[data-e2e="comment-input-placeholder"]',
            ], timeout=10.0)
            if placeholder is not None:
                await placeholder.click()
                input_el = await find_first_visible(page, direct_input_selectors, timeout=3.0)

        if input_el is None:
            logger.warning(f"「{nickname}」未找到评论输入框")
            return aweme_id, False

        logger.info("[评论] 已激活评论输入框")

        # Step 5：整段填充，避免 humanize="careful" 将逐字输入放大到几十秒。
        await _fill_input(input_el, comment_text, page)
        logger.info("[评论] 输入完成")

        # Step 6：点击发送按钮（截图确认：输入后右侧出现「发布」按钮）
        send_selectors = [
            'button[data-e2e="comment-submit"]',
            'button:has-text("发布")',
            'button:has-text("发送")',
            '[class*="submit"]',
            '[class*="btn-send"]',
        ]

        try:
            async with page.expect_response(
                lambda response: "/aweme/v1/web/comment/publish" in response.url,
                timeout=10_000,
            ) as response_info:
                send_btn = await find_first_visible(page, send_selectors, timeout=0.8)
                if send_btn is not None and await send_btn.is_enabled():
                    await send_btn.click()
                    logger.info("[评论] 点击发布按钮")
                else:
                    await input_el.press("Enter")
                    logger.info("[评论] Enter 键发送")

            response = await response_info.value
            if not response.ok:
                logger.warning(f"「{nickname}」评论接口返回 HTTP {response.status}")
                return aweme_id, False
            try:
                body = await response.json()
                if body.get("status_code", 0) != 0:
                    logger.warning(
                        f"「{nickname}」评论接口业务码异常："
                        f"{body.get('status_code')} {body.get('status_msg', '')}"
                    )
                    return aweme_id, False
            except Exception:
                # 非 JSON 响应仍以 HTTP 成功为准。
                pass
        except PWTimeout:
            if (await editor_text(input_el)).strip():
                logger.warning(f"「{nickname}」未捕获到评论发布响应")
                return aweme_id, False
            logger.warning(f"「{nickname}」发布响应未捕获，但输入框已清空，按成功处理")

        logger.info(f"[评论] 单条操作耗时 {time.monotonic() - started:.2f}s")
        return aweme_id, True
