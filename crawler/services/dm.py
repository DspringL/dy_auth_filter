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
from crawler.utils.config import get, ROOT_DIR

# 仅 SENT_FILE 保持模块级常量（路径不会被前端修改）
SENT_FILE: Path = ROOT_DIR / get("dm.sent_record_file", "data/dm_sent.json")


def _get_template() -> str:
    """运行时读取私信模板（DB 覆盖 > yaml）"""
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

    def __init__(self, page: Page):
        self._page = page
        self._record = SentRecord()

    async def send_batch(self, profiles: list[KOLProfile]) -> dict:
        """
        批量发送私信。
        返回统计：{"sent": n, "skipped_dup": n, "skipped_limit": n, "failed": n}
        """
        stats = {"sent": 0, "skipped_dup": 0, "skipped_limit": 0, "failed": 0}
        daily_limit = _get_daily_limit()
        interval_min, interval_max = _get_interval()

        for profile in profiles:
            if self._record.today_count() >= daily_limit:
                logger.warning(f"今日私信已达上限 {daily_limit} 条，停止发送")
                stats["skipped_limit"] += len(profiles) - profiles.index(profile)
                break

            if self._record.already_sent(profile.sec_user_id):
                logger.debug(f"已发送过「{profile.nickname}」，跳过")
                stats["skipped_dup"] += 1
                continue

            ok = await self._send_one(profile)
            if ok:
                self._record.record(profile.sec_user_id, profile.nickname)
                stats["sent"] += 1
                logger.info(
                    f"✅ 已发送私信 → 「{profile.nickname}」"
                    f"（今日 {self._record.today_count()}/{daily_limit}）"
                )
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
        raw_template = _get_template()
        message = (
            raw_template
            .replace("\r\n", " ")
            .replace("\n", " ")
            .replace("\r", " ")
            .format(nickname=nickname)
            .strip()
        )
        logger.info(f"[DM] 准备发送（{len(message)} 字）：{message[:50]}...")
        profile_url = profile.profile_url

        if not profile_url:
            logger.warning(f"「{nickname}」无主页链接，跳过")
            return False

        try:
            logger.info(f"导航到主页：{profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(2.5, 4.0))

            # ── 截图诊断（仅首次）──────────────────────────────────────────
            if not getattr(self, '_dm_screenshot_taken', False):
                self._dm_screenshot_taken = True
                try:
                    await page.screenshot(path="/tmp/dm_profile_before.png")
                    logger.info("[DM诊断] 截图已保存 /tmp/dm_profile_before.png")
                    # 打印所有按钮文字
                    btns = await page.locator("button").all()
                    btn_texts = []
                    for b in btns[:15]:
                        try:
                            t = (await b.inner_text()).strip()
                            if t:
                                btn_texts.append(repr(t))
                        except Exception:
                            pass
                    logger.info(f"[DM诊断] 页面按钮: {btn_texts}")
                except Exception as e:
                    logger.debug(f"[DM诊断] 截图失败: {e}")

            # ── 点击「发私信」按钮 ───────────────────────────────────────────
            dm_btn = await self._find_dm_button(page)
            if dm_btn is None:
                logger.warning(f"「{nickname}」主页找不到私信按钮，跳过")
                return False

            await dm_btn.click()
            # 等待私信对话框/弹窗完全加载（需要更长时间）
            await asyncio.sleep(random.uniform(3.0, 5.0))

            # ── 截图查看弹窗状态 ────────────────────────────────────────────
            if not getattr(self, '_dm_dialog_screenshot', False):
                self._dm_dialog_screenshot = True
                try:
                    await page.screenshot(path="/tmp/dm_dialog.png")
                    logger.info("[DM诊断] 点击私信后截图 /tmp/dm_dialog.png")
                    # 打印 contenteditable 元素
                    edits = await page.locator('[contenteditable]').all()
                    for e in edits:
                        try:
                            ph = await e.get_attribute("placeholder") or ""
                            cls = await e.get_attribute("class") or ""
                            logger.info(f"[DM诊断] contenteditable: placeholder={ph!r} class={cls[:60]}")
                        except Exception:
                            pass
                    textareas = await page.locator("textarea").all()
                    for t in textareas:
                        try:
                            ph = await t.get_attribute("placeholder") or ""
                            logger.info(f"[DM诊断] textarea: placeholder={ph!r}")
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"[DM诊断] 对话框截图失败: {e}")

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
            input_box = None
            for sel in input_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        input_box = el
                        break
                except Exception:
                    continue

            if input_box is None:
                logger.warning(f"「{nickname}」找不到私信输入框")
                return False

            await input_box.click()
            await asyncio.sleep(0.5)
            # 使用 type（逐字符，模拟人类输入）
            await input_box.type(message, delay=random.randint(30, 80))
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ── 点击发送 ─────────────────────────────────────────────────────
            send_selectors = [
                'button[data-e2e="chat-send-btn"]',
                'button:has-text("发送")',
                '[class*="send-btn"]',
                'button[type="submit"]',
            ]
            sent = False
            for sel in send_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click()
                        sent = True
                        break
                except Exception:
                    continue

            if not sent:
                # 回退：用 Enter 键发送
                await input_box.press("Enter")
                sent = True

            await asyncio.sleep(1.0)
            return sent

        except PWTimeout:
            logger.error(f"「{nickname}」操作超时")
            return False
        except Exception as e:
            logger.error(f"「{nickname}」发送失败：{e}")
            return False

    @staticmethod
    async def _find_dm_button(page: Page):
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
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    logger.debug(f"[DM] 找到私信按钮：{sel}")
                    return el
            except Exception:
                continue

        # 最后一招：打印所有按钮调试
        try:
            btns = await page.locator("button").all()
            for b in btns:
                t = (await b.inner_text()).strip()
                if t:
                    logger.debug(f"[DM] 可用按钮: {t!r}")
        except Exception:
            pass
        return None
