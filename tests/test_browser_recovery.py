import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from crawler.core.browser import BrowserManager
from crawler.core.login import _poll_login_cookies


class _FakeBrowser:
    def __init__(self):
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


class _FakePage:
    def __init__(self):
        self.default_timeout = None
        self.navigation_timeout = None

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout):
        self.navigation_timeout = timeout


class _FakeContext:
    def __init__(self):
        self.browser = _FakeBrowser()
        self.pages = []
        self._listeners = {}
        self.closed = False
        self.cookies_result = []

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


if __name__ == "__main__":
    unittest.main()
