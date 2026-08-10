"""
全局配置加载器
"""
import os
import yaml
from pathlib import Path
from loguru import logger

# 项目根目录
ROOT_DIR = Path(__file__).parent.parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "settings.yaml"


def load_config() -> dict:
    """加载 YAML 配置文件"""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_FILE}")
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
