"""
用户详情补全服务 — 获取 IP 属地

策略：
  通过浏览器访问用户主页，拦截 /user/profile/other/ XHR 响应。
  该接口直接包含 ip_location 字段（如「广东」「北京」）。
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
            # 抖音用户主页接口返回省份字段为 province，城市为 city
            # ip_location 字段仅在部分接口版本中存在
            ip = (
                user.get("ip_location")           # 新版接口
                or user.get("province")            # 旧版接口省份
                or ""
            )
            # 如有城市则拼接（如「广东 佛山」）
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
        # 先跳空白页，确保下次导航触发全新 XHR
        try:
            await page.goto("about:blank", timeout=5000)
        except Exception:
            pass
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=25_000)
        # 等待 XHR 响应（最多 10 秒）
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
    max_count 控制最多处理条数（避免耗时过长）。
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
