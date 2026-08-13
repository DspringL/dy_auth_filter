import unittest

from pydantic import ValidationError

from backend.models.config_store import CONFIG_KEYS
from backend.schemas.kol import TaskCreate
from crawler.services.filter import FilterConfig, KOLFilter
from crawler.services.search import KOLProfile


class TaskFilterSchemaTests(unittest.TestCase):
    def test_upper_limits_default_to_unlimited(self):
        task = TaskCreate(keywords=["探店达人"])

        self.assertEqual(task.max_followers, 0)
        self.assertEqual(task.max_total_likes, 0)

    def test_upper_limits_cannot_be_lower_than_lower_limits(self):
        with self.assertRaisesRegex(ValidationError, "粉丝上限不能小于粉丝下限"):
            TaskCreate(
                keywords=["探店达人"],
                min_followers=20_000,
                max_followers=10_000,
            )

        with self.assertRaisesRegex(ValidationError, "总点赞上限不能小于总点赞下限"):
            TaskCreate(
                keywords=["探店达人"],
                min_total_likes=20_000,
                max_total_likes=10_000,
            )


class KOLFilterLimitTests(unittest.TestCase):
    def test_follower_and_like_upper_limits_are_applied(self):
        profiles = [
            KOLProfile(
                sec_user_id="ok",
                follower_count=50_000,
                total_favorited=100_000,
            ),
            KOLProfile(
                sec_user_id="too-many-followers",
                follower_count=100_001,
                total_favorited=100_000,
            ),
            KOLProfile(
                sec_user_id="too-many-likes",
                follower_count=50_000,
                total_favorited=200_001,
            ),
        ]
        config = FilterConfig(
            max_followers=100_000,
            max_total_likes=200_000,
        )

        passed = KOLFilter(config).apply(profiles)

        self.assertEqual([profile.sec_user_id for profile in passed], ["ok"])

    def test_zero_upper_limits_do_not_filter(self):
        profile = KOLProfile(
            sec_user_id="unlimited",
            follower_count=999_999_999,
            total_favorited=999_999_999,
        )

        self.assertEqual(KOLFilter(FilterConfig()).apply([profile]), [profile])


class SystemSettingsTests(unittest.TestCase):
    def test_filter_rules_are_not_exposed_as_system_settings(self):
        self.assertFalse(any(key.startswith("filter.") for key in CONFIG_KEYS))


if __name__ == "__main__":
    unittest.main()
