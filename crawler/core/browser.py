"""
浏览器连接管理器。

- 使用持久化 Profile 保留登录态 Cookie
- 优先使用本机 Chrome，避免旧定制内核导致验证码图片 5202
- 未安装 Chrome 时回退 CloakBrowser
- 后台看门狗持续检查浏览器进程
- 用户手动关闭浏览器后自动重启
- 所有 Context 操作在断连时自动恢复一次
"""

import asyncio
import os
import platform
import shutil
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from cloakbrowser import launch_persistent_context_async
from cloakbrowser.browser import IGNORE_DEFAULT_ARGS as CLOAKBROWSER_IGNORE_DEFAULT_ARGS
from loguru import logger
from patchright.async_api import async_playwright

from crawler.utils.config import ROOT_DIR, get
from crawler.utils.captcha import (
    attach_captcha_cdp_diagnostics,
    attach_captcha_diagnostics,
)


# 持久化浏览器 Profile 目录（保存登录态 Cookie）
PROFILE_DIR = ROOT_DIR / "data" / "browser_profile"

_T = TypeVar("_T")


def _find_native_chrome_executable() -> Path | None:
    """返回可用的系统 Chrome；允许环境变量覆盖，便于部署其他 Chromium。"""
    configured = os.environ.get("DYAUTH_BROWSER_EXECUTABLE", "").strip()
    candidates: list[str] = [configured] if configured else []

    system = platform.system()
    if system == "Darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(
                    Path.home()
                    / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                ),
            ]
        )
    elif system == "Windows":
        for base in (
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if base:
                candidates.append(str(Path(base) / "Google/Chrome/Application/chrome.exe"))
    else:
        for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(resolved)

    for candidate in candidates:
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return path
    return None


def _native_sandboxed_launch_options() -> dict[str, Any]:
    """使用本机真实设备参数，并确保两层启动参数都不会关闭 Chromium 沙箱。"""
    # CloakBrowser 会把这个列表直接交给 Playwright。加入 --no-sandbox 后，
    # Playwright 自己的默认 --no-sandbox 也会被过滤掉。
    if "--no-sandbox" not in CLOAKBROWSER_IGNORE_DEFAULT_ARGS:
        CLOAKBROWSER_IGNORE_DEFAULT_ARGS.append("--no-sandbox")

    return {
        # 回退模式也关闭合成指纹，保留机器真实且稳定的屏幕/GPU 参数。
        # 实测 CloakBrowser 仍可能触发抖音验证码 5202，因此这里只作为
        # 系统 Chrome 不可用时的兼容兜底。
        "stealth_args": False,
        "args": [],
    }


class BrowserManager:
    """浏览器生命周期管理器，包含进程存活检测和自动恢复。"""

    def __init__(
        self,
        health_check_interval: float | None = None,
        restart_retry_interval: float | None = None,
    ):
        self._context = None
        self._playwright = None
        self._browser = None
        self._browser_process: asyncio.subprocess.Process | None = None
        self._browser_label = "浏览器"
        self._lifecycle_lock = asyncio.Lock()
        self._restart_event = asyncio.Event()
        self._watchdog_task: asyncio.Task | None = None
        self._desired_running = False
        self._generation = 0
        self._health_check_interval = max(
            0.1,
            float(
                health_check_interval
                if health_check_interval is not None
                else get("cloak_browser.health_check_interval", 3)
            ),
        )
        self._restart_retry_interval = max(
            0.1,
            float(
                restart_retry_interval
                if restart_retry_interval is not None
                else get("cloak_browser.restart_retry_interval", 3)
            ),
        )

    @property
    def generation(self) -> int:
        """浏览器每成功启动一次递增；调用方可据此发现页面已经失效。"""
        return self._generation

    def is_running(self) -> bool:
        """同步检查当前 Context 对应的浏览器进程是否仍连接。"""
        context = self._context
        if context is None:
            return False
        try:
            browser = self._browser or context.browser
            process = self._browser_process
            if process is not None and process.returncode is not None:
                return False
            # persistent context 正常情况下总能取得 Browser；保留无 Browser
            # 的兼容分支，避免第三方封装行为变化造成误判。
            return browser.is_connected() if browser is not None else bool(context.pages)
        except Exception:
            return False

    async def start(self) -> "BrowserManager":
        """
        启动浏览器并开启后台看门狗。

        初次启动失败会向调用方抛错，但看门狗仍会按配置间隔继续重试。
        """
        self._desired_running = True
        try:
            await self.ensure_running()
        finally:
            self._start_watchdog()
        return self

    async def ensure_running(self) -> bool:
        """确保浏览器进程存在；发生重启返回 True，原进程健康返回 False。"""
        if not self._desired_running:
            raise RuntimeError("BrowserManager 已停止")

        async with self._lifecycle_lock:
            if self.is_running():
                return False

            await self._close_context_locked()
            await self._launch_context_locked()
            return True

    async def restart(self) -> "BrowserManager":
        """主动重启浏览器，但保持看门狗继续运行。"""
        self._desired_running = True
        async with self._lifecycle_lock:
            await self._close_context_locked()
            await self._launch_context_locked()
        self._start_watchdog()
        return self

    async def _launch_context_locked(self) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        proxy_url = get("cloak_browser.proxy", "")
        chrome_executable = _find_native_chrome_executable()

        if chrome_executable is not None:
            self._browser_label = "系统 Chrome"
            logger.info(
                f"启动系统 Chrome（{chrome_executable}），Profile 目录：{PROFILE_DIR}"
            )
            playwright = await async_playwright().start()
            # Playwright/Patchright 的直接 launch 都会加入导致 Chrome 顶部
            # 显示“不受支持”的 AutomationControlled 参数。改为应用直接
            # 启动 Chrome，再通过一个随机的非零本机调试端口连接：既不
            # 传该参数，也不会因 remote-debugging-port=0 设置 webdriver。
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
                port_socket.bind(("127.0.0.1", 0))
                debug_port = int(port_socket.getsockname()[1])
            chrome_args = [
                str(chrome_executable),
                f"--user-data-dir={PROFILE_DIR}",
                f"--remote-debugging-port={debug_port}",
                "--remote-debugging-address=127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=zh-CN",
            ]
            if proxy_url:
                chrome_args.append(f"--proxy-server={proxy_url}")
                logger.info(f"已配置代理：{proxy_url}")
            chrome_args.append("about:blank")
            try:
                process = await asyncio.create_subprocess_exec(
                    *chrome_args,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                browser = None
                endpoint = f"http://127.0.0.1:{debug_port}"
                deadline = asyncio.get_running_loop().time() + 15
                while asyncio.get_running_loop().time() < deadline:
                    if process.returncode is not None:
                        raise RuntimeError(
                            f"系统 Chrome 提前退出（code={process.returncode}）"
                        )
                    try:
                        browser = await playwright.chromium.connect_over_cdp(
                            endpoint,
                            timeout=1_000,
                        )
                        break
                    except Exception:
                        await asyncio.sleep(0.1)
                if browser is None:
                    raise RuntimeError("等待系统 Chrome 调试端口超时")
                if not browser.contexts:
                    raise RuntimeError("系统 Chrome 未提供默认 Profile Context")
                context = browser.contexts[0]
            except Exception:
                if "process" in locals() and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                await playwright.stop()
                raise
            self._playwright = playwright
            self._browser = browser
            self._browser_process = process
        else:
            self._browser_label = "CloakBrowser（回退）"
            logger.warning("未找到系统 Chrome，回退使用 CloakBrowser")
            launch_kwargs: dict[str, Any] = dict(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                humanize=True,
                human_preset="careful",
                locale="zh-CN",
                **_native_sandboxed_launch_options(),
            )
            if proxy_url:
                launch_kwargs["proxy"] = proxy_url
                launch_kwargs["geoip"] = True
                logger.info(f"已配置代理：{proxy_url}")
            context = await launch_persistent_context_async(**launch_kwargs)
            self._browser = context.browser

        self._context = context
        self._generation += 1

        # 用户关闭窗口、进程崩溃或被系统杀死时，persistent context 会触发
        # close。事件只负责唤醒看门狗，真正的重启统一在生命周期锁内完成。
        context.on("close", lambda: self._on_context_closed(context))
        logger.info(f"{self._browser_label}启动成功（实例 #{self._generation}）")

    def _on_context_closed(self, context) -> None:
        if context is not self._context:
            return
        self._context = None
        if self._desired_running:
            logger.warning(f"检测到{self._browser_label}已关闭，准备自动恢复...")
            self._restart_event.set()

    def _start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(),
                name="cloakbrowser-watchdog",
            )

    async def _watchdog_loop(self) -> None:
        """断连事件触发即时恢复，并定期兜底检查进程连接状态。"""
        while self._desired_running:
            self._restart_event.clear()
            delay = self._health_check_interval
            try:
                restarted = await self.ensure_running()
                if restarted:
                    logger.info(f"{self._browser_label}自动恢复成功")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = self._restart_retry_interval
                logger.error(
                    f"浏览器自动恢复失败，{delay:g} 秒后重试：{exc}"
                )

            if not self._desired_running:
                break
            try:
                await asyncio.wait_for(self._restart_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _close_context_locked(self) -> None:
        context = self._context
        playwright = self._playwright
        browser = self._browser
        process = self._browser_process
        # 先摘除引用，避免 close 事件被当成用户手动关闭并重复触发重启。
        self._context = None
        self._playwright = None
        self._browser = None
        self._browser_process = None
        if process is not None:
            if browser is not None:
                try:
                    await browser.close()
                except Exception as exc:
                    logger.debug(f"断开系统 Chrome 调试连接时忽略异常：{exc}")
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        elif context is not None:
            try:
                await context.close()
            except Exception as exc:
                logger.debug(f"清理已失效的浏览器 Context 时忽略异常：{exc}")
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:
                logger.debug(f"停止浏览器驱动时忽略异常：{exc}")

    async def _context_operation(
        self,
        name: str,
        operation: Callable[[Any], Awaitable[_T]],
    ) -> _T:
        """执行 Context 操作；断连或 Context 损坏时重启后重试一次。"""
        await self.ensure_running()
        context = self._context
        try:
            return await operation(context)
        except Exception as exc:
            logger.warning(f"{name} 失败，重启浏览器后重试：{exc}")
            async with self._lifecycle_lock:
                # close 事件可能已经让看门狗抢先完成恢复。此时直接复用新
                # Context，避免把刚拉起的健康进程再次关闭。
                if self._context is context or not self.is_running():
                    await self._close_context_locked()
                    await self._launch_context_locked()
            return await operation(self._context)

    @staticmethod
    def _configure_page(page) -> None:
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(60_000)
        attach_captcha_diagnostics(page)

    async def new_page(self):
        """创建新标签页；浏览器已被关闭时自动恢复后重试。"""
        page = await self._context_operation(
            "new_page",
            lambda context: context.new_page(),
        )
        self._configure_page(page)
        await attach_captcha_cdp_diagnostics(page)
        return page

    async def get_cookies(self) -> list[dict]:
        """获取当前 Context 下的所有 Cookie，断连时自动恢复。"""
        return await self._context_operation(
            "读取 Cookie",
            lambda context: context.cookies(),
        )

    async def get_cookie_dict(self) -> dict:
        """以 {name: value} 格式返回 Cookie，方便构造请求头。"""
        cookies = await self.get_cookies()
        return {c["name"]: c["value"] for c in cookies}

    async def save_storage_state(self, path: str | None = None):
        """持久化 Cookie + localStorage 到 JSON 文件。"""
        save_path = path or str(ROOT_DIR / "data" / "storage_state.json")

        async def save(context):
            await context.storage_state(path=save_path)

        await self._context_operation("保存 Storage State", save)
        logger.info(f"已保存 Storage State：{save_path}")

    async def clear_auth_state(self) -> None:
        """清除抖音 Cookie 与页面站点存储，但保持浏览器进程继续运行。"""

        async def clear(context):
            for page in list(context.pages):
                try:
                    if "douyin.com" not in page.url:
                        continue
                    await page.evaluate(
                        """async () => {
                            localStorage.clear();
                            sessionStorage.clear();
                            if ('caches' in window) {
                                for (const key of await caches.keys()) await caches.delete(key);
                            }
                            if ('serviceWorker' in navigator) {
                                for (const reg of await navigator.serviceWorker.getRegistrations()) {
                                    await reg.unregister();
                                }
                            }
                            if (indexedDB.databases) {
                                for (const db of await indexedDB.databases()) {
                                    if (db.name) indexedDB.deleteDatabase(db.name);
                                }
                            }
                        }"""
                    )
                except Exception as exc:
                    logger.debug(f"清除页面站点存储时忽略异常：{exc}")
            await context.clear_cookies()

        await self._context_operation("清除登录态", clear)
        logger.info("已清除浏览器中的抖音登录态，浏览器进程保持运行")

    async def close(self):
        """停止看门狗并关闭浏览器；正常服务退出时调用。"""
        self._desired_running = False
        self._restart_event.set()

        watchdog = self._watchdog_task
        self._watchdog_task = None
        if watchdog is not None and watchdog is not asyncio.current_task():
            try:
                await watchdog
            except asyncio.CancelledError:
                pass

        async with self._lifecycle_lock:
            await self._close_context_locked()
        logger.info("浏览器已关闭")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()


# ─── 全局单例 ───────────────────────────────────────────────────────────────

_manager: BrowserManager | None = None
_manager_lock = asyncio.Lock()


async def get_browser() -> BrowserManager:
    """获取全局 BrowserManager；并发调用也只会启动一个浏览器进程。"""
    global _manager
    async with _manager_lock:
        if _manager is None:
            _manager = BrowserManager()
            await _manager.start()
        else:
            _manager._desired_running = True
            await _manager.ensure_running()
            _manager._start_watchdog()
        return _manager


async def restart_browser() -> BrowserManager:
    """重启全局浏览器并继续保持自动恢复。"""
    manager = await get_browser()
    return await manager.restart()


async def close_browser():
    """关闭全局浏览器实例和它的看门狗。"""
    global _manager
    async with _manager_lock:
        manager = _manager
        _manager = None
        if manager is not None:
            await manager.close()
