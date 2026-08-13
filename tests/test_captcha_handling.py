import tempfile
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crawler.utils.captcha import (
    attach_captcha_diagnostics,
    capture_captcha_screenshot,
    handle_captcha_if_present,
    is_captcha_load_failed,
    is_captcha_present,
    latest_captcha_screenshot,
    save_captcha_diagnostics,
    wait_for_captcha_clear,
    wait_for_queue_with_captcha,
)


class _Element:
    def __init__(self, *, src: str = "", visible: bool = False):
        self.src = src
        self.visible = visible

    async def get_attribute(self, name):
        return self.src if name == "src" else None

    async def is_visible(self):
        return self.visible


class _Locator:
    def __init__(self, elements, text=""):
        self.elements = elements
        self.text = text

    async def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]

    async def inner_text(self, timeout=None):
        return self.text


class _Page:
    def __init__(self, elements=None, body_text="", frames=None):
        self.url = "https://www.douyin.com/search/test?type=video"
        self.elements = elements or []
        self.body_text = body_text
        self.frames = frames or []
        self._listeners = {}

    def on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)

    def locator(self, selector):
        if selector == "body":
            return _Locator([], self.body_text)
        return _Locator(self.elements)

    async def screenshot(self, path, full_page=False):
        self.screenshot_full_page = full_page
        Path(path).write_bytes(b"png")

    async def evaluate(self, _script):
        return {"userAgent": "test", "platform": "MacIntel"}


class CaptchaDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_nocaptcha_iframe_is_not_a_challenge(self):
        page = _Page([
            _Element(
                src="https://static.example/rc-verifycenter/rmc-nocaptcha/index.html",
                visible=False,
            )
        ])

        self.assertFalse(await is_captcha_present(page))

    async def test_visible_captcha_iframe_is_detected(self):
        page = _Page([
            _Element(
                src="https://rmc.bytedance.com/verifycenter/captcha/v2",
                visible=True,
            )
        ])

        self.assertTrue(await is_captcha_present(page))

    async def test_image_load_failure_is_still_a_challenge(self):
        page = _Page(body_text="图片加载失败，请刷新重试或换个网络 [5202]")

        self.assertTrue(await is_captcha_load_failed(page))
        self.assertTrue(await is_captcha_present(page))

    async def test_image_load_failure_inside_cross_origin_frame_is_detected(self):
        captcha_frame = _Page(
            body_text="图片加载失败，请刷新重试或换个网络 [5202]"
        )
        page = _Page(body_text="搜索结果", frames=[captcha_frame])

        self.assertTrue(await is_captcha_load_failed(page))

    async def test_captcha_screenshot_is_saved_for_manual_handling(self):
        page = _Page()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "crawler.utils.captcha.CAPTCHA_SCREENSHOT_DIR",
                Path(temp_dir),
            ):
                screenshot = await capture_captcha_screenshot(page, "test")

            self.assertIsNotNone(screenshot)
            self.assertTrue(screenshot.exists())
            self.assertEqual(latest_captcha_screenshot(), screenshot)
            self.assertFalse(page.screenshot_full_page)

    async def test_diagnostics_strip_query_tokens_and_save_to_download_dir(self):
        page = _Page()
        attach_captcha_diagnostics(page)

        class _Request:
            url = "https://verify.example/captcha/image?token=secret"
            resource_type = "image"

        class _Response:
            url = _Request.url
            status = 403
            request = _Request()

            async def all_headers(self):
                return {
                    "content-type": "image/jpeg",
                    "access-control-allow-origin": "*",
                }

            async def body(self):
                return b"jpeg-test"

        await page._listeners["response"][0](_Response())

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "crawler.utils.captcha.CAPTCHA_SCREENSHOT_DIR",
                Path(temp_dir),
            ):
                diagnostic = await save_captcha_diagnostics(page, "test")

            content = diagnostic.read_text(encoding="utf-8")
            self.assertIn("https://verify.example/captcha/image", content)
            self.assertNotIn("secret", content)

    async def test_manual_challenge_emits_required_then_cleared(self):
        events = []

        def on_event(event, screenshot):
            events.append((event, screenshot))

        screenshot = Path("/tmp/captcha-test.png")
        with (
            patch(
                "crawler.utils.captcha.is_captcha_present",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "crawler.utils.captcha.capture_captcha_screenshot",
                new_callable=AsyncMock,
                return_value=screenshot,
            ),
            patch(
                "crawler.utils.captcha.wait_for_captcha_clear",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            handled = await handle_captcha_if_present(
                _Page(),
                reason="unit-test",
                event_handler=on_event,
            )

        self.assertTrue(handled)
        self.assertEqual(events, [("required", screenshot), ("cleared", screenshot)])

    async def test_refresh_flicker_does_not_clear_challenge(self):
        with (
            patch(
                "crawler.utils.captcha.is_captcha_load_failed",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "crawler.utils.captcha.is_captcha_present",
                new_callable=AsyncMock,
                side_effect=[False, True, False, False, False],
            ) as present,
            patch("crawler.utils.captcha.asyncio.sleep", new_callable=AsyncMock),
        ):
            cleared = await wait_for_captcha_clear(_Page(), wait_seconds=10)

        self.assertTrue(cleared)
        self.assertEqual(present.await_count, 5)

    async def test_load_failure_updates_screenshot_and_keeps_waiting(self):
        events = []
        screenshot = Path("/tmp/captcha-load-failed.png")

        with (
            patch(
                "crawler.utils.captcha.is_captcha_load_failed",
                new_callable=AsyncMock,
                side_effect=[True, False, False, False],
            ),
            patch(
                "crawler.utils.captcha.is_captcha_present",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "crawler.utils.captcha.capture_captcha_screenshot",
                new_callable=AsyncMock,
                return_value=screenshot,
            ),
            patch(
                "crawler.utils.captcha.save_captcha_diagnostics",
                new_callable=AsyncMock,
            ),
            patch("crawler.utils.captcha.asyncio.sleep", new_callable=AsyncMock),
        ):
            cleared = await wait_for_captcha_clear(
                _Page(),
                wait_seconds=10,
                event_handler=lambda event, path: events.append((event, path)),
            )

        self.assertTrue(cleared)
        self.assertEqual(events, [("load_failed", screenshot)])

    async def test_xhr_wait_resumes_after_manual_challenge(self):
        queue = asyncio.Queue()
        await queue.put({"ok": True})

        with patch(
            "crawler.utils.captcha.handle_captcha_if_present",
            new_callable=AsyncMock,
            side_effect=[True, False],
        ):
            result = await wait_for_queue_with_captcha(
                _Page(),
                queue,
                timeout=1,
                reason="unit-test",
            )

        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
