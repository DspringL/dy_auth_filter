"""
私信发送服务
策略：
  - 通过 CloakBrowser 页面自动化操作（导航用户主页 → 点击「发私信」→ 输入模板 → 发送）
  - 每日发送上限 + 随机间隔防风控
  - 已发送记录持久化（避免重复发送）
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
from crawler.utils.captcha import (
    CaptchaBlockedError,
    CaptchaEventHandler,
    handle_captcha_if_present,
    latest_captcha_screenshot,
    navigate_with_captcha_guard,
)

# 仅 SENT_FILE 保持模块级常量（路径不会被前端修改）
SENT_FILE: Path = ROOT_DIR / get("dm.sent_record_file", "data/dm_sent.json")


async def _fill_input(el, text: str, page):
    """兼容旧调用点，实际使用整段快速填充。"""
    await fill_editor(el, text, page)


def _get_template() -> str:
    from backend.models.config_store import get_runtime
    return get_runtime("dm.template", get("dm.template",
        "你好 {nickname}，我是某某品牌商务负责人，"
        "看了您的内容非常专业，想和您洽谈品牌合作事宜。"
        "有意向的话请留下联系方式，期待合作！"))


def _get_daily_limit() -> int:
    from backend.models.config_store import get_runtime
    return int(get_runtime("dm.daily_limit", int(get("dm.daily_limit", 20))))


def _get_interval() -> tuple[int, int]:
    from backend.models.config_store import get_runtime
    return (
        int(get_runtime("dm.interval_min", int(get("dm.interval_min", 60)))),
        int(get_runtime("dm.interval_max", int(get("dm.interval_max", 180)))),
    )


# ─── 已发送记录管理 ────────────────────────────────────────────────────────────

class SentRecord:
    """持久化已发送私信记录，支持按日计数"""

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
        return {"sent_users": {}, "daily_count": {}}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def already_sent(self, sec_user_id: str) -> bool:
        return sec_user_id in self._data.get("sent_users", {})

    def today_count(self) -> int:
        today = str(date.today())
        return self._data.get("daily_count", {}).get(today, 0)

    def record(self, sec_user_id: str, nickname: str):
        today = str(date.today())
        self._data.setdefault("sent_users", {})[sec_user_id] = {
            "nickname": nickname,
            "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        counts = self._data.setdefault("daily_count", {})
        counts[today] = counts.get(today, 0) + 1
        self._save()


# ─── 私信发送服务 ──────────────────────────────────────────────────────────────

class DMService:
    """
    通过浏览器页面自动化发送私信。
    page 必须是已登录抖音的 CloakBrowser Page 对象。
    """

    def __init__(
        self,
        page: Page,
        captcha_event_handler: CaptchaEventHandler | None = None,
    ):
        self._page = page
        self._record = SentRecord()
        self._captcha_event_handler = captcha_event_handler

    async def _pause_for_captcha(self, reason: str) -> bool:
        return await handle_captcha_if_present(
            self._page,
            reason=reason,
            event_handler=getattr(self, "_captcha_event_handler", None),
        )

    async def send_batch(self, profiles: list[KOLProfile]) -> dict:
        """
        批量发送私信。
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
                or self._record.already_sent(profile.sec_user_id)
            ):
                logger.debug(f"已发送过「{profile.nickname}」，跳过")
                stats["skipped_dup"] += 1
                continue
            seen_user_ids.add(profile.sec_user_id)
            candidates.append(profile)

        remaining = max(0, daily_limit - self._record.today_count())
        targets = candidates[:remaining]
        stats["skipped_limit"] = len(candidates) - len(targets)
        if stats["skipped_limit"]:
            logger.warning(
                f"今日私信额度仅剩 {remaining} 条，"
                f"另有 {stats['skipped_limit']} 条因达到上限跳过"
            )

        for index, profile in enumerate(targets):
            ok = await self._send_one(profile)
            if ok:
                self._record.record(profile.sec_user_id, profile.nickname)
                stats["sent"] += 1
                logger.info(
                    f"✅ 已发送私信 → 「{profile.nickname}」"
                    f"（今日 {self._record.today_count()}/{daily_limit}）"
                )
                # 最后一条完成后立即返回，避免无意义地再等待 10~30 秒。
                if index < len(targets) - 1:
                    wait = random.randint(interval_min, interval_max)
                    logger.debug(f"等待 {wait}s 后发送下一条...")
                    await asyncio.sleep(wait)
            else:
                stats["failed"] += 1

        logger.info(
            f"私信批量发送完成："
            f"发送 {stats['sent']} / 跳过重复 {stats['skipped_dup']} / "
            f"达限 {stats['skipped_limit']} / 失败 {stats['failed']}"
        )
        return stats

    async def _send_one(self, profile: KOLProfile) -> bool:
        """
        发送单条私信。
        流程：导航用户主页 → 点击「发私信」按钮 → 输入文本 → 点击发送
        """
        page = self._page
        nickname = profile.nickname or profile.uid
        # 强制去掉换行符，确保整条模板作为一条消息发送
        # 抖音限制：对方回复前只能发一条，多行会被拆成多条导致失败
        message = render_single_line(_get_template(), nickname)
        logger.info(f"[DM] 准备发送（{len(message)} 字）：{message[:50]}...")
        profile_url = profile.profile_url

        if not profile_url:
            logger.warning(f"「{nickname}」无主页链接，跳过")
            return False

        profile_api = "/aweme/v1/web/user/profile/other/"
        post_api = "/aweme/v1/web/aweme/post/"

        async def on_profile_response(response):
            if response.status != 200:
                return
            try:
                if profile_api in response.url:
                    data = await response.json()
                    user = data.get("user") or {}
                    ip = user.get("ip_location") or user.get("province") or ""
                    city = user.get("city") or ""
                    if ip and city and city not in ip:
                        ip = f"{ip} {city}"
                    if ip.strip():
                        profile.ip_location = ip.strip()
                elif post_api in response.url and not profile.latest_aweme_id:
                    data = await response.json()
                    aweme_list = data.get("aweme_list") or []
                    if aweme_list:
                        profile.latest_aweme_id = str(aweme_list[0].get("aweme_id") or "")
            except Exception as exc:
                logger.debug(f"[DM] 解析主页响应失败（忽略）：{exc}")

        page.on("response", on_profile_response)
        started = time.monotonic()
        try:
            current_url = page.url.split("?", 1)[0].rstrip("/")
            if current_url != profile_url.split("?", 1)[0].rstrip("/"):
                logger.info(f"导航到主页：{profile_url}")
                ok = await navigate_with_captcha_guard(
                    page,
                    profile_url,
                    timeout=30_000,
                    event_handler=getattr(self, "_captcha_event_handler", None),
                )
                if not ok:
                    raise CaptchaBlockedError(
                        "私信主页被验证码阻止"
                        f"；截图：{latest_captcha_screenshot() or '未生成'}"
                    )
            else:
                logger.debug(f"[DM] 复用已打开的达人主页：{nickname}")

            # ── 点击「发私信」按钮 ───────────────────────────────────────────
            await self._pause_for_captcha("dm-before-button")
            dm_btn = await self._find_dm_button(page, timeout=8.0)
            if not profile.latest_aweme_id:
                profile.latest_aweme_id = await first_video_id(page)
            if profile.ip_location:
                logger.info(f"[DM] 顺带采集 IP：{nickname} → {profile.ip_location}")
            if dm_btn is None:
                logger.warning(f"「{nickname}」主页找不到私信按钮，跳过")
                return False

            await dm_btn.click()
            await self._pause_for_captcha("dm-after-button")

            # ── 输入消息 ─────────────────────────────────────────────────────
            input_selectors = [
                # 抖音 IM 输入框常见选择器（按优先级排列）
                '[data-e2e="chat-input"]',
                'div[contenteditable="true"][class*="chat"]',
                'div[contenteditable="true"][class*="input"]',
                'div[contenteditable="true"][class*="message"]',
                'div[contenteditable="true"][class*="msg"]',
                'div[contenteditable="true"][class*="im"]',
                'textarea[placeholder*="说点什么"]',
                'textarea[placeholder*="消息"]',
                'textarea[placeholder*="输入"]',
                # 通用：任意 contenteditable（最后兜底）
                'div[contenteditable="true"]',
                'textarea',
            ]
            input_box = await find_first_visible(page, input_selectors, timeout=10.0)

            if input_box is None:
                logger.warning(f"「{nickname}」找不到私信输入框")
                return False

            await _fill_input(input_box, message, page)
            # ── 点击发送 ─────────────────────────────────────────────────────
            send_selectors = [
                'button[data-e2e="chat-send-btn"]',
                'button:has-text("发送")',
                '[class*="send-btn"]',
                '[class*="sendBtn"]',
            ]
            try:
                await self._pause_for_captcha("dm-before-send")
                async with page.expect_response(
                    lambda response: "/v1/message/send" in response.url,
                    timeout=10_000,
                ) as response_info:
                    send_btn = await find_first_visible(page, send_selectors, timeout=0.8)
                    if send_btn is not None and await send_btn.is_enabled():
                        await send_btn.click()
                        logger.info("[DM] 点击发送按钮")
                    else:
                        await input_box.press("Enter")
                        logger.info("[DM] 用 Enter 键发送")

                response = await response_info.value
                if not response.ok:
                    logger.warning(f"「{nickname}」私信接口返回 HTTP {response.status}")
                    return False
            except PWTimeout:
                # 少数版本的 IM SDK 可能没有把响应暴露给 Page；输入框清空可作为
                # 已被 SDK 接收的保守兜底，避免误判失败后重复发送。
                if (await editor_text(input_box)).strip():
                    logger.warning(f"「{nickname}」未捕获到私信发送响应")
                    return False
                logger.warning(f"「{nickname}」发送响应未捕获，但输入框已清空，按成功处理")

            logger.info(f"[DM] 单条操作耗时 {time.monotonic() - started:.2f}s")
            return True

        except CaptchaBlockedError:
            raise
        except PWTimeout:
            logger.error(f"「{nickname}」操作超时")
            return False
        except Exception as e:
            logger.error(f"「{nickname}」发送失败：{e}")
            return False
        finally:
            page.remove_listener("response", on_profile_response)

    @staticmethod
    async def _find_dm_button(page: Page, timeout: float = 8.0):
        """在用户主页上查找「发私信」按钮，返回 Locator 或 None"""
        selectors = [
            # 数据属性（最可靠）
            'button[data-e2e="user-info-send-message"]',
            'button[data-e2e="send-message"]',
            # 文字匹配
            'button:has-text("私信")',
            'button:has-text("发消息")',
            'button:has-text("发私信")',
            'button:has-text("消息")',
            # class 匹配
            '[class*="message-btn"]',
            '[class*="im-btn"]',
            '[class*="send-msg"]',
            '[class*="sendMsg"]',
            # 通用兜底
            'a:has-text("私信")',
            'span:has-text("私信")',
        ]
        result = await find_first_visible(page, selectors, timeout=timeout)
        if result is not None:
            logger.debug("[DM] 已找到可见私信按钮")
        return result
