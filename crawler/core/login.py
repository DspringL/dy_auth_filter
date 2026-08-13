"""
抖音网页端登录模块
策略：
  1. 打开抖音首页，等待页面加载完成
  2. 若未登录，点击「登录」按钮弹出登录弹窗（抖音自带二维码）
  3. 轮询 Cookie，只要检测到登录态就视为成功，不做任何页面跳转
  4. 登录成功后持久化 Cookie 供后续 XHR 接口复用
"""
import asyncio
import json
import time
from pathlib import Path
from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from crawler.core.browser import BrowserManager
from crawler.utils.config import get, ROOT_DIR

# ── 常量 ──────────────────────────────────────────────────────────────────────

DOUYIN_HOME = "https://www.douyin.com"

# 登录态关键 Cookie（任意一个有值即视为已登录）
LOGIN_COOKIES = ["sessionid", "sessionid_ss", "uid_tt", "uid_tt_ss"]

# Cookie 持久化路径
COOKIE_FILE = ROOT_DIR / get("douyin.cookie_file", "data/cookies.json")

# 扫码等待超时（秒）
QRCODE_TIMEOUT = 180


# ── 登录状态检测 ──────────────────────────────────────────────────────────────

async def check_login(manager: BrowserManager) -> bool:
    """
    检测浏览器 Profile 中是否已有有效登录 Cookie。
    返回 True = 已登录，False = 未登录。
    """
    cookies = await manager.get_cookies()
    cookie_map = {c["name"]: c["value"] for c in cookies}

    for key in LOGIN_COOKIES:
        val = cookie_map.get(key, "").strip()
        if val:
            logger.info(f"检测到登录 Cookie：{key}={val[:8]}*** → 已登录")
            return True

    logger.info("未检测到有效登录 Cookie → 未登录")
    return False


async def _poll_login_cookies(
    manager: BrowserManager,
    timeout: int = QRCODE_TIMEOUT,
    ensure_login_page=None,
) -> bool:
    """
    在指定超时时间内轮询 Cookie，检测到登录态立即返回 True。
    浏览器或二维码标签页被关闭时，可通过 ensure_login_page 恢复扫码页。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        cookies = await manager.get_cookies()
        cookie_map = {c["name"]: c["value"] for c in cookies}
        if any(cookie_map.get(k, "").strip() for k in LOGIN_COOKIES):
            return True

        if ensure_login_page is not None:
            try:
                await ensure_login_page()
            except Exception as exc:
                # BrowserManager 的看门狗仍会继续恢复进程；下一轮再尝试重建扫码页。
                logger.warning(f"恢复扫码登录页面失败，将继续重试：{exc}")

        remaining = int(deadline - time.time())
        if remaining % 10 == 0 and remaining > 0:
            logger.debug(f"等待扫码登录...剩余 {remaining}s")
        await asyncio.sleep(min(2, max(0, deadline - time.time())))
    return False


# ── 触发登录弹窗 ──────────────────────────────────────────────────────────────

async def _trigger_login_dialog(page: Page):
    """
    在抖音首页触发登录弹窗（含二维码）。
    策略：依次尝试多种登录入口，找到就点击，找不到也不报错。
    """
    # 候选登录按钮选择器（抖音会经常改 class，多个兜底）
    login_selectors = [
        # 右上角「登录」文字按钮
        'button:has-text("登录")',
        'a:has-text("登录")',
        '[class*="login"]:has-text("登录")',
        # 数据属性
        '[data-e2e="login-button"]',
        '[data-e2e="nav-login-button"]',
        # 通用 header 区域登录入口
        'header button:has-text("登录")',
        '.login-btn',
        '#login-btn',
    ]

    for sel in login_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                logger.info(f"已点击登录按钮（选择器：{sel}）")
                await asyncio.sleep(1.5)
                return
        except Exception:
            continue

    logger.info("未找到明确的登录按钮，等待页面自然弹出登录弹窗...")


async def _ensure_qrcode_visible(page: Page):
    """
    尝试切换到二维码登录（部分情况下默认展示手机号登录）。
    找不到切换按钮也不报错，直接返回。
    """
    qr_tab_selectors = [
        'text=扫码登录',
        'text=二维码登录',
        '[class*="qrcode-tab"]',
        '[class*="QrCode"]',
        'li:has-text("扫码")',
        'div[role="tab"]:has-text("扫码")',
    ]
    for sel in qr_tab_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                logger.info(f"已切换到二维码登录（选择器：{sel}）")
                await asyncio.sleep(1)
                return
        except Exception:
            continue


# ── 主登录流程 ────────────────────────────────────────────────────────────────

async def do_qrcode_login(manager: BrowserManager) -> bool:
    """
    完整二维码登录流程：
      1. 打开抖音首页
      2. 点击登录按钮触发弹窗
      3. 尝试切换到二维码登录
      4. 轮询 Cookie 等待用户扫码完成
      5. 成功后持久化 Cookie

    不做任何页面跳转（避免 ERR_CONNECTION_CLOSED）。
    """
    logger.info("开始二维码登录流程...")
    page = None
    page_generation = -1

    async def _close_login_page():
        nonlocal page
        if page is not None:
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        page = None

    async def _show_login_page() -> bool:
        """创建并展示扫码页；若 Profile 已登录则返回 True。"""
        nonlocal page, page_generation
        await _close_login_page()
        page = await manager.new_page()
        page_generation = manager.generation

        # ── 第一步：打开抖音首页 ──────────────────────────────────────────
        logger.info(f"打开抖音首页：{DOUYIN_HOME}")
        try:
            await page.goto(
                DOUYIN_HOME,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except PlaywrightTimeoutError:
            # 超时不代表页面没加载，继续尝试
            logger.warning("首页加载超时，继续流程...")
        except Exception as e:
            # 连接错误等，记录后继续
            logger.warning(f"首页加载异常（将继续）：{e}")

        await asyncio.sleep(3)  # 等待 JS 渲染

        # ── 第二步：检测是否已登录（可能 Profile 已有 Cookie） ────────────
        if await check_login(manager):
            logger.info("打开首页后检测到已登录 Cookie，无需扫码")
            return True

        # ── 第三步：触发登录弹窗 ──────────────────────────────────────────
        await _trigger_login_dialog(page)

        # ── 第四步：尝试切换到二维码 ──────────────────────────────────────
        await _ensure_qrcode_visible(page)
        return False

    async def _ensure_login_page():
        """扫码等待期间发现页面或整个浏览器被关闭时，重新展示二维码。"""
        page_lost = page is None
        if not page_lost:
            try:
                page_lost = page.is_closed()
            except Exception:
                page_lost = True
        browser_restarted = page_generation != manager.generation
        if page_lost or browser_restarted:
            logger.warning("扫码页面已关闭或浏览器已重启，正在重新打开二维码...")
            await _show_login_page()

    try:
        if await _show_login_page():
            await _save_cookies(manager)
            await _close_login_page()
            return True

        # ── 第五步：提示用户扫码 ──────────────────────────────────────────
        logger.info(f"等待扫码登录（最长 {QRCODE_TIMEOUT} 秒）...")
        print("\n" + "=" * 60)
        print("请使用抖音 App 扫描浏览器中的二维码完成登录")
        print(f"   等待时间：{QRCODE_TIMEOUT} 秒，扫码后自动继续")
        print("=" * 60 + "\n")

        # ── 第六步：轮询 Cookie，等待登录成功 ────────────────────────────
        logged_in = await _poll_login_cookies(
            manager,
            timeout=QRCODE_TIMEOUT,
            ensure_login_page=_ensure_login_page,
        )

        if logged_in:
            logger.info("扫码登录成功！")
            await _save_cookies(manager)
            await _close_login_page()
            return True
        else:
            logger.error(f"扫码登录超时（{QRCODE_TIMEOUT} 秒内未检测到登录 Cookie）")
            await _close_login_page()
            return False

    except Exception as e:
        logger.error(f"二维码登录流程异常：{e}")
        await _close_login_page()
        return False


# ── Cookie 持久化 ─────────────────────────────────────────────────────────────

async def _save_cookies(manager: BrowserManager):
    """将登录后的 Cookie 持久化到 JSON 文件，供 XHR 请求复用"""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cookies = await manager.get_cookies()
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info(f"Cookie 已保存到：{COOKIE_FILE}（共 {len(cookies)} 条）")

    # 同时保存完整 storage state（含 localStorage，供下次免登录）
    try:
        await manager.save_storage_state()
    except Exception as e:
        logger.warning(f"保存 storage state 失败（不影响登录）：{e}")


def load_cookies_as_header() -> str:
    """
    从文件加载 Cookie，格式化为 HTTP Cookie 请求头字符串。
    用于构造 XHR 请求时注入。
    """
    if not COOKIE_FILE.exists():
        logger.warning("Cookie 文件不存在，XHR 请求可能无法通过鉴权")
        return ""

    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        cookies: list[dict] = json.load(f)

    relevant = [
        c for c in cookies
        if "douyin.com" in c.get("domain", "") or "tiktok.com" in c.get("domain", "")
    ]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)


def load_cookies_as_dict() -> dict:
    """将文件中的 Cookie 加载为 {name: value} 字典"""
    if not COOKIE_FILE.exists():
        return {}
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        cookies: list[dict] = json.load(f)
    return {c["name"]: c["value"] for c in cookies}


# ── 统一入口 ──────────────────────────────────────────────────────────────────

async def ensure_login(manager: BrowserManager) -> bool:
    """
    检查登录状态，未登录则引导扫码登录。
    返回 True = 最终已登录，False = 未成功登录。
    """
    logged_in = await check_login(manager)
    if logged_in:
        logger.info("Cookie 有效，已登录，跳过二维码流程")
        await _save_cookies(manager)
        return True

    return await do_qrcode_login(manager)
