"""
Pydantic 数据校验 Schema（API 请求/响应）
"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict


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
    # 搜索类型：user=搜索用户，video=搜索作品（提取作者）
    search_type: Literal["user", "video"] = "user"
    min_followers: Optional[int] = None
    max_followers: Optional[int] = None
    min_total_likes: Optional[int] = None
    require_shop: Optional[bool] = None
    # 联系方式：dm=私信，comment=评论，both=两种都发
    contact_method: Literal["dm", "comment", "both", "none"] = "none"


class TaskStatus(BaseModel):
    task_id: int
    status: str
    total_found: int = 0
    total_passed: int = 0
    total_sent: int = 0        # 私信发送数
    total_commented: int = 0   # 评论发送数
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_msg: Optional[str] = None
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
