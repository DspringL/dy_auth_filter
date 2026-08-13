import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from crawler.core.browser import (
    CLOAKBROWSER_IGNORE_DEFAULT_ARGS,
    BrowserManager,
)
from crawler.core.login import _poll_login_cookies


class _FakeBrowser:
    def __init__(self):
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


class _FakeCdpBrowser(_FakeBrowser):
    def __init__(self, context):
        super().__init__()
        self.contexts = [context]

    async def close(self):
        self.connected = False


class _FakeProcess:
    def __init__(self):
        self.pid = 12345
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _FakePage:
    def __init__(self):
        self.default_timeout = None
        self.navigation_timeout = None
        self.url = "about:blank"
        self.evaluated = False
        self._listeners = {}

    def on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout):
        self.navigation_timeout = timeout

    async def evaluate(self, _script):
        self.evaluated = True


class _FakeContext:
    def __init__(self):
        self.browser = _FakeBrowser()
        self.pages = []
        self._listeners = {}
        self.closed = False
        self.cookies_result = []
        self.cookies_cleared = False

    def on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)

    async def new_page(self):
        if not self.browser.connected:
            raise RuntimeError("Target page, context or browser has been closed")
        page = _FakePage()
        self.pages.append(page)
        return page

    async def cookies(self):
        if not self.browser.connected:
            raise RuntimeError("browser disconnected")
        return self.cookies_result

    async def clear_cookies(self):
        self.cookies_cleared = True
        self.cookies_result = []

    async def storage_state(self, path):
        if not self.browser.connected:
            raise RuntimeError("browser disconnected")

    async def close(self):
        self.closed = True
        self.browser.connected = False
        self._emit_close()

    def manual_close(self):
        self.closed = True
        self.browser.connected = False
        self._emit_close()

    def _emit_close(self):
        for callback in self._listeners.get("close", []):
            callback()


class BrowserRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 绝大多数测试覆盖 CloakBrowser 回退路径，避免开发机是否安装
        # Chrome 影响测试结果。
        self.chrome_lookup = patch(
            "crawler.core.browser._find_native_chrome_executable",
            return_value=None,
        )
        self.chrome_lookup.start()
        self.addCleanup(self.chrome_lookup.stop)

    async def _wait_until(self, predicate, timeout=1.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("condition was not met before timeout")
            await asyncio.sleep(0.01)

    async def test_watchdog_restarts_after_user_closes_browser(self):
        contexts = []

        async def launch(**_kwargs):
            context = _FakeContext()
            contexts.append(context)
            return context

        with patch(
            "crawler.core.browser.launch_persistent_context_async",
            side_effect=launch,
        ):
            manager = BrowserManager(
                health_check_interval=0.1,
                restart_retry_interval=0.1,
            )
            await manager.start()
            self.assertTrue(manager.is_running())
            self.assertEqual(manager.generation, 1)

            contexts[0].manual_close()
            await self._wait_until(lambda: manager.generation == 2)

            self.assertTrue(manager.is_running())
            self.assertIs(manager._context, contexts[1])

            await manager.close()
            launch_count = len(contexts)
            await asyncio.sleep(0.15)
            self.assertEqual(len(contexts), launch_count)

    async def test_launch_uses_native_device_properties_with_sandbox(self):
        launch_kwargs = {}

        async def launch(**kwargs):
            launch_kwargs.update(kwargs)
            return _FakeContext()

        with patch(
            "crawler.core.browser.launch_persistent_context_async",
            side_effect=launch,
        ):
            manager = BrowserManager(health_check_interval=10)
            await manager.start()
            await manager.close()

        self.assertEqual(launch_kwargs["locale"], "zh-CN")
        self.assertFalse(launch_kwargs["stealth_args"])
        self.assertNotIn("--no-sandbox", launch_kwargs["args"])
        self.assertIn("--no-sandbox", CLOAKBROWSER_IGNORE_DEFAULT_ARGS)
        self.assertEqual(launch_kwargs["args"], [])

    async def test_native_chrome_is_preferred_and_keeps_sandbox_enabled(self):
        context = _FakeContext()
        cdp_browser = _FakeCdpBrowser(context)
        process = _FakeProcess()
        playwright_driver = MagicMock()
        playwright_driver.chromium.connect_over_cdp = AsyncMock(
            return_value=cdp_browser
        )
        playwright_driver.stop = AsyncMock()
        starter = MagicMock()
        starter.start = AsyncMock(return_value=playwright_driver)
        chrome_path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        spawned_args = []

        async def spawn(*args, **_kwargs):
            spawned_args.extend(args)
            return process

        with (
            patch(
                "crawler.core.browser._find_native_chrome_executable",
                return_value=chrome_path,
            ),
            patch("crawler.core.browser.async_playwright", return_value=starter),
            patch(
                "crawler.core.browser.asyncio.create_subprocess_exec",
                side_effect=spawn,
            ),
        ):
            manager = BrowserManager(health_check_interval=10)
            await manager.start()
            self.assertEqual(manager._browser_label, "系统 Chrome")
            await manager.close()

        command = " ".join(str(arg) for arg in spawned_args)
        self.assertTrue(command.startswith(str(chrome_path)))
        self.assertIn("--remote-debugging-port=", command)
        self.assertNotIn("--no-sandbox", command)
        self.assertNotIn("--disable-blink-features=AutomationControlled", command)
        self.assertNotIn("--enable-automation", command)
        self.assertTrue(process.terminated)
        playwright_driver.stop.assert_awaited_once()

    async def test_new_page_recovers_when_disconnect_event_is_missed(self):
        contexts = []

        async def launch(**_kwargs):
            context = _FakeContext()
            contexts.append(context)
            return context

        with patch(
            "crawler.core.browser.launch_persistent_context_async",
            side_effect=launch,
        ):
            manager = BrowserManager(health_check_interval=10)
            await manager.start()
            # 模拟进程被强制杀死但 close 事件尚未送达。
            contexts[0].browser.connected = False

            page = await manager.new_page()

            self.assertEqual(manager.generation, 2)
            self.assertEqual(page.default_timeout, 30_000)
            self.assertEqual(page.navigation_timeout, 60_000)
            await manager.close()

    async def test_login_poll_keeps_scan_page_alive_until_cookie_arrives(self):
        manager = AsyncMock()
        manager.get_cookies.side_effect = [
            [],
            [{"name": "sessionid", "value": "logged-in"}],
        ]
        ensure_page = AsyncMock()

        with patch(
            "crawler.core.login.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            logged_in = await _poll_login_cookies(
                manager,
                timeout=1,
                ensure_login_page=ensure_page,
            )

        self.assertTrue(logged_in)
        ensure_page.assert_awaited_once()

    async def test_logout_clears_auth_without_stopping_browser(self):
        context = _FakeContext()
        douyin_page = _FakePage()
        douyin_page.url = "https://www.douyin.com/"
        context.pages.append(douyin_page)

        manager = BrowserManager()
        manager._context = context
        manager._desired_running = True

        await manager.clear_auth_state()

        self.assertTrue(context.cookies_cleared)
        self.assertTrue(douyin_page.evaluated)
        self.assertTrue(manager.is_running())


if __name__ == "__main__":
    unittest.main()
