"""
验证码检测与等待恢复工具
检测页面上是否出现滑块/点选验证码，出现后等待配置时间再重试
"""
import asyncio
import random
from loguru import logger
from playwright.async_api import Page

from crawler.utils.config import get


def _captcha_wait() -> int:
    return int(get("search.anti_crawl.captcha_wait", 90))


def _captcha_max_retry() -> int:
    return int(get("search.anti_crawl.captcha_max_retry", 3))


# 验证码页面的特征选择器（抖音常见）
CAPTCHA_SELECTORS = [
    '[class*="captcha"]',
    '[id*="captcha"]',
    '[class*="verify"]',
    '[class*="slider"]',
    'div[class*="secsdk"]',
    'div[class*="cap-box"]',
    'iframe[src*="captcha"]',
    'iframe[src*="verify"]',
    # 抖音滑块验证码
    'div[class*="sc-captcha"]',
    '#captcha_container',
]

# 验证码页面 URL 特征
CAPTCHA_URL_KEYWORDS = [
    "captcha",
    "verify",
    "verifycenter",
    "passport.douyin.com/web/passport/captcha",
]


async def is_captcha_present(page: Page) -> bool:
    """检测当前页面是否出现验证码"""
    # 1. 检查 URL 是否跳转到验证码页
    current_url = page.url
    if any(kw in current_url.lower() for kw in CAPTCHA_URL_KEYWORDS):
        logger.warning(f"[验证码] 检测到验证码 URL：{current_url[:80]}")
        return True

    # 2. 检查页面上的验证码 DOM 元素
    for sel in CAPTCHA_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                logger.warning(f"[验证码] 检测到验证码元素：{sel}")
                return True
        except Exception:
            continue

    return False


async def wait_for_captcha_clear(page: Page, wait_seconds: int | None = None) -> bool:
    """
    等待验证码消失（用户手动完成或超时自动跳过）。
    返回 True = 验证码已消失，False = 超时仍未消失。
    """
    wait = wait_seconds or _captcha_wait()
    logger.warning(f"[验证码] 等待用户完成验证码，或 {wait} 秒后自动跳过...")

    deadline = asyncio.get_event_loop().time() + wait
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(3)
        if not await is_captcha_present(page):
            logger.info("[验证码] 验证码已消失，继续执行")
            return True
        remaining = int(deadline - asyncio.get_event_loop().time())
        if remaining % 15 == 0 and remaining > 0:
            logger.debug(f"[验证码] 等待验证码...剩余 {remaining}s")

    logger.error(f"[验证码] 超时 {wait}s，验证码未消失")
    return False


async def navigate_with_captcha_guard(
    page: Page,
    url: str,
    wait_until: str = "domcontentloaded",
    timeout: int = 25_000,
    max_retry: int | None = None,
) -> bool:
    """
    带验证码检测的页面导航。
    出现验证码时等待恢复后重试，最多重试 max_retry 次。
    返回 True = 成功导航，False = 失败。
    """
    retries = max_retry or _captcha_max_retry()

    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            logger.warning(f"[导航] 第 {attempt} 次导航超时：{e}")

        await asyncio.sleep(random.uniform(2, 4))

        # 检测验证码
        if await is_captcha_present(page):
            logger.warning(f"[验证码] 第 {attempt}/{retries} 次导航后出现验证码：{url[:60]}")
            cleared = await wait_for_captcha_clear(page)
            if cleared:
                # 验证码消失后再次导航
                try:
                    await page.goto(url, wait_until=wait_until, timeout=timeout)
                    await asyncio.sleep(2)
                    if not await is_captcha_present(page):
                        logger.info(f"[导航] 验证码恢复后导航成功")
                        return True
                except Exception as e:
                    logger.warning(f"[导航] 验证码后重新导航失败：{e}")
            if attempt >= retries:
                logger.error(f"[导航] 已重试 {retries} 次，放弃")
                return False
        else:
            return True

    return False
