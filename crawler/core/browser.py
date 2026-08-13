"""
CloakBrowser 连接管理器。

- 使用持久化 Profile 保留登录态 Cookie
- 后台看门狗持续检查浏览器进程
- 用户手动关闭浏览器后自动重启
- 所有 Context 操作在断连时自动恢复一次
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cloakbrowser import launch_persistent_context_async
from loguru import logger

from crawler.utils.config import ROOT_DIR, get


# 持久化浏览器 Profile 目录（保存登录态 Cookie）
PROFILE_DIR = ROOT_DIR / "data" / "browser_profile"

_T = TypeVar("_T")


class BrowserManager:
    """CloakBrowser 生命周期管理器，包含进程存活检测和自动恢复。"""

    def __init__(
        self,
        health_check_interval: float | None = None,
        restart_retry_interval: float | None = None,
    ):
        self._context = None
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
            browser = context.browser
            # persistent context 正常情况下总能取得 Browser；保留无 Browser
            # 的兼容分支，避免第三方封装行为变化造成误判。
            return browser.is_connected() if browser is not None else bool(context.pages)
        except Exception:
            return False

    async def start(self) -> "BrowserManager":
        """
        启动 CloakBrowser 并开启后台看门狗。

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
        logger.info(f"启动 CloakBrowser，Profile 目录：{PROFILE_DIR}")

        proxy_url = get("cloak_browser.proxy", "")
        launch_kwargs: dict[str, Any] = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=False,          # 抖音检测 headless，必须有界面
            humanize=True,           # 启用人类行为模拟（鼠标曲线、键盘时序）
            human_preset="careful", # 更自然的行为
            args=[
                "--fingerprint=88888",
                "--fingerprint-platform=windows",
                "--fingerprint-windows-font-metrics",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        if proxy_url:
            launch_kwargs["proxy"] = proxy_url
            launch_kwargs["geoip"] = True
            logger.info(f"已配置代理：{proxy_url}")

        context = await launch_persistent_context_async(**launch_kwargs)
        self._context = context
        self._generation += 1

        # 用户关闭窗口、进程崩溃或被系统杀死时，persistent context 会触发
        # close。事件只负责唤醒看门狗，真正的重启统一在生命周期锁内完成。
        context.on("close", lambda: self._on_context_closed(context))
        logger.info(f"CloakBrowser 启动成功（实例 #{self._generation}）")

    def _on_context_closed(self, context) -> None:
        if context is not self._context:
            return
        self._context = None
        if self._desired_running:
            logger.warning("检测到 CloakBrowser 已关闭，准备自动恢复...")
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
                    logger.info("CloakBrowser 自动恢复成功")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = self._restart_retry_interval
                logger.error(
                    f"CloakBrowser 自动恢复失败，{delay:g} 秒后重试：{exc}"
                )

            if not self._desired_running:
                break
            try:
                await asyncio.wait_for(self._restart_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _close_context_locked(self) -> None:
        context = self._context
        # 先摘除引用，避免 close 事件被当成用户手动关闭并重复触发重启。
        self._context = None
        if context is None:
            return
        try:
            await context.close()
        except Exception as exc:
            logger.debug(f"清理已失效的浏览器 Context 时忽略异常：{exc}")

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

    async def new_page(self):
        """创建新标签页；浏览器已被关闭时自动恢复后重试。"""
        page = await self._context_operation(
            "new_page",
            lambda context: context.new_page(),
        )
        self._configure_page(page)
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
        logger.info("CloakBrowser 已关闭")

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
