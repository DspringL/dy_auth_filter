"""
用户详情补全服务 — 获取 IP 属地，可选同步联系

策略：
  通过浏览器访问用户主页，拦截 /user/profile/other/ XHR 响应获取 ip_location。
  如传入 contact_method，在同一次主页访问中顺带执行私信或评论，减少重复打开主页。
"""
import asyncio
import json
import random
from loguru import logger
from playwright.async_api import Page

PROFILE_API = "/aweme/v1/web/user/profile/other/"


async def fetch_ip_location(page: Page, sec_user_id: str) -> str:
    """
    访问单个用户主页，拦截 XHR 响应提取 ip_location。
    返回 IP 属地字符串（如「广东」），失败返回空字符串。
    """
    result: list[str] = []
    done = asyncio.Event()

    async def on_response(response):
        if PROFILE_API not in response.url:
            return
        if response.status != 200:
            done.set()
            return
        try:
            body = await response.body()
            data = json.loads(body)
            user = data.get("user") or {}
            ip = (
                user.get("ip_location")
                or user.get("province")
                or ""
            )
            city = user.get("city") or ""
            if ip and city and city not in ip:
                ip = f"{ip} {city}"
            result.append(ip.strip())
        except Exception as e:
            logger.debug(f"解析用户主页响应失败: {e}")
        finally:
            done.set()

    page.on("response", on_response)
    try:
        profile_url = f"https://www.douyin.com/user/{sec_user_id}"
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.wait_for(done.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.debug(f"获取 ip_location 超时：{sec_user_id[:16]}...")
    except Exception as e:
        logger.debug(f"访问用户主页失败：{e}")
    finally:
        page.remove_listener("response", on_response)

    return result[0] if result else ""


async def batch_fetch_ip_locations(
    page: Page,
    sec_user_ids: list[str],
    max_count: int = 30,
) -> dict[str, str]:
    """
    批量获取 IP 属地，返回 {sec_user_id: ip_location} 映射。
    """
    targets = sec_user_ids[:max_count]
    results: dict[str, str] = {}

    for i, sec_uid in enumerate(targets):
        ip = await fetch_ip_location(page, sec_uid)
        results[sec_uid] = ip
        if ip:
            logger.info(f"[IP属地 {i+1}/{len(targets)}] {sec_uid[:20]}... → {ip}")
        else:
            logger.debug(f"[IP属地 {i+1}/{len(targets)}] {sec_uid[:20]}... → 未获取")
        if i < len(targets) - 1:
            await asyncio.sleep(random.uniform(1.2, 2.5))

    got = sum(1 for v in results.values() if v)
    logger.info(f"IP 属地批量获取完成：共 {len(results)} 条，有值 {got} 条")
    return results


async def batch_fetch_ip_and_contact(
    page: Page,
    profiles: list,          # list[KOLProfile]
    contact_method: str,     # "dm" | "comment" | "both" | "none"
    dm_svc=None,
    cmt_svc=None,
    max_count: int = 30,
    task_log=None,
) -> tuple[dict[str, str], int, int]:
    """
    访问用户主页，同步获取 IP 属地并执行联系动作（私信/评论/两种）。

    优势：相比「先批量抓IP，再单独联系」减少了重复访问主页的次数。
    在 fetch_ip_location 完成后（主页已打开），直接执行私信或评论。

    返回：(ip_map, dm_sent_count, comment_sent_count)
    """
    from crawler.services.search import KOLProfile

    def log(msg):
        if task_log:
            task_log(msg)
        else:
            logger.info(msg)

    targets = profiles[:max_count]
    ip_map: dict[str, str] = {}
    dm_sent = 0
    comment_sent = 0

    for i, profile in enumerate(targets):
        sec_uid = profile.sec_user_id
        nickname = profile.nickname or sec_uid[:12]

        # ── 1. 访问主页并拦截 XHR 获取 IP ──────────────────────────────────
        ip = await fetch_ip_location(page, sec_uid)
        ip_map[sec_uid] = ip
        log(f"[{i+1}/{len(targets)}] {nickname} IP={ip or '未获取'}")

        # ── 2. 主页已打开，顺带执行私信 ──────────────────────────────────────
        if contact_method in ("dm", "both") and dm_svc:
            from crawler.services.dm import _get_daily_limit as _dm_limit
            if dm_svc._record.today_count() >= _dm_limit():
                log(f"私信今日已达上限，跳过 {nickname}")
            elif dm_svc._record.already_sent(sec_uid):
                log(f"已发过私信，跳过 {nickname}")
            else:
                # page 已在用户主页，直接点「发私信」
                ok = await dm_svc._send_one(profile)
                if ok:
                    dm_svc._record.record(sec_uid, nickname)
                    dm_sent += 1
                    log(f"✅ 私信已发 → {nickname}")
                else:
                    log(f"❌ 私信失败 → {nickname}")

        # ── 3. 顺带执行评论（需要访问最新作品页） ────────────────────────────
        if contact_method in ("comment", "both") and cmt_svc:
            from crawler.services.comment import _get_daily_limit as _cmt_limit
            if cmt_svc._record.today_count() >= _cmt_limit():
                log(f"评论今日已达上限，跳过 {nickname}")
            elif cmt_svc._record.already_commented(sec_uid):
                log(f"已评论过，跳过 {nickname}")
            else:
                aweme_id, ok = await cmt_svc._comment_one(profile)
                if ok:
                    cmt_svc._record.record(sec_uid, nickname, aweme_id)
                    comment_sent += 1
                    log(f"✅ 评论已发 → {nickname}（作品 {aweme_id}）")
                else:
                    log(f"❌ 评论失败 → {nickname}")

        if i < len(targets) - 1:
            await asyncio.sleep(random.uniform(1.5, 3.0))

    return ip_map, dm_sent, comment_sent
