"""
应用日志配置。

日志同时输出到：
- 文件：index_tts_studio.log（按大小滚动，保留 3 个备份）
- 控制台：stderr

调用方通过标准 logging 模块获取 logger：
    import logging
    logger = logging.getLogger("index_tts")
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path


LOG_FILE = "index_tts_studio.log"
MAX_BYTES = 2 * 1024 * 1024  # 2MB
BACKUP_COUNT = 3


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    配置根 logger。

    Args:
        level: 日志级别，默认 INFO

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger("index_tts")
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler（滚动）
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "index_tts") -> logging.Logger:
    """获取指定名称的 logger。"""
    return logging.getLogger(name)
