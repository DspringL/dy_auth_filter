"""
全局配置加载器
"""
import os
import sys
import yaml
from pathlib import Path
from loguru import logger

# ── 项目根目录 ──────────────────────────────────────────────────────────────
# PyInstaller 打包后 sys.frozen=True，此时使用 Rust 传入的 DYAUTH_RESOURCE_DIR
# 指向 app bundle 内的资源目录（包含 config/ 和 data/）
# 开发模式下直接用文件系统路径
if getattr(sys, "frozen", False):
    # 打包版：优先读环境变量，fallback 到可执行文件同级目录
    _resource = os.environ.get("DYAUTH_RESOURCE_DIR", "")
    if _resource:
        ROOT_DIR = Path(_resource)
    else:
        ROOT_DIR = Path(sys.executable).parent
else:
    # 开发版
    ROOT_DIR = Path(__file__).parent.parent.parent

CONFIG_FILE = ROOT_DIR / "config" / "settings.yaml"

# ── 内置默认配置（打包版找不到配置文件时自动写入） ─────────────────────────
DEFAULT_CONFIG = """\
cloak_browser:
  api_host: "http://127.0.0.1"
  api_port: 35000
  env_id: ""
  cdp_port: 9222
  launch_timeout: 30
douyin:
  base_url: "https://www.douyin.com"
  cookie_file: "data/cookies.json"
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
search:
  keywords:
    - "达人探店"
    - "探店达人"
  max_users_per_keyword: 50
  search_type: "user"
  anti_crawl:
    page_load_wait: 5
    render_wait: 4
    scroll_interval_min: 3.0
    scroll_interval_max: 6.0
    keyword_interval_min: 8.0
    keyword_interval_max: 15.0
    video_warmup_wait: 8
    video_scroll_interval_min: 4.0
    video_scroll_interval_max: 7.0
    captcha_wait: 90
    captcha_max_retry: 3
filter:
  min_followers: 1000
  max_followers: 0
  min_total_likes: 1000
  require_shop_window: false
  min_recent_videos: 3
dm:
  template: "你好 {nickname}，我是品牌商务负责人，看了您的内容非常专业，想和您洽谈品牌合作，有意向请回复我！"
  daily_limit: 20
  interval_min: 10
  interval_max: 30
  sent_record_file: "data/dm_sent.json"
comment:
  template: "你好 {nickname}，我是品牌商务负责人，看了您的内容非常专业，想和您洽谈品牌合作，有意向请私信我！"
  daily_limit: 100
  interval_min: 10
  interval_max: 30
  sent_record_file: "data/comment_sent.json"
  latest_video_count: 1
database:
  path: "data/douyin.db"
api:
  host: "127.0.0.1"
  port: 8000
  reload: true
logging:
  level: "INFO"
  file: "logs/app.log"
  max_bytes: 10485760
  backup_count: 5
"""


def load_config() -> dict:
    """加载 YAML 配置文件，找不到时自动初始化"""
    if not CONFIG_FILE.exists():
        # ── 打包版：尝试从 PyInstaller 解压的资源目录复制 ──────────────────
        copied = False
        if getattr(sys, "frozen", False):
            mei_dir = Path(getattr(sys, "_MEIPASS", ""))
            src = mei_dir / "config" / "settings.yaml"
            if src.exists():
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(str(src), str(CONFIG_FILE))
                logger.info(f"配置文件已从资源目录复制：{CONFIG_FILE}")
                copied = True

        if not copied:
            # 写内置默认配置
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
            logger.info(f"已创建默认配置文件：{CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


# 全局配置单例
_config: dict | None = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get(key_path: str, default=None):
    """
    通过点分隔路径获取配置项
    例如：get("cloak_browser.api_port") -> 35000
    """
    cfg = get_config()
    keys = key_path.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val
