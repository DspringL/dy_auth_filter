"""
达人筛选服务
根据配置的粉丝数、点赞数等指标对 KOLProfile 列表进行过滤
粉丝数、点赞数均使用实际数量（不再以万为单位）
"""
from dataclasses import dataclass
from loguru import logger
from crawler.services.search import KOLProfile
from crawler.utils.config import get


def _fmt(n: int) -> str:
    """格式化大数字：>=1亿显示亿，>=1万显示万，否则显示原始值"""
    if n >= 100_000_000:
        return f"{n/100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n/10_000:.1f}万"
    return str(n)


@dataclass
class FilterConfig:
    """筛选规则（粉丝数/点赞数均为实际数量）"""
    min_followers: int   = 0       # 粉丝数下限（实际数，0=不限）
    max_followers: int   = 0       # 粉丝数上限（实际数，0=不限）
    min_total_likes: int = 0       # 总点赞数下限（实际数）
    require_shop: bool   = False   # 是否必须开通橱窗
    min_videos: int      = 0       # 最少作品数

    @classmethod
    def from_config(cls) -> "FilterConfig":
        """从 settings.yaml [filter] 段加载（数值为实际数量）"""
        return cls(
            min_followers   = int(get("filter.min_followers",  0)),
            max_followers   = int(get("filter.max_followers",  0)),
            min_total_likes = int(get("filter.min_total_likes", 0)),
            require_shop    = bool(get("filter.require_shop_window", False)),
            min_videos      = int(get("filter.min_recent_videos", 0)),
        )


class KOLFilter:
    """根据筛选规则过滤 KOLProfile 列表"""

    def __init__(self, config: FilterConfig | None = None):
        self.cfg = config or FilterConfig.from_config()
        logger.info(
            f"筛选规则："
            f"粉丝 >={_fmt(self.cfg.min_followers)} "
            f"{'上限 ' + _fmt(self.cfg.max_followers) + ' ' if self.cfg.max_followers else ''}"
            f"总赞 >={_fmt(self.cfg.min_total_likes)} "
            f"橱窗={'必须' if self.cfg.require_shop else '不限'} "
            f"作品 >={self.cfg.min_videos}"
        )

    def apply(self, profiles: list[KOLProfile]) -> list[KOLProfile]:
        """过滤并返回通过筛选的达人列表"""
        passed, rejected = [], []
        for p in profiles:
            reason = self._check(p)
            if reason is None:
                passed.append(p)
            else:
                rejected.append((p.nickname, reason))

        logger.info(
            f"筛选结果：{len(profiles)} 位 → 通过 {len(passed)} 位 "
            f"/ 淘汰 {len(rejected)} 位"
        )
        for name, reason in rejected[:5]:
            logger.debug(f"  淘汰「{name}」：{reason}")
        return passed

    def _check(self, p: KOLProfile) -> str | None:
        """返回 None = 通过，否则返回淘汰原因"""
        # 粉丝数（直接用实际数量比较）
        if self.cfg.min_followers > 0 and p.follower_count < self.cfg.min_followers:
            return f"粉丝数 {_fmt(p.follower_count)} < 下限 {_fmt(self.cfg.min_followers)}"

        if self.cfg.max_followers > 0 and p.follower_count > self.cfg.max_followers:
            return f"粉丝数 {_fmt(p.follower_count)} > 上限 {_fmt(self.cfg.max_followers)}"

        # 总点赞数
        if self.cfg.min_total_likes > 0 and p.total_favorited < self.cfg.min_total_likes:
            return f"总点赞 {_fmt(p.total_favorited)} < 下限 {_fmt(self.cfg.min_total_likes)}"

        # 橱窗
        if self.cfg.require_shop and not p.has_shop:
            return "未开通带货橱窗"

        # 作品数
        if self.cfg.min_videos > 0 and p.aweme_count < self.cfg.min_videos:
            return f"作品数 {p.aweme_count} < 下限 {self.cfg.min_videos}"

        return None

    def sort_by_popularity(self, profiles: list[KOLProfile]) -> list[KOLProfile]:
        """按综合人气降序排列"""
        if not profiles:
            return profiles
        max_f = max(p.follower_count for p in profiles) or 1
        max_l = max(p.total_favorited for p in profiles) or 1

        def score(p: KOLProfile) -> float:
            return (p.follower_count / max_f) * 0.6 + (p.total_favorited / max_l) * 0.4

        return sorted(profiles, key=score, reverse=True)
