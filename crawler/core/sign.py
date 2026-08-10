"""
抖音 Web 端关键请求参数生成模块（JS 逆向）

涉及参数：
  - X-Bogus (a_bogus)：URL 签名，通过逆向 webmssdk.js 的 _0x5a8f25 函数生成
  - msToken      ：随机 Base64 令牌，由浏览器本地生成后写入 Cookie
  - _signature   ：部分旧接口仍使用，通过 byted_acrawler 生成
  - verifyFp     ：设备指纹（持久化，每次会话固定）

策略：
  1. 优先通过浏览器内页面执行 JS，直接调用抖音网站内置的签名函数（最准确）
  2. 如浏览器不可用，退化为 execjs + 本地 webmssdk.js 文件（需预先提取）
  3. msToken 可直接从登录后的 Cookie 中读取，无需额外生成
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import string
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, unquote

from loguru import logger
from playwright.async_api import Page

from crawler.utils.config import get, ROOT_DIR

# ─── 常量 ─────────────────────────────────────────────────────────────────────

UA = get(
    "douyin.user_agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

# webmssdk.js 本地缓存路径（首次从抖音网页提取后保存）
WEBMSSDK_PATH = ROOT_DIR / "data" / "webmssdk.js"
SIGN_JS_PATH  = ROOT_DIR / "data" / "sign_helper.js"


# ─── msToken 生成 ──────────────────────────────────────────────────────────────

def generate_ms_token(length: int = 107) -> str:
    """
    生成 msToken（随机 Base64 字符串）。
    抖音前端从 Cookie 中读取，Cookie 不存在时由本地算法随机生成写入。
    长度通常为 107 个字符的 URL-safe Base64 字符集。
    """
    chars = string.ascii_letters + string.digits + "=_-"
    token = "".join(random.choices(chars, k=length))
    return token


def generate_verify_fp() -> str:
    """
    生成 verifyFp 设备指纹（格式：verify_<hex>_<timestamp>）。
    正常情况下每台设备固定，这里每次会话生成一次即可复用。
    """
    rand_hex = "".join(random.choices("0123456789abcdef", k=8))
    ts = str(int(time.time() * 1000))
    return f"verify_{rand_hex}_{ts}"


def generate_ttwid() -> str:
    """生成 ttwid（UUID 格式的 Token）"""
    import uuid
    return str(uuid.uuid4()).replace("-", "")


# ─── X-Bogus 生成（通过浏览器内执行 JS） ─────────────────────────────────────

async def get_x_bogus_via_browser(page: Page, url_params: str) -> str:
    """
    在已打开的抖音页面内，通过 window.__SIGN_FUNC 或 webmssdk 函数生成 X-Bogus。
    这是最准确的方式，利用抖音页面已加载的 JS Runtime 直接调用。

    Args:
        page: 已导航到抖音域名的 Playwright Page 对象
        url_params: 请求的 query string（不含 X-Bogus 本身）
    Returns:
        X-Bogus 值字符串，失败时返回空字符串
    """
    js_code = f"""
    (async () => {{
        const params = {json.dumps(url_params)};
        const ua = {json.dumps(UA)};

        // 方式1：尝试调用 window.byted_acrawler（老版本）
        if (window.byted_acrawler && typeof window.byted_acrawler.sign === 'function') {{
            return window.byted_acrawler.sign({{ url: params }});
        }}

        // 方式2：尝试 window._0x... 系列入口（需提前提取函数名）
        // 这里统一尝试已知入口
        const candidates = ['_ms', 'webSign', 'genXB', 'dyjs'];
        for (const fn of candidates) {{
            if (window[fn] && typeof window[fn] === 'function') {{
                try {{
                    const r = window[fn](params, ua);
                    if (r && typeof r === 'string' && r.length > 10) return r;
                }} catch(e) {{}}
            }}
        }}

        return '';
    }})()
    """
    try:
        result = await page.evaluate(js_code)
        if result and len(str(result)) > 10:
            logger.debug(f"浏览器内生成 X-Bogus 成功：{str(result)[:20]}...")
            return str(result)
    except Exception as e:
        logger.debug(f"浏览器内生成 X-Bogus 失败：{e}")
    return ""


async def extract_and_cache_webmssdk(page: Page) -> bool:
    """
    从抖音页面提取 webmssdk.js 并缓存到本地，同时提取签名函数入口名。
    仅需执行一次。
    """
    if WEBMSSDK_PATH.exists():
        return True

    logger.info("正在提取 webmssdk.js...")
    try:
        # 拦截 webmssdk.js 网络请求
        sdk_content = []

        async def on_response(response):
            if "webmssdk" in response.url and response.status == 200:
                try:
                    body = await response.body()
                    sdk_content.append(body.decode("utf-8", errors="ignore"))
                except Exception:
                    pass

        page.on("response", on_response)

        # 访问抖音首页触发 JS 加载
        await page.goto("https://www.douyin.com", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(3)

        if sdk_content:
            content = sdk_content[0]
            WEBMSSDK_PATH.parent.mkdir(parents=True, exist_ok=True)

            # 补充运行时环境（浏览器外执行需要）
            env_patch = """
var window = global;
var navigator = { userAgent: '""" + UA + """' };
var document = { addEventListener: function(){}, createElement: function(){ return {}; } };
var location = { href: 'https://www.douyin.com/' };

"""
            full_js = env_patch + content

            with open(WEBMSSDK_PATH, "w", encoding="utf-8") as f:
                f.write(full_js)

            logger.info(f"webmssdk.js 已保存：{WEBMSSDK_PATH}")
            return True
        else:
            logger.warning("未捕获到 webmssdk.js，将使用降级方案")
    except Exception as e:
        logger.error(f"提取 webmssdk.js 失败：{e}")
    return False


def get_x_bogus_via_execjs(url_params: str) -> str:
    """
    使用本地 webmssdk.js + execjs 生成 X-Bogus（浏览器不可用时的降级方案）。
    需要先调用 extract_and_cache_webmssdk() 提取 JS 文件。
    """
    if not WEBMSSDK_PATH.exists():
        logger.warning("webmssdk.js 不存在，无法通过 execjs 生成 X-Bogus")
        return ""

    try:
        import execjs
        with open(WEBMSSDK_PATH, "r", encoding="utf-8") as f:
            js_code = f.read()
        ctx = execjs.compile(js_code)
        # 调用提取出的入口函数 window.dyjs
        result = ctx.call("window.dyjs", url_params)
        if result:
            logger.debug(f"execjs 生成 X-Bogus：{str(result)[:20]}...")
            return str(result)
    except Exception as e:
        logger.warning(f"execjs 生成 X-Bogus 失败：{e}")
    return ""


# ─── 请求参数构建 ──────────────────────────────────────────────────────────────

class SignatureHelper:
    """
    统一的请求签名辅助类：
    - 构建含 msToken、verifyFp 的基础参数
    - 生成 X-Bogus 并追加到 URL
    - 构建请求头（含 Cookie）
    """

    def __init__(self, page: Page | None = None):
        self._page = page
        self._ms_token = generate_ms_token()
        self._verify_fp = generate_verify_fp()
        self._ttwid = generate_ttwid()

    def set_cookies_from_browser(self, cookie_dict: dict):
        """从浏览器 Cookie 中同步关键 Token"""
        if "msToken" in cookie_dict:
            self._ms_token = cookie_dict["msToken"]
            logger.debug("msToken 已从 Cookie 同步")
        if "ttwid" in cookie_dict:
            self._ttwid = cookie_dict["ttwid"]
        if "verifyFp" in cookie_dict:
            self._verify_fp = cookie_dict["verifyFp"]

    def build_base_params(self, extra: dict | None = None) -> dict:
        """构建基础公共参数"""
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "version_code": "190500",
            "version_name": "19.5.0",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "124.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "124.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": "8",
            "device_memory": "8",
            "platform": "PC",
            "webid": self._generate_webid(),
            "msToken": self._ms_token,
            "verifyFp": self._verify_fp,
        }
        if extra:
            params.update(extra)
        return params

    def _generate_webid(self) -> str:
        """生成 webid（19 位数字字符串）"""
        return str(random.randint(10 ** 18, 10 ** 19 - 1))

    async def sign_url(self, base_url: str, params: dict) -> str:
        """
        将参数签名并生成完整的带 X-Bogus 的 URL。

        Args:
            base_url: 不含查询参数的 URL
            params: 请求参数字典
        Returns:
            完整 URL（含 X-Bogus）
        """
        query_str = urlencode(params)
        x_bogus = ""

        # 优先通过浏览器生成
        if self._page:
            x_bogus = await get_x_bogus_via_browser(self._page, query_str)

        # 降级到 execjs
        if not x_bogus:
            x_bogus = get_x_bogus_via_execjs(query_str)

        # 最终降级：直接拼接（部分接口可接受无签名请求）
        if x_bogus:
            return f"{base_url}?{query_str}&X-Bogus={x_bogus}"
        else:
            logger.warning("无法生成 X-Bogus，使用未签名 URL（可能被拦截）")
            return f"{base_url}?{query_str}"

    def build_headers(self, cookie_str: str = "") -> dict:
        """构建完整请求头"""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://www.douyin.com",
            "Referer": "https://www.douyin.com/",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": UA,
        }
        if cookie_str:
            headers["Cookie"] = cookie_str
        return headers
