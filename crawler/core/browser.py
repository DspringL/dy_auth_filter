"""
CloakBrowser 连接管理器
- 支持持久化 Profile（保留登录态 Cookie）
- 启用人类行为模拟（humanize=True）
- 支持代理配置
"""
import asyncio
from pathlib import Path
from loguru import logger
from cloakbrowser import launch_persistent_context_async
from crawler.utils.config import get, ROOT_DIR


# 持久化浏览器 Profile 目录（保存登录态 Cookie）
PROFILE_DIR = ROOT_DIR / "data" / "browser_profile"


class BrowserManager:
    """CloakBrowser 生命周期管理器（异步单例）"""

    def __init__(self):
        self._context = None
        self._page = None

    async def start(self) -> "BrowserManager":
        """
        启动 CloakBrowser，使用持久化 Profile 保留登录态。
        首次运行时需要扫码登录，之后复用 Cookie 免登录。
        """
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"启动 CloakBrowser，Profile 目录：{PROFILE_DIR}")

        # 读取代理配置（可选）
        proxy_url = get("cloak_browser.proxy", "")

        launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=False,          # 抖音检测 headless，必须有界面
            humanize=True,           # 启用人类行为模拟（鼠标曲线、键盘时序）
            human_preset="careful",  # 更自然的行为
            args=[
                "--fingerprint=88888",            # 固定指纹种子，避免多次启动被识别为不同设备
                "--fingerprint-platform=windows", # 伪装 Windows 平台
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

        self._context = await launch_persistent_context_async(**launch_kwargs)
        logger.info("CloakBrowser 启动成功")
        return self

    async def new_page(self):
        """创建新标签页，自动处理 context 失效情况"""
        if self._context is None:
            raise RuntimeError("BrowserManager 未启动，请先调用 start()")
        try:
            page = await self._context.new_page()
            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(60_000)
            return page
        except Exception as e:
            logger.warning(f"new_page 失败（context 可能已关闭），尝试重启浏览器：{e}")
            await self.close()
            await self.start()
            page = await self._context.new_page()
            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(60_000)
            return page

    async def get_cookies(self) -> list[dict]:
        """获取当前 Context 下的所有 Cookie"""
        if self._context is None:
            return []
        return await self._context.cookies()

    async def get_cookie_dict(self) -> dict:
        """以 {name: value} 格式返回 Cookie，方便构造请求头"""
        cookies = await self.get_cookies()
        return {c["name"]: c["value"] for c in cookies}

    async def save_storage_state(self, path: str | None = None):
        """持久化 Cookie + localStorage 到 JSON 文件（用于 XHR 请求复用）"""
        if self._context is None:
            return
        save_path = path or str(ROOT_DIR / "data" / "storage_state.json")
        await self._context.storage_state(path=save_path)
        logger.info(f"已保存 Storage State：{save_path}")

    async def close(self):
        """关闭浏览器，保存 Profile"""
        if self._context:
            await self._context.close()
            self._context = None
            logger.info("CloakBrowser 已关闭")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()


# ─── 全局单例 ───────────────────────────────────────────────────────────────

_manager: BrowserManager | None = None


async def get_browser() -> BrowserManager:
    """获取全局 BrowserManager 单例，自动检测 context 是否存活"""
    global _manager
    if _manager is None or _manager._context is None:
        _manager = BrowserManager()
        await _manager.start()
        return _manager

    # 检测 context 是否仍然存活（通过 new_page 测试）
    try:
        # 用 context.pages 属性探测是否已关闭（Playwright 关闭的 context 访问属性会抛异常）
        _ = _manager._context.pages
    except Exception:
        logger.warning("BrowserManager context 已失效，重新启动...")
        try:
            await _manager.close()
        except Exception:
            pass
        _manager = BrowserManager()
        await _manager.start()

    return _manager


async def close_browser():
    """关闭全局浏览器实例"""
    global _manager
    if _manager:
        await _manager.close()
        _manager = None
