"""
应用日志配置。

日志同时输出到：
- 文件：index_tts_studio.log（按大小滚动，保留 3 个备份）
- 控制台：stderr

handler 挂在 ROOT logger 上而不是 "index_tts" 上：包内模块有的用
logging.getLogger("index_tts*")，有的用 logging.getLogger(__name__)
（名字是 "index_tts_gui.core.xxx"，在 logging 层级里并不是 "index_tts"
的子节点）。只配 "index_tts" 会让后者写出的告警全部丢失。
"""
import logging
import logging.handlers
import os
import sys

from index_tts_gui.core.paths import app_root


LOG_FILE = "index_tts_studio.log"
MAX_BYTES = 2 * 1024 * 1024  # 2MB
BACKUP_COUNT = 3

#: 第三方库日志过于啰嗦，统一压到 WARNING
NOISY_LOGGERS = ("openai", "httpx", "httpcore", "urllib3")


def log_file_path() -> str:
    """日志文件绝对路径（跟随 app_root，不受启动工作目录影响）。"""
    return os.path.join(app_root(), LOG_FILE)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    配置根 logger。

    Args:
        level: 日志级别，默认 INFO

    Returns:
        "index_tts" logger 实例（handler 实际挂在 root 上）
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 避免重复添加 handler
    if not root.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
            datefmt="%m-%d %H:%M:%S",
        )

        # 文件 handler（滚动）
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path(),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        # 控制台 handler
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # 包内主 logger 单独设级别（root 已是该级别，这里保持显式）
    app_logger = logging.getLogger("index_tts")
    app_logger.setLevel(level)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return app_logger


def get_logger(name: str = "index_tts") -> logging.Logger:
    """获取指定名称的 logger。"""
    return logging.getLogger(name)
