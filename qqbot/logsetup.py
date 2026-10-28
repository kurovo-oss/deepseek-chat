"""日志配置：控制台 + 滚动文件，文件写在项目的 logs/ 目录下。

只配置 "qqbot" 这个 logger（propagate=False），不碰根 logger——
避免和宿主程序（pytest、其他库）抢 handler。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s | %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
LOGGER_NAME = "qqbot"


def setup_logging(
    log_dir: Path,
    level: int = logging.INFO,
    console: bool = True,
    logger_name: str = LOGGER_NAME,
) -> Path:
    """配置日志，返回日志文件路径。重复调用不会叠加 handler。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响后续
            pass

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    return log_file
