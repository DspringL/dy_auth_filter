import unittest
from unittest.mock import AsyncMock, patch

from crawler.services.browser_actions import render_single_line, video_id_from_href
from crawler.services.comment import CommentService
from crawler.services.contact import CombinedContactService
from crawler.services.dm import DMService
from crawler.services.search import KOLProfile


class _DMRecord:
    def __init__(self, sent: set[str] | None = None, today: int = 0):
        self.sent = set(sent or ())
        self.today = today

    def already_sent(self, sec_user_id: str) -> bool:
        return sec_user_id in self.sent

    def today_count(self) -> int:
        return self.today

    def record(self, sec_user_id: str, _nickname: str) -> None:
        self.sent.add(sec_user_id)
        self.today += 1


class _CommentRecord:
    def __init__(self, commented: set[str] | None = None, today: int = 0):
        self.commented = set(commented or ())
        self.today = today

    def already_commented(self, sec_user_id: str) -> bool:
        return sec_user_id in self.commented

    def today_count(self) -> int:
        return self.today

    def record(self, sec_user_id: str, _nickname: str, _aweme_id: str) -> None:
        self.commented.add(sec_user_id)
        self.today += 1


def _profile(user_id: str) -> KOLProfile:
    return KOLProfile(
        sec_user_id=user_id,
        nickname=user_id,
        profile_url=f"https://www.douyin.com/user/{user_id}",
    )


class BrowserActionTests(unittest.TestCase):
    def test_render_and_id_extraction(self):
        self.assertEqual(render_single_line("hi\n{nickname}\r!", "测试"), "hi 测试 !")
        self.assertEqual(video_id_from_href("https://www.douyin.com/video/12345?x=1"), "12345")


class BatchSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_contact_finishes_both_actions_before_next_profile(self):
        events: list[str] = []
        progress: list[tuple[str, str, int, int]] = []

        dm_service = object.__new__(DMService)
        dm_service._record = _DMRecord()

        async def send_dm(profile):
            events.append(f"dm:{profile.sec_user_id}")
            profile.latest_aweme_id = f"aweme-{profile.sec_user_id}"
            return True

        dm_service._send_one = AsyncMock(side_effect=send_dm)

        comment_service = object.__new__(CommentService)
        comment_service._record = _CommentRecord()

        async def send_comment(profile):
            self.assertEqual(profile.latest_aweme_id, f"aweme-{profile.sec_user_id}")
            events.append(f"comment:{profile.sec_user_id}")
            return profile.latest_aweme_id, True

        comment_service._comment_one = AsyncMock(side_effect=send_comment)
        service = CombinedContactService(dm_service, comment_service)

        def on_progress(_index, _total, _profile, stage, status, dm_stats, comment_stats):
            progress.append((stage, status, dm_stats["sent"], comment_stats["sent"]))

        with (
            patch("crawler.services.contact.dm_daily_limit", return_value=10),
            patch("crawler.services.contact.comment_daily_limit", return_value=10),
            patch("crawler.services.contact.dm_interval", return_value=(10, 30)),
            patch("crawler.services.contact.comment_interval", return_value=(10, 30)),
            patch("crawler.services.contact.random.randint", side_effect=[12, 18]),
            patch("crawler.services.contact.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await service.send_batch(
                [_profile("a"), _profile("b")],
                on_progress=on_progress,
            )

        self.assertEqual(events, ["dm:a", "comment:a", "dm:b", "comment:b"])
        self.assertEqual(result["dm"]["sent"], 2)
        self.assertEqual(result["comment"]["sent"], 2)
        self.assertEqual(
            progress,
            [
                ("dm", "sent", 1, 0),
                ("comment", "sent", 1, 1),
                ("dm", "sent", 2, 1),
                ("comment", "sent", 2, 2),
            ],
        )
        sleep.assert_awaited_once_with(18)

    async def test_combined_contact_keeps_channel_quotas_and_duplicate_rules(self):
        events: list[str] = []
        dm_service = object.__new__(DMService)
        dm_service._record = _DMRecord(sent={"a"})
        dm_service._send_one = AsyncMock(
            side_effect=lambda profile: events.append(f"dm:{profile.sec_user_id}") or True
        )
        comment_service = object.__new__(CommentService)
        comment_service._record = _CommentRecord(commented={"b"})
        comment_service._comment_one = AsyncMock(
            side_effect=lambda profile: (
                events.append(f"comment:{profile.sec_user_id}")
                or (f"aweme-{profile.sec_user_id}", True)
            )
        )

        with (
            patch("crawler.services.contact.dm_daily_limit", return_value=1),
            patch("crawler.services.contact.comment_daily_limit", return_value=1),
            patch("crawler.services.contact.dm_interval", return_value=(10, 30)),
            patch("crawler.services.contact.comment_interval", return_value=(10, 30)),
            patch("crawler.services.contact.random.randint", return_value=10),
            patch("crawler.services.contact.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await CombinedContactService(
                dm_service,
                comment_service,
            ).send_batch([_profile("a"), _profile("b"), _profile("c")])

        self.assertEqual(events, ["comment:a", "dm:b"])
        self.assertEqual(
            result,
            {
                "dm": {"sent": 1, "skipped_dup": 1, "skipped_limit": 1, "failed": 0},
                "comment": {"sent": 1, "skipped_dup": 1, "skipped_limit": 1, "failed": 0},
            },
        )
        sleep.assert_awaited_once_with(10)

    async def test_dm_deduplicates_and_does_not_sleep_after_last_send(self):
        service = object.__new__(DMService)
        service._record = _DMRecord()
        service._send_one = AsyncMock(return_value=True)

        with (
            patch("crawler.services.dm._get_daily_limit", return_value=10),
            patch("crawler.services.dm._get_interval", return_value=(10, 30)),
            patch("crawler.services.dm.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await service.send_batch([_profile("a"), _profile("a"), _profile("b")])

        self.assertEqual(result, {"sent": 2, "skipped_dup": 1, "skipped_limit": 0, "failed": 0})
        self.assertEqual(service._send_one.await_count, 2)
        self.assertEqual(sleep.await_count, 1)

    async def test_comment_applies_remaining_quota_before_navigation(self):
        service = object.__new__(CommentService)
        service._record = _CommentRecord(today=1)
        service._comment_one = AsyncMock(side_effect=lambda profile: (f"aweme-{profile.sec_user_id}", True))

        with (
            patch("crawler.services.comment._get_daily_limit", return_value=2),
            patch("crawler.services.comment._get_interval", return_value=(10, 30)),
            patch("crawler.services.comment.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await service.send_batch([_profile("a"), _profile("b")])

        self.assertEqual(result, {"sent": 1, "skipped_dup": 0, "skipped_limit": 1, "failed": 0})
        service._comment_one.assert_awaited_once()
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
