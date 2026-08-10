"""
全局日志配置
"""
import sys
from pathlib import Path
from loguru import logger
from crawler.utils.config import get, ROOT_DIR


def setup_logger():
    """初始化 loguru 日志"""
    log_file = ROOT_DIR / get("logging.file", "logs/app.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()  # 移除默认 handler

    # 控制台输出
    logger.add(
        sys.stdout,
        level=get("logging.level", "INFO"),
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 文件输出（按大小滚动）
    logger.add(
        str(log_file),
        level=get("logging.level", "INFO"),
        rotation=get("logging.max_bytes", 10485760),
        retention=get("logging.backup_count", 5),
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
    )

    return logger


# 初始化日志
setup_logger()
