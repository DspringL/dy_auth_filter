"""联系流程共用的快速、可验证浏览器操作。"""

import asyncio
import random
import re
import time

from loguru import logger
from playwright.async_api import Locator, Page


VIDEO_ID_RE = re.compile(r"/video/(\d+)")


def pick_random_line(template: str) -> str:
    """
    从多行模板中随机取一行非空内容返回。
    若模板只有一行（或全是空行），则返回去首尾空白后的原始内容。
    """
    lines = [line.strip() for line in template.splitlines() if line.strip()]
    if not lines:
        return template.strip()
    return random.choice(lines)


def render_single_line(template: str, nickname: str) -> str:
    """渲染模板并强制为单行，避免一次输入被拆成多条消息。
    当模板为多行时，先随机取一行再渲染。"""
    line = pick_random_line(template)
    return (
        line
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .format(nickname=nickname)
        .strip()
    )


def video_id_from_href(href: str | None) -> str:
    """从抖音作品链接中提取 aweme_id。"""
    match = VIDEO_ID_RE.search(href or "")
    return match.group(1) if match else ""


async def first_video_id(page: Page) -> str:
    """读取主页 DOM 中排在最前的作品 ID，不触发额外请求。"""
    try:
        links = page.locator('a[href*="/video/"]')
        count = min(await links.count(), 12)
        for index in range(count):
            aweme_id = video_id_from_href(await links.nth(index).get_attribute("href"))
            if aweme_id:
                return aweme_id
    except Exception as exc:
        logger.debug(f"读取主页作品链接失败：{exc}")
    return ""


async def find_first_visible(
    page: Page,
    selectors: list[str],
    timeout: float = 8.0,
) -> Locator | None:
    """轮询一组选择器，返回第一个可见元素，避免固定时长 sleep。"""
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    return locator
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.1)


async def editor_text(locator: Locator) -> str:
    """读取 input/textarea/contenteditable 的当前文本。"""
    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
    if tag in ("input", "textarea"):
        return await locator.input_value()
    return await locator.inner_text()


async def fill_editor(locator: Locator, text: str, page: Page) -> None:
    """
    一次性填充编辑器。

    input/textarea 使用 Playwright fill；contenteditable 走 keyboard.insert_text，
    一次触发完整文本变更，不受 CloakBrowser humanize 的逐按键延迟影响。
    """
    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
    if tag in ("input", "textarea"):
        await locator.fill(text, timeout=3_000)
    else:
        # CloakBrowser 的 humanize="careful" 会把 Locator.fill(contenteditable)
        # 也转换成人类逐键输入，50 字可能耗时约 10~30 秒。
        is_draft_js = await locator.evaluate(
            "el => el.classList.contains('public-DraftEditor-content')"
        )
        if is_draft_js:
            # Draft.js 需要真实 input 事件才能维护 EditorState。insert_text 只发
            # 一次事件，既兼容状态管理，也不会触发 humanize 的逐键延迟。
            await locator.focus()
            if (await editor_text(locator)).strip():
                # 直接设定选区，避免宿主 macOS 与页面 Windows 指纹之间的
                # Control/Meta 快捷键差异；实际变更仍由真实键盘事件完成。
                await locator.evaluate(
                    """el => {
                        const selection = window.getSelection();
                        const range = document.createRange();
                        range.selectNodeContents(el);
                        selection.removeAllRanges();
                        selection.addRange(range);
                    }"""
                )
                if text:
                    await page.keyboard.insert_text(text)
                else:
                    await page.keyboard.press("Backspace")
            elif text:
                await page.keyboard.insert_text(text)
        else:
            # IM EditorKit 能正确处理浏览器原生编辑命令；选择后整段替换也能
            # 清掉上一次意外中断留下的草稿。
            await locator.evaluate(
                """(el, value) => {
                    el.focus();
                    const selection = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    return value === ''
                        ? document.execCommand('delete', false, null)
                        : document.execCommand('insertText', false, value);
                }""",
                text,
            )

    actual = await editor_text(locator)
    if text and text not in actual:
        raise RuntimeError("编辑器未接收到完整文本")
    if not text and actual.strip():
        raise RuntimeError("编辑器未成功清空")
