import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from backend.schemas.kol import TaskCreate
from crawler.services.filter import VideoFilter, VideoFilterConfig
from crawler.services.search import DouyinSearchService, KOLProfile


class _Candidate:
    def __init__(self):
        self.hovered = False
        self.clicked = False

    async def is_visible(self):
        return True

    async def hover(self):
        self.hovered = True

    async def click(self):
        self.clicked = True


class _TextLocator:
    def __init__(self, candidates):
        self.candidates = candidates

    async def count(self):
        return len(self.candidates)

    def nth(self, index):
        return self.candidates[index]


class _FilterPage:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_by_text(self, text, exact=True):
        return _TextLocator(self.mapping.get(text, []))


class VideoTaskSchemaTests(unittest.TestCase):
    def test_video_filters_default_to_unlimited(self):
        task = TaskCreate(keywords=["美食"], search_type="video")

        self.assertEqual(task.video_sort_type, 0)
        self.assertEqual(task.video_publish_time, 0)
        self.assertEqual(task.min_video_likes, 0)
        self.assertEqual(task.min_video_collects, 0)
        self.assertEqual(task.min_video_comments, 0)
        self.assertEqual(task.min_video_shares, 0)

    def test_only_native_sort_and_publish_values_are_accepted(self):
        with self.assertRaises(ValidationError):
            TaskCreate(keywords=["美食"], video_sort_type=3)
        with self.assertRaises(ValidationError):
            TaskCreate(keywords=["美食"], video_publish_time=30)


class VideoResponseParsingTests(unittest.TestCase):
    def test_current_nested_response_shape_is_extracted(self):
        current = {"data": [{"type": 1, "aweme_info": {"aweme_id": "123"}}]}
        legacy = {"aweme_list": [{"aweme_id": "456"}]}

        self.assertEqual(
            DouyinSearchService._extract_video_items(current),
            [{"aweme_id": "123"}],
        )
        self.assertEqual(
            DouyinSearchService._extract_video_items(legacy),
            [{"aweme_id": "456"}],
        )

    def test_filter_query_is_matched_exactly(self):
        url = "https://www.douyin.com/aweme/v1/web/search/item/?sort_type=1&publish_time=7"
        self.assertTrue(DouyinSearchService._video_response_matches_filters(url, 1, 7))
        self.assertFalse(DouyinSearchService._video_response_matches_filters(url, 2, 7))
        self.assertTrue(
            DouyinSearchService._video_response_matches_filters(
                "https://www.douyin.com/aweme/v1/web/search/item/?count=16",
                0,
                0,
            )
        )

    def test_work_metrics_and_matched_work_id_are_parsed(self):
        profile = DouyinSearchService._parse_video_author(
            {
                "aweme_id": "7673102163879239818",
                "create_time": 1786533317,
                "author": {
                    "sec_uid": "author-1",
                    "nickname": "作者",
                    "follower_count": 20_000,
                },
                "statistics": {
                    "digg_count": 1500,
                    "collect_count": 300,
                    "comment_count": 80,
                    "share_count": 40,
                },
            },
            "美食",
        )

        self.assertEqual(profile.latest_aweme_id, "7673102163879239818")
        self.assertEqual(profile.video_create_time, 1786533317)
        self.assertEqual(profile.video_like_count, 1500)
        self.assertEqual(profile.video_collect_count, 300)
        self.assertEqual(profile.video_comment_count, 80)
        self.assertEqual(profile.video_share_count, 40)


class VideoNativeFilterInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_panel_is_hovered_and_native_options_are_clicked(self):
        trigger = _Candidate()
        latest = _Candidate()
        within_week = _Candidate()
        service = DouyinSearchService(
            _FilterPage({
                "筛选": [trigger],
                "最新发布": [latest],
                "一周内": [within_week],
            })
        )

        with patch(
            "crawler.services.search.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await service._apply_video_filters(sort_type=2, publish_time=7)

        self.assertTrue(trigger.hovered)
        self.assertTrue(latest.clicked)
        self.assertTrue(within_week.clicked)


class VideoFilterTests(unittest.TestCase):
    def test_work_performance_and_author_follower_filters_are_applied(self):
        config = VideoFilterConfig(
            min_followers=10_000,
            max_followers=100_000,
            min_likes=1_000,
            min_collects=200,
            min_comments=50,
            min_shares=20,
        )
        passed = KOLProfile(
            sec_user_id="passed",
            follower_count=50_000,
            video_like_count=1_000,
            video_collect_count=200,
            video_comment_count=50,
            video_share_count=20,
        )
        low_share = KOLProfile(
            sec_user_id="low-share",
            follower_count=50_000,
            video_like_count=10_000,
            video_collect_count=2_000,
            video_comment_count=500,
            video_share_count=19,
        )
        too_many_followers = KOLProfile(
            sec_user_id="too-many-followers",
            follower_count=100_001,
            video_like_count=10_000,
            video_collect_count=2_000,
            video_comment_count=500,
            video_share_count=200,
        )

        self.assertEqual(VideoFilter(config).apply([passed, low_share, too_many_followers]), [passed])

    def test_filter_before_dedupe_keeps_later_qualifying_work(self):
        low_work = KOLProfile(sec_user_id="same-author", video_like_count=10)
        qualifying_work = KOLProfile(sec_user_id="same-author", video_like_count=1_000)
        video_filter = VideoFilter(VideoFilterConfig(min_likes=500))

        filtered = video_filter.apply([low_work, qualifying_work])
        deduped = video_filter.dedupe_authors(filtered)

        self.assertEqual(deduped, [qualifying_work])


if __name__ == "__main__":
    unittest.main()
