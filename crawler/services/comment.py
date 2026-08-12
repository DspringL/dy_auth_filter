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
        ip_result: list[str] = []
        done = asyncio.Event()

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
                            result.append(aweme_id)
                except Exception as e:
                    logger.debug(f"解析作品列表失败: {e}")
                finally:
                    done.set()
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
            await page.goto("about:blank", timeout=5000)
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=25_000)
            await asyncio.wait_for(done.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            logger.debug(f"获取最新作品超时：{sec_user_id[:16]}...")
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
        - 激活方式：坐标点击底部评论栏区域
        - 输入方式：page.keyboard.type(text, delay=N) 逐字输入
        - 发送方式：按 Enter 键，触发 POST /aweme/v1/web/comment/publish
        返回 (aweme_id, success)
        """
        page = self._page
        nickname = profile.nickname or profile.uid

        # Step 1：获取最新作品 ID（顺带采集 IP 属地）
        aweme_id, ip = await self._get_latest_aweme_id(profile.sec_user_id)
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
            await page.goto("about:blank", timeout=5000)
            await page.goto(video_url, wait_until="domcontentloaded", timeout=25_000)
        except Exception as e:
            logger.warning(f"「{nickname}」导航到作品页失败：{e}")
            return aweme_id, False

        await asyncio.sleep(random.uniform(3.0, 4.5))

        # Step 3：构造评论文本
        comment_text = (
            _get_template()
            .replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            .format(nickname=nickname)
            .strip()
        )
        logger.info(f"[评论] 准备发送（{len(comment_text)} 字）：{comment_text[:50]}...")

        # Step 4：找到评论输入框并点击激活
        # 截图确认：视频详情页底部评论输入框是 input[placeholder*="留下你的精彩评论吧"]
        # 点击后页面滚动，输入框移到顶部（y≈85），但焦点已在输入框上
        input_sel = None
        input_el = None

        for sel in [
            'input[placeholder*="留下你的精彩评论吧"]',
            'input[placeholder*="友善评论"]',
            'input[placeholder*="评论"]',
            'input[placeholder*="说点什么"]',
            '[data-e2e="comment-input"]',
            # 评论区列表底部的输入框
            '[class*="commentInput"] input',
            '[class*="comment-input"] input',
            '[class*="DraftEditor-content"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    input_sel = sel
                    input_el = el
                    break
            except Exception:
                pass

        if input_el is None:
            logger.warning(f"「{nickname}」未找到评论输入框")
            return aweme_id, False

        logger.info(f"[评论] 找到输入框：{input_sel}")

        # 点击输入框（会触发页面滚动，输入框移到顶部，焦点正确）
        await input_el.click()
        await asyncio.sleep(1.0)

        # 确认焦点已在输入框
        focused_tag = await page.evaluate(
            "() => document.activeElement ? document.activeElement.tagName + '|' + (document.activeElement.placeholder||'') : 'none'"
        )
        logger.info(f"[评论] focused: {focused_tag}")

        # Step 5：直接用 fill 填充（input 元素支持 fill）
        tag = await input_el.evaluate("el => el.tagName.toLowerCase()")
        if tag in ("input", "textarea"):
            await input_el.fill(comment_text)
        else:
            # contenteditable：逐字输入
            await page.keyboard.type(comment_text, delay=8)
        await asyncio.sleep(random.uniform(0.3, 0.6))
        logger.info(f"[评论] 输入完成")

        # Step 6：点击发送按钮（截图确认：输入后右侧出现「发布」按钮）
        sent = False
        for sel in [
            'button[data-e2e="comment-submit"]',
            'button:has-text("发布")',
            'button:has-text("发送")',
            '[class*="submit"]',
            '[class*="btn-send"]',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    sent = True
                    logger.info(f"[评论] 点击发送按钮：{sel}")
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass

        if not sent:
            # 回退：Enter 键
            await input_el.press("Enter")
            await asyncio.sleep(1.5)
            logger.info("[评论] Enter 键发送")
            sent = True

        return aweme_id, sent
