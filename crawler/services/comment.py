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
from crawler.utils.config import get, ROOT_DIR

# 仅路径保持模块级常量
SENT_FILE: Path = ROOT_DIR / get("comment.sent_record_file", "data/comment_sent.json")
POST_LIST_API   = "/aweme/v1/web/aweme/post/"


async def _fill_input(el, text: str, page):
    """
    通用输入填充：
    - textarea / input：用 fill() 直接填充
    - contenteditable div：用 evaluate 设置内容并触发 React 能感知的事件
    """
    tag = await el.evaluate("e => e.tagName.toLowerCase()")
    if tag in ("input", "textarea"):
        await el.fill(text)
    else:
        await el.click()
        await el.evaluate(
            """(el, txt) => {
                el.textContent = txt;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            text,
        )
        # 末尾补空格再退格，确保 React onChange 感知到内容变化
        await page.keyboard.press("End")
        await page.keyboard.type(" ")
        await page.keyboard.press("Backspace")


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

        for profile in profiles:
            if self._record.today_count() >= daily_limit:
                logger.warning(f"今日评论已达上限 {daily_limit} 条，停止")
                stats["skipped_limit"] += len(profiles) - profiles.index(profile)
                break

            if self._record.already_commented(profile.sec_user_id):
                logger.debug(f"已评论过「{profile.nickname}」，跳过")
                stats["skipped_dup"] += 1
                continue

            aweme_id, ok = await self._comment_one(profile)
            if ok:
                self._record.record(profile.sec_user_id, profile.nickname, aweme_id)
                stats["sent"] += 1
                logger.info(
                    f"✅ 已评论 → 「{profile.nickname}」作品 {aweme_id}"
                    f"（今日 {self._record.today_count()}/{daily_limit}）"
                )
                await asyncio.sleep(random.randint(interval_min, interval_max))
            else:
                stats["failed"] += 1

        logger.info(
            f"评论批量完成：发送 {stats['sent']} / 跳过重复 {stats['skipped_dup']} / "
            f"达限 {stats['skipped_limit']} / 失败 {stats['failed']}"
        )
        return stats

    # ── 获取最新作品 ID ───────────────────────────────────────────────────────

    async def _get_latest_aweme_id(self, sec_user_id: str) -> str:
        """
        访问达人主页，拦截 /aweme/v1/web/aweme/post/ 接口获取最新作品 ID。
        """
        page = self._page
        result: list[str] = []
        done = asyncio.Event()

        async def on_response(response):
            if POST_LIST_API not in response.url:
                return
            if response.status != 200:
                done.set()
                return
            try:
                body = await response.body()
                data = json.loads(body)
                aweme_list = data.get("aweme_list") or []
                if aweme_list:
                    aweme_id = aweme_list[0].get("aweme_id", "")
                    if aweme_id:
                        result.append(aweme_id)
            except Exception as e:
                logger.debug(f"解析作品列表失败: {e}")
            finally:
                done.set()

        page.on("response", on_response)
        try:
            profile_url = f"https://www.douyin.com/user/{sec_user_id}"
            await page.goto("about:blank", timeout=5000)
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=25_000)
            await asyncio.wait_for(done.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            logger.debug(f"获取最新作品超时：{sec_user_id[:16]}...")
        except Exception as e:
            logger.debug(f"访问主页失败：{e}")
        finally:
            page.remove_listener("response", on_response)

        return result[0] if result else ""

    # ── 发布评论 ──────────────────────────────────────────────────────────────

    async def _comment_one(self, profile: KOLProfile) -> tuple[str, bool]:
        """
        对单个达人的最新作品发评论。
        返回 (aweme_id, success)
        """
        page = self._page
        nickname = profile.nickname or profile.uid

        # Step 1：获取最新作品 ID
        aweme_id = await self._get_latest_aweme_id(profile.sec_user_id)
        if not aweme_id:
            logger.warning(f"「{nickname}」未获取到最新作品，跳过评论")
            return "", False

        # Step 2：导航到作品页
        video_url = f"https://www.douyin.com/video/{aweme_id}"
        logger.info(f"导航到作品页：{video_url}")
        try:
            await page.goto("about:blank", timeout=5000)
            await page.goto(video_url, wait_until="domcontentloaded", timeout=25_000)
        except Exception as e:
            logger.warning(f"「{nickname}」导航到作品页失败：{e}")
            return aweme_id, False

        await asyncio.sleep(random.uniform(3.5, 5.0))

        # 截图诊断（仅首次）
        if not getattr(self, '_cmt_screenshot', False):
            self._cmt_screenshot = True
            try:
                await page.screenshot(path="/tmp/comment_video_page.png")
                title = await page.title()
                logger.info(f"[评论诊断] 截图已保存 /tmp/comment_video_page.png title={title!r}")
                # 打印所有 textarea 和 contenteditable
                for sel in ['textarea', '[contenteditable]', '[placeholder*="评论"]', '[placeholder*="说点"]']:
                    els = await page.locator(sel).all()
                    for el in els:
                        ph = await el.get_attribute("placeholder") or ""
                        cls = await el.get_attribute("class") or ""
                        logger.info(f"[评论诊断] {sel}: placeholder={ph!r} class={cls[:60]}")
            except Exception as e:
                logger.debug(f"[评论诊断] 失败: {e}")

        # Step 3：找到评论输入框（先点击「评论」区域激活）
        comment_text = (
            _get_template()
            .replace("\r\n", " ")
            .replace("\n", " ")
            .replace("\r", " ")
            .format(nickname=nickname)
            .strip()
        )
        logger.info(f"[评论] 准备发送（{len(comment_text)} 字）：{comment_text[:50]}...")
        input_box = await self._find_comment_input(page)
        if input_box is None:
            logger.warning(f"「{nickname}」未找到评论输入框")
            return aweme_id, False

        # Step 4：点击激活并输入
        try:
            await input_box.click()
            await asyncio.sleep(0.8)
            # 点击后重新找——有些容器点击后才会出现真正的 editable 子元素
            real_input = None
            for sel in ['[contenteditable="true"]', 'textarea', 'input[type="text"]']:
                try:
                    el = input_box.locator(sel).first
                    if await el.count() > 0:
                        real_input = el
                        break
                    # 也从 page 全局找（对话框可能在 body 层）
                    el2 = page.locator(sel).last
                    if await el2.count() > 0:
                        real_input = el2
                        break
                except Exception:
                    continue
            target = real_input or input_box
            # 直接填充，不逐字输入
            await _fill_input(target, comment_text, page)
            await asyncio.sleep(random.uniform(0.3, 0.8))
        except Exception as e:
            logger.warning(f"「{nickname}」输入评论失败：{e}")
            return aweme_id, False

        # Step 5：发送评论
        sent = await self._submit_comment(page, input_box)
        return aweme_id, sent

    @staticmethod
    async def _find_comment_input(page: Page):
        """查找评论输入框，找不到时先尝试点击评论区域激活"""
        selectors = [
            '[data-e2e="comment-input"]',
            'div[contenteditable="true"][class*="comment"]',
            'div[class*="comment"] div[contenteditable]',
            'div[contenteditable="true"][class*="input"]',
            'textarea[placeholder*="评论"]',
            'textarea[placeholder*="说点什么"]',
            'textarea[placeholder*="友善评论"]',
            '[class*="comment-input-inner"]',
            '[class*="commentInput"]',
            '[class*="comment_input"]',
            # 通用 contenteditable 兜底
            'div[contenteditable="true"]',
        ]

        # 先直接找
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    logger.debug(f"[评论] 找到输入框：{sel}")
                    return el
            except Exception:
                continue

        # 找不到时，尝试点击「评论」按钮/区域来激活输入框
        activate_selectors = [
            '[data-e2e="comment-icon"]',
            'button[aria-label*="评论"]',
            'svg[class*="comment"]',
            'span:has-text("评论")',
            '[class*="comment-btn"]',
        ]
        for sel in activate_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    logger.debug(f"[评论] 点击评论入口：{sel}")
                    await asyncio.sleep(2.0)
                    break
            except Exception:
                continue

        # 激活后再次查找
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    logger.debug(f"[评论] 激活后找到输入框：{sel}")
                    return el
            except Exception:
                continue
        return None

    @staticmethod
    async def _submit_comment(page: Page, input_box) -> bool:
        """提交评论：先找发送按钮，找不到就用 Enter"""
        send_selectors = [
            'button[data-e2e="comment-submit"]',
            'button:has-text("发布")',
            'button:has-text("发送")',
            '[class*="comment"] button[class*="submit"]',
            '[class*="btn-submit"]',
            '[class*="send-btn"]',
            'button[class*="send"]',
        ]
        for sel in send_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_enabled():
                    await btn.click()
                    await asyncio.sleep(1.5)
                    logger.info(f"[评论] 点击发送按钮：{sel}")
                    return True
            except Exception:
                continue

        # 回退：直接按 Enter 键发送
        try:
            await input_box.press("Enter")
            await asyncio.sleep(1.5)
            logger.info("[评论] 用 Enter 键发送")
            return True
        except Exception as e:
            logger.warning(f"[评论] Enter 发送失败：{e}")

        return False
