"""
抖音带货达人搜索服务

策略：浏览器页面自动化 + XHR 响应拦截
  - 让 CloakBrowser 直接访问抖音搜索页面
  - 浏览器自动携带 Cookie 和签名，无需手动逆向
  - 通过 Playwright response 事件拦截 XHR 响应

搜索用户：拦截 /aweme/v1/web/discover/search/?search_channel=user_search_channel
搜索作品：拦截 /aweme/v1/web/search/item/（aweme_list 字段）
"""
import asyncio
import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import AsyncGenerator
from urllib.parse import quote

from loguru import logger
from playwright.async_api import Page, Response

from crawler.utils.config import get

# ── 接口路径 ───────────────────────────────────────────────────────────────────
USER_SEARCH_API  = "/aweme/v1/web/discover/search/"
VIDEO_SEARCH_API = "/aweme/v1/web/search/item/"

# ── 反爬节奏配置（从 settings.yaml 读取，避免触发验证码） ──────────────────────
def _acfg(key: str, default: float) -> float:
    """读取 search.anti_crawl.* 配置"""
    return float(get(f"search.anti_crawl.{key}", default))


# ── 数据模型 ──────────────────────────────────────────────────────────────────

@dataclass
class KOLProfile:
    """带货达人数据结构"""
    sec_user_id: str = ""
    uid: str = ""
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    following_count: int = 0
    total_favorited: int = 0
    aweme_count: int = 0
    is_star: bool = False
    commerce_user_level: int = 0
    has_shop: bool = False
    short_id: str = ""
    profile_url: str = ""
    # 当前任务内缓存的最新作品 ID。私信访问主页时顺带补齐；不入库，仅用于
    # “私信 + 评论”流程避免评论阶段再次打开达人主页。
    latest_aweme_id: str = ""
    ip_location: str = ""
    search_keyword: str = ""
    collected_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── 搜索服务 ──────────────────────────────────────────────────────────────────

class DouyinSearchService:
    """通过浏览器自动化搜索抖音用户或视频作者"""

    def __init__(self, page: Page):
        self._page = page

    async def close(self):
        pass

    # ── 搜索用户（单关键词）────────────────────────────────────────────────────

    async def search_users(
        self,
        keyword: str,
        max_count: int = 50,
    ) -> AsyncGenerator[KOLProfile, None]:
        """访问用户搜索页，拦截 /discover/search/ XHR，提取 user_list"""
        logger.info(f"[用户搜索] 开始：「{keyword}」，目标 {max_count} 条")
        page = self._page
        total_fetched = 0
        page_num = 0
        queue: asyncio.Queue = asyncio.Queue()

        async def on_response(response: Response):
            if USER_SEARCH_API not in response.url:
                return
            if response.status != 200:
                await queue.put(None)
                return
            try:
                body = await response.body()
                data = json.loads(body)
                if data.get("status_code", -1) != 0:
                    logger.warning(f"[用户搜索] 业务码异常 {data.get('status_code')}：{data.get('status_msg', '')}")
                    await queue.put(None)
                    return
                user_list = data.get("user_list") or []
                has_more  = bool(data.get("has_more", False))
                logger.info(f"[用户搜索] 拦截 {len(user_list)} 条用户，has_more={has_more}")
                await queue.put({"users": user_list, "has_more": has_more})
            except Exception as e:
                logger.warning(f"[用户搜索] 解析失败：{e}")
                await queue.put(None)

        page.on("response", on_response)
        try:
            encoded_kw = quote(keyword)
            url = f"https://www.douyin.com/search/{encoded_kw}?type=user"
            try:
                await page.goto("about:blank", timeout=5_000)
            except Exception:
                pass

            from crawler.utils.captcha import navigate_with_captcha_guard
            ok = await navigate_with_captcha_guard(page, url)
            if not ok:
                logger.error(f"[用户搜索]「{keyword}」导航失败（验证码）")
                return

            result = await asyncio.wait_for(queue.get(), timeout=15.0)
            if not result:
                logger.error(f"[用户搜索]「{keyword}」首页失败")
                return

            for item in result["users"]:
                if total_fetched >= max_count:
                    return
                p = self._parse_item(item, keyword)
                if p.sec_user_id:
                    yield p
                    total_fetched += 1

            if not result["has_more"] or total_fetched >= max_count:
                return

            while total_fetched < max_count:
                page_num += 1
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(_acfg('scroll_interval_min',3.0), _acfg('scroll_interval_max',6.0))
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    nr = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    break
                if not nr or not nr.get("users"):
                    break
                for item in nr["users"]:
                    if total_fetched >= max_count:
                        return
                    p = self._parse_item(item, keyword)
                    if p.sec_user_id:
                        yield p
                        total_fetched += 1
                if not nr["has_more"]:
                    break
                await asyncio.sleep(_acfg('scroll_interval_min',3.0), _acfg('scroll_interval_max',6.0))

        except asyncio.TimeoutError:
            logger.error(f"[用户搜索]「{keyword}」XHR 超时")
        except Exception as e:
            logger.error(f"[用户搜索]「{keyword}」异常：{e}")
        finally:
            page.remove_listener("response", on_response)
            logger.info(f"[用户搜索]「{keyword}」完成，共 {total_fetched} 条")

    async def batch_search(self, keywords: list[str], max_per_keyword: int = 50) -> list[KOLProfile]:
        """多关键词用户搜索，去重"""
        all_profiles: dict[str, KOLProfile] = {}
        for idx, kw in enumerate(keywords):
            logger.info(f"[批量搜索] 关键词 {idx+1}/{len(keywords)}：「{kw}」")
            async for p in self.search_users(kw, max_per_keyword):
                if p.sec_user_id not in all_profiles:
                    all_profiles[p.sec_user_id] = p
            if idx < len(keywords) - 1:
                await asyncio.sleep(_acfg('keyword_interval_min',8.0), _acfg('keyword_interval_max',15.0))
        logger.info(f"批量搜索完成，去重后共 {len(all_profiles)} 位用户")
        return list(all_profiles.values())

    # ── 搜索作品，提取作者（单关键词）────────────────────────────────────────

    async def search_videos(
        self,
        keyword: str,
        max_count: int = 50,
    ) -> AsyncGenerator[KOLProfile, None]:
        """
        搜索视频作品，提取作者信息转为 KOLProfile。

        策略（根据实际抓包确认的接口）：
          1. 先导航到用户搜索页（?type=user）确保登录 Cookie 生效
          2. 切换到综合搜索（?type=general），同时拦截视频接口
             - 视频接口：/aweme/v1/web/search/item/（含 aweme_list）
             - 备用接口：/aweme/v1/web/discover/search/ 中含 aweme_list 的响应
          3. 翻页同样走滚动触发
        按 sec_user_id 去重，同一作者多视频只保留一次。
        """
        logger.info(f"[视频搜索] 开始：「{keyword}」，目标作者 {max_count} 条")
        page = self._page
        seen_authors: set[str] = set()
        total_fetched = 0
        page_num = 0
        queue: asyncio.Queue = asyncio.Queue()

        async def on_response(response: Response):
            url = response.url
            if VIDEO_SEARCH_API not in url and USER_SEARCH_API not in url:
                return
            if response.status != 200:
                return
            try:
                body = await response.body()
                data = json.loads(body)
                if data.get("status_code", -1) != 0:
                    return
                # 调试：首次打印完整字段名，找视频数据所在字段
                if not getattr(on_response, '_printed', False):
                    on_response._printed = True
                    logger.info(f"[视频搜索] 接口字段列表: {list(data.keys())}")
                    # 打印非空字段的类型和长度
                    for k, v in data.items():
                        if v and k not in ('extra', 'log_pb', 'guide_search_words'):
                            vinfo = f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__
                            logger.info(f"[视频搜索]   data[{k!r}] = {vinfo}")
                if "aweme_list" not in data:
                    return
                aweme_list = data.get("aweme_list") or []
                has_more = bool(data.get("has_more", False))
                if aweme_list:
                    logger.info(f"[视频搜索] 拦截 {len(aweme_list)} 条视频，has_more={has_more}")
                    await queue.put({"videos": aweme_list, "has_more": has_more})
                else:
                    logger.debug(f"[视频搜索] 预热帧 aweme_list=[]，has_more={has_more}，放入队列")
                    await queue.put({"videos": [], "has_more": has_more})
            except Exception as e:
                logger.warning(f"[视频搜索] 响应解析失败：{e}")

        page.on("response", on_response)
        try:
            encoded_kw = quote(keyword)
            video_url = f"https://www.douyin.com/search/{encoded_kw}?type=video"

            # Step 1：先打开抖音首页建立 Session，然后用户搜索页热身
            from crawler.utils.captcha import navigate_with_captcha_guard
            try:
                await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                pass
            await asyncio.sleep(_acfg("video_warmup_wait", 8))

            # Step 2：用户搜索页热身（带验证码保护）
            user_url = f"https://www.douyin.com/search/{encoded_kw}?type=user"
            ok = await navigate_with_captcha_guard(page, user_url)
            if not ok:
                logger.warning(f"[视频搜索] 热身页导航失败，继续尝试")
            # 热身页充分等待
            await asyncio.sleep(_acfg("video_warmup_wait", 8))
            # 清空用户搜索的 XHR
            while not queue.empty():
                try:
                    queue.get_nowait()
                except Exception:
                    break

            # Step 2：导航到视频搜索页（带验证码检测保护）
            from crawler.utils.captcha import navigate_with_captcha_guard, is_captcha_present
            video_url = f"https://www.douyin.com/search/{encoded_kw}?type=video"
            logger.info(f"[视频搜索] 导航：{video_url}")
            ok = await navigate_with_captcha_guard(page, video_url)
            if not ok:
                logger.error(f"[视频搜索]「{keyword}」导航失败（可能被验证码阻止）")
                return

            # 等待页面视频卡片完全渲染（骨架屏消失）
            await asyncio.sleep(_acfg('page_load_wait',5))

            # 等待视频 XHR
            try:
                result = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                result = None

            if not result:
                logger.error(f"[视频搜索]「{keyword}」无法获取视频数据")
                return

            # 首帧 aweme_list 为空（预热帧）时，触发页面滚动拿真实数据
            retry = 0
            while result is not None and not result["videos"] and result.get("has_more") and retry < 5:
                retry += 1
                logger.info(f"[视频搜索] 首帧为空（预热），触发滚动等待真实数据（第 {retry} 次）")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(_acfg('scroll_interval_min',3.0), _acfg('scroll_interval_max',6.0))
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    result = await asyncio.wait_for(queue.get(), timeout=12.0)
                except asyncio.TimeoutError:
                    result = None
                    break

            if not result or not result["videos"]:
                logger.error(f"[视频搜索]「{keyword}」视频数据始终为空（预热 {retry} 次后仍无数据）")
                return

            for video in result["videos"]:
                if total_fetched >= max_count:
                    return
                p = self._parse_video_author(video, keyword)
                if p.sec_user_id and p.sec_user_id not in seen_authors:
                    seen_authors.add(p.sec_user_id)
                    yield p
                    total_fetched += 1

            if not result["has_more"] or total_fetched >= max_count:
                return

            while total_fetched < max_count:
                page_num += 1
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(_acfg('scroll_interval_min',3.0), _acfg('scroll_interval_max',6.0))
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    nr = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    break
                if not nr or not nr.get("videos"):
                    break
                for video in nr["videos"]:
                    if total_fetched >= max_count:
                        return
                    p = self._parse_video_author(video, keyword)
                    if p.sec_user_id and p.sec_user_id not in seen_authors:
                        seen_authors.add(p.sec_user_id)
                        yield p
                        total_fetched += 1
                if not nr["has_more"]:
                    break
                await asyncio.sleep(_acfg('scroll_interval_min',3.0), _acfg('scroll_interval_max',6.0))

        except asyncio.TimeoutError:
            logger.error(f"[视频搜索]「{keyword}」XHR 超时")
        except Exception as e:
            logger.error(f"[视频搜索]「{keyword}」异常：{e}")
        finally:
            page.remove_listener("response", on_response)
            logger.info(f"[视频搜索]「{keyword}」完成，共获取 {total_fetched} 位作者")

    async def batch_search_videos(self, keywords: list[str], max_per_keyword: int = 50) -> list[KOLProfile]:
        """多关键词视频搜索，提取不重复作者"""
        all_profiles: dict[str, KOLProfile] = {}
        for idx, kw in enumerate(keywords):
            logger.info(f"[视频批量搜索] 关键词 {idx+1}/{len(keywords)}：「{kw}」")
            async for p in self.search_videos(kw, max_per_keyword):
                if p.sec_user_id not in all_profiles:
                    all_profiles[p.sec_user_id] = p
            if idx < len(keywords) - 1:
                await asyncio.sleep(_acfg('keyword_interval_min',8.0), _acfg('keyword_interval_max',15.0))
        logger.info(f"[视频批量搜索] 完成，去重后共 {len(all_profiles)} 位作者")
        return list(all_profiles.values())

    # ── 数据解析 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_item(item: dict, keyword: str = "") -> KOLProfile:
        """解析用户搜索结果单条（user_info 子字典）"""
        user_info = item.get("user_info") or item.get("user") or item
        sec_uid = user_info.get("sec_uid") or user_info.get("sec_user_id") or ""
        uid     = str(user_info.get("uid") or user_info.get("user_id") or "")
        follower  = int(user_info.get("follower_count") or user_info.get("fans_count") or 0)
        total_fav = int(user_info.get("total_favorited") or user_info.get("total_favorited_count") or 0)
        commerce_info  = user_info.get("commerce_user_info") or {}
        commerce_level = int(commerce_info.get("commerce_user_level") or 0)
        has_shop = bool(
            user_info.get("with_shop_entry")
            or user_info.get("with_commerce_entry")
            or user_info.get("has_shop_entry")
            or commerce_level > 0
            or bool(commerce_info)
        )
        avatar_url = ""
        for av_key in ("avatar_thumb", "avatar_medium", "avatar_larger"):
            av = user_info.get(av_key)
            if isinstance(av, dict):
                urls = av.get("url_list") or []
                if urls:
                    avatar_url = urls[0]
                    break
        return KOLProfile(
            sec_user_id         = sec_uid,
            uid                 = uid,
            nickname            = user_info.get("nickname") or "",
            signature           = user_info.get("signature") or "",
            avatar_url          = avatar_url,
            follower_count      = follower,
            following_count     = int(user_info.get("following_count") or 0),
            total_favorited     = total_fav,
            aweme_count         = int(user_info.get("aweme_count") or 0),
            is_star             = bool(user_info.get("is_star") or user_info.get("custom_verify")),
            commerce_user_level = commerce_level,
            has_shop            = has_shop,
            short_id            = user_info.get("short_id") or uid,
            profile_url         = f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
            ip_location         = user_info.get("ip_location") or "",
            search_keyword      = keyword,
            collected_at        = datetime.now().isoformat(timespec="seconds"),
        )

    @staticmethod
    def _parse_video_author(video: dict, keyword: str = "") -> KOLProfile:
        """从视频数据中提取作者信息（author 字段）"""
        author = video.get("author") or {}
        if not author:
            return KOLProfile()
        sec_uid = author.get("sec_uid") or author.get("sec_user_id") or ""
        uid     = str(author.get("uid") or author.get("user_id") or "")
        follower  = int(author.get("follower_count") or author.get("fans_count") or 0)
        total_fav = int(author.get("total_favorited") or 0)
        commerce_info  = author.get("commerce_user_info") or {}
        commerce_level = int(commerce_info.get("commerce_user_level") or 0)
        has_shop = bool(
            author.get("with_shop_entry")
            or author.get("with_commerce_entry")
            or commerce_level > 0
        )
        avatar_url = ""
        for av_key in ("avatar_thumb", "avatar_medium", "avatar_larger"):
            av = author.get(av_key)
            if isinstance(av, dict):
                urls = av.get("url_list") or []
                if urls:
                    avatar_url = urls[0]
                    break
        return KOLProfile(
            sec_user_id         = sec_uid,
            uid                 = uid,
            nickname            = author.get("nickname") or "",
            signature           = author.get("signature") or "",
            avatar_url          = avatar_url,
            follower_count      = follower,
            following_count     = int(author.get("following_count") or 0),
            total_favorited     = total_fav,
            aweme_count         = int(author.get("aweme_count") or 0),
            is_star             = bool(author.get("is_star") or author.get("custom_verify")),
            commerce_user_level = commerce_level,
            has_shop            = has_shop,
            short_id            = author.get("short_id") or uid,
            profile_url         = f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
            ip_location         = author.get("ip_location") or "",
            search_keyword      = keyword,
            collected_at        = datetime.now().isoformat(timespec="seconds"),
        )
