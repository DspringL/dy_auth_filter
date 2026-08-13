"""
验证码检测与等待恢复工具
检测页面上是否出现可见的滑块/点选验证码，自动截图后等待用户手动完成
"""
import asyncio
import inspect
import json
import os
import random
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import Page

from crawler.utils.config import get


def _captcha_wait() -> int:
    # 旧版桌面配置可能仍保留 90 秒；人工点选验证码通常需要更长时间，
    # 最少保留 5 分钟，避免刷新素材时任务提前超时。
    return max(300, int(get("search.anti_crawl.captcha_wait", 300)))


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

CAPTCHA_LOAD_FAILURE_TEXTS = [
    "图片加载失败",
    "请刷新重试或换个网络",
    "5202",
]

CAPTCHA_CLEAR_STABLE_CHECKS = 3
CAPTCHA_CLEAR_POLL_INTERVAL = 2


def _captcha_screenshot_dir() -> Path:
    """优先使用桌面壳传入的系统下载目录，命令行模式使用 ~/Downloads。"""
    configured = os.environ.get("DYAUTH_CAPTCHA_SCREENSHOT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    downloads = Path.home() / "Downloads"
    return downloads / "DouyinKOL验证码"


CAPTCHA_SCREENSHOT_DIR = _captcha_screenshot_dir()
_latest_captcha_screenshot: Path | None = None
CaptchaEventHandler = Callable[[str, Path | None], Awaitable[None] | None]

CAPTCHA_NETWORK_KEYWORDS = (
    "captcha",
    # 抖音验证码图片域名实际使用了历史拼写 catpcha.byteimg.com。
    "catpcha",
    "verify",
    "secsdk",
    "byteverify",
    "rmc.bytedance",
)
CAPTCHA_DIAGNOSTIC_EVENTS_LIMIT = 300
_DIAGNOSTIC_EVENTS_ATTR = "_dyauth_captcha_diagnostic_events"
_DIAGNOSTIC_ATTACHED_ATTR = "_dyauth_captcha_diagnostic_attached"
_DIAGNOSTIC_CDP_ATTACHED_ATTR = "_dyauth_captcha_cdp_diagnostic_attached"


class CaptchaBlockedError(RuntimeError):
    """验证码在人工等待窗口内未完成，任务应暂停并向上层报告。"""


async def _emit_captcha_event(
    handler: CaptchaEventHandler | None,
    event: str,
    screenshot: Path | None,
) -> None:
    if handler is None:
        return
    result = handler(event, screenshot)
    if inspect.isawaitable(result):
        await result


def latest_captcha_screenshot() -> Path | None:
    """返回本进程最近一次验证码截图路径。"""
    return _latest_captcha_screenshot


def _sanitized_url(raw_url: str) -> str:
    """去掉查询参数和片段，避免诊断文件写入验证码 token。"""
    try:
        parsed = urlparse(raw_url)
        return parsed._replace(query="", fragment="").geturl()
    except Exception:
        return ""


def _is_captcha_network_url(raw_url: str) -> bool:
    lowered = raw_url.lower()
    return any(keyword in lowered for keyword in CAPTCHA_NETWORK_KEYWORDS)


def _request_is_from_captcha_frame(request) -> bool:
    try:
        frame_url = request.frame.url
    except Exception:
        return False
    return _is_captcha_network_url(frame_url)


def attach_captcha_diagnostics(page: Page) -> None:
    """记录验证码相关请求状态；不记录请求参数、Cookie 或图片内容。"""
    if getattr(page, _DIAGNOSTIC_ATTACHED_ATTR, False):
        return

    events = deque(maxlen=CAPTCHA_DIAGNOSTIC_EVENTS_LIMIT)
    setattr(page, _DIAGNOSTIC_EVENTS_ATTR, events)
    setattr(page, _DIAGNOSTIC_ATTACHED_ATTR, True)

    async def record_response(response) -> None:
        try:
            request = response.request
            raw_url = response.url
            status = int(response.status)
            if (
                not _is_captcha_network_url(raw_url)
                and not _request_is_from_captcha_frame(request)
                and status < 400
            ):
                return
            event = {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "event": "response",
                "status": status,
                "resource_type": getattr(request, "resource_type", ""),
                "url": _sanitized_url(raw_url),
            }
            if _is_captcha_network_url(raw_url):
                headers = {
                    str(key).lower(): str(value)
                    for key, value in (await response.all_headers()).items()
                }
                selected_headers = {
                    key: headers[key]
                    for key in (
                        "content-type",
                        "content-length",
                        "content-encoding",
                        "access-control-allow-origin",
                        "cross-origin-resource-policy",
                        "x-cache",
                    )
                    if key in headers
                }
                if selected_headers:
                    event["headers"] = selected_headers

            events.append(event)
        except Exception:
            return

    def record_request_failed(request) -> None:
        try:
            failure = getattr(request, "failure", None)
            if callable(failure):
                failure = failure()
            events.append(
                {
                    "time": datetime.now().isoformat(timespec="milliseconds"),
                    "event": "request_failed",
                    "failure": str(failure or "unknown")[:300],
                    "resource_type": getattr(request, "resource_type", ""),
                    "url": _sanitized_url(getattr(request, "url", "")),
                }
            )
        except Exception:
            return

    page.on("response", record_response)
    page.on("requestfailed", record_request_failed)

    def record_console(message) -> None:
        try:
            if message.type not in ("error", "warning"):
                return
            location = message.location or {}
            events.append(
                {
                    "time": datetime.now().isoformat(timespec="milliseconds"),
                    "event": "console",
                    "level": message.type,
                    "text": message.text[:500],
                    "url": _sanitized_url(str(location.get("url", ""))),
                    "line": location.get("lineNumber"),
                }
            )
        except Exception:
            return

    def record_page_error(error) -> None:
        events.append(
            {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "event": "page_error",
                "text": str(error)[:500],
            }
        )

    page.on("console", record_console)
    page.on("pageerror", record_page_error)


async def attach_captcha_cdp_diagnostics(page: Page) -> None:
    """通过 CDP 记录网络失败的底层原因（CORS/拦截/取消/TLS）。"""
    if getattr(page, _DIAGNOSTIC_CDP_ATTACHED_ATTR, False):
        return
    attach_captcha_diagnostics(page)
    events = getattr(page, _DIAGNOSTIC_EVENTS_ATTR)

    try:
        session = await page.context.new_cdp_session(page)
        await session.send("Network.enable")
    except Exception as exc:
        logger.debug(f"[验证码] 无法启用 CDP 网络诊断：{exc}")
        return

    setattr(page, _DIAGNOSTIC_CDP_ATTACHED_ATTR, True)
    # 保持 Session 引用，防止被垃圾回收后监听失效。
    setattr(page, "_dyauth_captcha_cdp_session", session)
    request_urls: dict[str, str] = {}

    def request_will_be_sent(params: dict) -> None:
        request_id = str(params.get("requestId", ""))
        raw_url = str((params.get("request") or {}).get("url", ""))
        if request_id:
            request_urls[request_id] = _sanitized_url(raw_url)
            if len(request_urls) > 1_000:
                for stale_id in list(request_urls)[:500]:
                    request_urls.pop(stale_id, None)

    def loading_failed(params: dict) -> None:
        request_id = str(params.get("requestId", ""))
        error_text = str(params.get("errorText", ""))[:300]
        resource_type = str(params.get("type", ""))
        failed_url = request_urls.pop(request_id, "")
        # 首页跳转到搜索页时会主动取消上百个异步脚本；这类正常取消会挤掉
        # 真正的验证码诊断，且与素材失败无关。
        if (
            error_text == "net::ERR_ABORTED"
            and resource_type == "Script"
            and not _is_captcha_network_url(failed_url)
        ):
            return
        event = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "event": "cdp_loading_failed",
            "error_text": error_text,
            "canceled": bool(params.get("canceled", False)),
            "blocked_reason": str(params.get("blockedReason", "")),
            "resource_type": resource_type,
            "url": failed_url,
        }
        cors_status = params.get("corsErrorStatus")
        if cors_status:
            event["cors_error_status"] = cors_status
        events.append(event)

    session.on("Network.requestWillBeSent", request_will_be_sent)
    session.on("Network.loadingFailed", loading_failed)


async def save_captcha_diagnostics(page: Page, reason: str) -> Path | None:
    """把 5202 前的网络状态和非敏感浏览器环境保存到下载目录。"""
    CAPTCHA_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_reason = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in reason)
    path = CAPTCHA_SCREENSHOT_DIR / f"captcha_diagnostic_{timestamp}_{safe_reason}.json"

    try:
        browser_environment = await page.evaluate(
            """() => ({
                userAgent: navigator.userAgent,
                platform: navigator.platform,
                vendor: navigator.vendor,
                language: navigator.language,
                languages: navigator.languages,
                webdriver: navigator.webdriver,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                maxTouchPoints: navigator.maxTouchPoints,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                window: {
                    outerWidth: window.outerWidth,
                    outerHeight: window.outerHeight,
                    innerWidth: window.innerWidth,
                    innerHeight: window.innerHeight
                },
                screen: {
                    width: screen.width,
                    height: screen.height,
                    availWidth: screen.availWidth,
                    availHeight: screen.availHeight,
                    colorDepth: screen.colorDepth,
                    devicePixelRatio: window.devicePixelRatio
                }
            })"""
        )
    except Exception as exc:
        browser_environment = {"read_error": str(exc)[:300]}

    frame_dom_snapshots = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame in frames:
        try:
            snapshot = await frame.evaluate(
                """() => ({
                    images: Array.from(document.images).map((img) => ({
                        src: img.currentSrc || img.src || '',
                        complete: img.complete,
                        naturalWidth: img.naturalWidth,
                        naturalHeight: img.naturalHeight,
                        width: img.width,
                        height: img.height,
                        crossOrigin: img.crossOrigin,
                        decoding: img.decoding
                    })),
                    canvases: Array.from(document.querySelectorAll('canvas')).map((canvas) => ({
                        width: canvas.width,
                        height: canvas.height,
                        clientWidth: canvas.clientWidth,
                        clientHeight: canvas.clientHeight
                    }))
                })"""
            )
        except Exception:
            continue
        images = snapshot.get("images", []) if isinstance(snapshot, dict) else []
        canvases = snapshot.get("canvases", []) if isinstance(snapshot, dict) else []
        # 只保留验证码 Frame 或包含验证码图片/画布的 Frame，避免写出无关页面数据。
        captcha_images = []
        for image in images:
            src = str(image.get("src", ""))
            if _is_captcha_network_url(src):
                image = dict(image)
                image["src"] = _sanitized_url(src)
                captcha_images.append(image)
        if _is_captcha_network_url(frame.url) or captcha_images:
            frame_dom_snapshots.append(
                {
                    "url": _sanitized_url(frame.url),
                    "images": captcha_images,
                    "canvases": canvases,
                }
            )

    events = list(getattr(page, _DIAGNOSTIC_EVENTS_ATTR, []))
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "page_url": _sanitized_url(getattr(page, "url", "")),
        "browser_environment": browser_environment,
        "frame_dom_snapshots": frame_dom_snapshots,
        "network_events": events,
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"[验证码] 保存诊断失败：{exc}")
        return None

    logger.warning(f"[验证码] 已保存网络诊断：{path}")
    return path


async def capture_captcha_screenshot(page: Page, reason: str = "challenge") -> Path | None:
    """保存验证码出现时的整页截图，供人工确认与排障。"""
    global _latest_captcha_screenshot

    CAPTCHA_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_reason = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in reason)
    path = CAPTCHA_SCREENSHOT_DIR / f"captcha_{timestamp}_{safe_reason}.png"
    try:
        await page.screenshot(path=str(path), full_page=False)
    except Exception as exc:
        logger.warning(f"[验证码] 截图失败：{exc}")
        return None

    _latest_captcha_screenshot = path
    logger.warning(f"[验证码] 已保存截图：{path}")
    return path


async def is_captcha_present(page: Page) -> bool:
    """检测当前页面是否出现验证码"""
    # 1. 检查 URL 是否跳转到验证码页
    current_url = page.url
    if any(kw in current_url.lower() for kw in CAPTCHA_URL_KEYWORDS):
        logger.warning(f"[验证码] 检测到验证码 URL：{current_url[:80]}")
        return True

    # 验证素材刷新过程中，验证码容器可能被短暂销毁，但页面仍显示
    # “图片加载失败 [5202]”。这仍然是未完成的验证码，不能当作通过。
    if await is_captcha_load_failed(page):
        return True

    # 2. 只把可见的验证码 DOM 判为挑战。抖音正常页面会常驻一个
    # display:none、0×0 的 rmc-nocaptcha iframe，不能仅凭其存在判定。
    for sel in CAPTCHA_SELECTORS:
        try:
            locator = page.locator(sel)
            count = min(await locator.count(), 10)
            for index in range(count):
                el = locator.nth(index)
                src = (await el.get_attribute("src")) or ""
                if "rmc-nocaptcha" in src:
                    continue
                if await el.is_visible():
                    logger.warning(f"[验证码] 检测到可见验证码元素：{sel}")
                    return True
        except Exception:
            continue

    return False


async def is_captcha_load_failed(page: Page) -> bool:
    """检测验证码素材加载失败提示（例如错误码 5202）。"""
    # 验证码正文通常位于 rmc.bytedance.com 的跨域 iframe 中，主页面
    # body.innerText 看不到。Playwright 可以直接读取各 Frame 的 DOM，
    # 因此主页面和所有子 Frame 都要检查。
    targets = [page]
    try:
        targets.extend(list(page.frames))
    except Exception:
        pass

    seen: set[int] = set()
    for target in targets:
        if id(target) in seen:
            continue
        seen.add(id(target))
        try:
            body_text = await target.locator("body").inner_text(timeout=1_000)
        except Exception:
            continue
        if any(text in body_text for text in CAPTCHA_LOAD_FAILURE_TEXTS):
            return True
    return False


async def wait_for_captcha_clear(
    page: Page,
    wait_seconds: int | None = None,
    event_handler: CaptchaEventHandler | None = None,
    reason: str = "challenge",
) -> bool:
    """
    等待验证码消失（用户手动完成或超时终止）。
    返回 True = 验证码已消失，False = 超时仍未消失。
    """
    wait = wait_seconds or _captcha_wait()
    logger.warning(f"[验证码] 请在浏览器中手动完成验证，最长等待 {wait} 秒...")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    clear_checks = 0
    load_failure_reported = False
    while loop.time() < deadline:
        await asyncio.sleep(CAPTCHA_CLEAR_POLL_INTERVAL)

        if await is_captcha_load_failed(page):
            clear_checks = 0
            if not load_failure_reported:
                load_failure_reported = True
                screenshot = await capture_captcha_screenshot(
                    page,
                    reason=f"{reason}-load-failed-5202",
                )
                await save_captcha_diagnostics(
                    page,
                    reason=f"{reason}-load-failed-5202",
                )
                await _emit_captcha_event(event_handler, "load_failed", screenshot)
                logger.warning(
                    "[验证码] 验证素材加载失败（5202），任务继续暂停；"
                    "请更换网络后点击验证码弹窗内的刷新"
                )
            continue

        load_failure_reported = False
        if await is_captcha_present(page):
            clear_checks = 0
        else:
            clear_checks += 1
            if clear_checks >= CAPTCHA_CLEAR_STABLE_CHECKS:
                logger.info(
                    f"[验证码] 已连续 {CAPTCHA_CLEAR_STABLE_CHECKS} 次确认验证码消失，继续执行"
                )
                return True
            logger.debug(
                f"[验证码] 验证码暂时消失，稳定确认 "
                f"{clear_checks}/{CAPTCHA_CLEAR_STABLE_CHECKS}"
            )

        remaining = int(deadline - loop.time())
        if remaining % 15 == 0 and remaining > 0:
            logger.debug(f"[验证码] 等待验证码...剩余 {remaining}s")

    logger.error(f"[验证码] 超时 {wait}s，验证码未消失")
    return False


async def handle_captcha_if_present(
    page: Page,
    reason: str,
    event_handler: CaptchaEventHandler | None = None,
) -> bool:
    """
    若当前存在验证码则截图、通知上层并暂停等待人工处理。

    返回 True 表示本次确实处理过验证码；未出现则返回 False。超时抛出
    CaptchaBlockedError，让任务明确失败而不是绕过验证码继续空跑。
    """
    if not await is_captcha_present(page):
        return False

    screenshot = await capture_captcha_screenshot(page, reason=reason)
    await _emit_captcha_event(event_handler, "required", screenshot)
    cleared = await wait_for_captcha_clear(
        page,
        event_handler=event_handler,
        reason=reason,
    )
    if cleared:
        await _emit_captcha_event(event_handler, "cleared", screenshot)
        return True

    await _emit_captcha_event(event_handler, "timeout", screenshot)
    raise CaptchaBlockedError(
        f"验证码等待超时，请重新执行任务；截图：{screenshot or '未生成'}"
    )


async def wait_for_queue_with_captcha(
    page: Page,
    queue: asyncio.Queue,
    timeout: float,
    reason: str,
    event_handler: CaptchaEventHandler | None = None,
):
    """等待 XHR 队列期间持续检测验证码，人工处理耗时不计入 XHR 超时。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        handled = await handle_captcha_if_present(
            page,
            reason=reason,
            event_handler=event_handler,
        )
        if handled:
            # 验证完成后给页面完整的 XHR 等待窗口。
            deadline = loop.time() + timeout

        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError

        try:
            item = await asyncio.wait_for(queue.get(), timeout=min(1.0, remaining))
        except asyncio.TimeoutError:
            continue

        # 响应和验证码可能几乎同时出现，返回数据前再检查一次。
        handled = await handle_captcha_if_present(
            page,
            reason=reason,
            event_handler=event_handler,
        )
        if handled:
            deadline = loop.time() + timeout
            if item is None:
                continue
        return item


async def navigate_with_captcha_guard(
    page: Page,
    url: str,
    wait_until: str = "domcontentloaded",
    timeout: int = 25_000,
    max_retry: int | None = None,
    event_handler: CaptchaEventHandler | None = None,
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
            try:
                await handle_captcha_if_present(
                    page,
                    reason=f"navigation-attempt-{attempt}",
                    event_handler=event_handler,
                )
                # iframe 挑战通常覆盖在目标页上；验证消失后直接复用当前页，
                # 避免再次导航又触发一轮风控。
                current = urlparse(page.url)
                target = urlparse(url)
                if current.netloc == target.netloc and current.path == target.path:
                    logger.info("[导航] 验证码恢复后目标页面可继续使用")
                    return True

                # 少数全页验证会落到中间页，此时才重新导航到目标地址。
                try:
                    await page.goto(url, wait_until=wait_until, timeout=timeout)
                    await asyncio.sleep(2)
                    if not await is_captcha_present(page):
                        logger.info(f"[导航] 验证码恢复后导航成功")
                        return True
                except Exception as e:
                    logger.warning(f"[导航] 验证码后重新导航失败：{e}")
            except CaptchaBlockedError:
                return False
            if attempt >= retries:
                logger.error(f"[导航] 已重试 {retries} 次，放弃")
                return False
        else:
            return True

    return False
