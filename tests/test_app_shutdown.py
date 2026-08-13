import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.main import prepare_desktop_shutdown
from backend.api.routes import router
import crawler.core.browser as browser_module


class _FakeBrowserManager:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class AppShutdownTests(unittest.IsolatedAsyncioTestCase):
    def test_logout_keeps_compatibility_routes(self):
        logout_paths = {
            route.path
            for route in router.routes
            if "POST" in getattr(route, "methods", set())
            and getattr(route, "endpoint", None).__name__ == "logout_douyin"
        }
        self.assertEqual(
            logout_paths,
            {"/api/login/logout", "/api/logout", "/api/auth/logout"},
        )

    async def test_shutdown_rejects_missing_desktop_token(self):
        with patch.dict(os.environ, {"DYAUTH_SHUTDOWN_TOKEN": "desktop-secret"}):
            with self.assertRaises(HTTPException) as raised:
                await prepare_desktop_shutdown(None)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_shutdown_closes_browser_with_matching_token(self):
        manager = _FakeBrowserManager()
        old_manager = browser_module._manager
        browser_module._manager = manager
        try:
            with patch.dict(os.environ, {"DYAUTH_SHUTDOWN_TOKEN": "desktop-secret"}):
                result = await prepare_desktop_shutdown("desktop-secret")
        finally:
            browser_module._manager = old_manager

        self.assertEqual(result, {"status": "ready"})
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()
