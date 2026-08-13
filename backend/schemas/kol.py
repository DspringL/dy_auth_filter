"""
Pydantic 数据校验 Schema（API 请求/响应）
"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── 达人相关 ──────────────────────────────────────────────────────────────────

class KOLBase(BaseModel):
    sec_user_id: str
    uid: Optional[str] = None
    short_id: Optional[str] = None
    nickname: Optional[str] = None
    signature: Optional[str] = None
    avatar_url: Optional[str] = None
    follower_count: int = 0
    following_count: int = 0
    total_favorited: int = 0
    aweme_count: int = 0
    is_star: bool = False
    commerce_user_level: int = 0
    has_shop: bool = False
    profile_url: Optional[str] = None
    ip_location: Optional[str] = None
    search_keyword: Optional[str] = None
    # 私信状态
    dm_status: str = "pending"
    dm_sent_at: Optional[datetime] = None
    # 评论状态
    comment_status: str = "pending"
    comment_sent_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None


class KOLOut(KOLBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class KOLListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[KOLOut]


# ── 任务相关 ──────────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    keywords: list[str]
    search_type: Literal["user", "video"] = "user"
    min_followers: int = Field(0, ge=0)
    max_followers: int = Field(0, ge=0)
    min_total_likes: int = Field(0, ge=0)
    max_total_likes: int = Field(0, ge=0)
    # 搜索作品专用条件。排序/发布时间的取值与抖音搜索接口保持一致。
    video_sort_type: Literal[0, 1, 2] = 0
    video_publish_time: Literal[0, 1, 7, 180] = 0
    min_video_likes: int = Field(0, ge=0)
    min_video_collects: int = Field(0, ge=0)
    min_video_comments: int = Field(0, ge=0)
    min_video_shares: int = Field(0, ge=0)
    require_shop: bool = False
    max_per_keyword: Optional[int] = None
    contact_method: Literal["dm", "comment", "both", "none"] = "none"

    @model_validator(mode="after")
    def validate_filter_ranges(self):
        if self.max_followers and self.max_followers < self.min_followers:
            raise ValueError("粉丝上限不能小于粉丝下限")
        if self.max_total_likes and self.max_total_likes < self.min_total_likes:
            raise ValueError("总点赞上限不能小于总点赞下限")
        return self


class TaskStatus(BaseModel):
    task_id: int
    status: str
    total_found: int = 0
    total_passed: int = 0
    total_sent: int = 0
    total_commented: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_msg: Optional[str] = None
    logs: list[str] = []
    captcha_required: bool = False
    captcha_message: Optional[str] = None
    captcha_screenshot: Optional[str] = None
    # 任务参数（用于前端展示筛选条件）
    keywords: list[str] = []
    search_type: str = "user"
    contact_method: str = "none"
    max_per_keyword: Optional[int] = None
    min_followers: int = 0
    max_followers: int = 0
    min_total_likes: int = 0
    max_total_likes: int = 0
    video_sort_type: int = 0
    video_publish_time: int = 0
    min_video_likes: int = 0
    min_video_collects: int = 0
    min_video_comments: int = 0
    min_video_shares: int = 0
    require_shop: bool = False
    model_config = ConfigDict(from_attributes=True)


# ── 联系方式请求 ──────────────────────────────────────────────────────────────

class ContactRequest(BaseModel):
    sec_user_ids: list[str]
    # dm=私信，comment=评论，both=两种都发
    contact_method: Literal["dm", "comment", "both"] = "dm"


class ContactResult(BaseModel):
    dm_sent: int = 0
    dm_skipped_dup: int = 0
    dm_skipped_limit: int = 0
    dm_failed: int = 0
    comment_sent: int = 0
    comment_skipped_dup: int = 0
    comment_skipped_limit: int = 0
    comment_failed: int = 0


# 兼容旧接口
class DMRequest(BaseModel):
    sec_user_ids: list[str]

class DMResult(BaseModel):
    sent: int
    skipped_dup: int
    skipped_limit: int
    failed: int


# ── 登录状态 ──────────────────────────────────────────────────────────────────

class LoginStatus(BaseModel):
    logged_in: bool
    message: str
