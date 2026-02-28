"""日志配置模块。

输出：
- 控制台（stderr）：INFO 级别，带颜色，用于开发调试
- 文件（logs/app_{date}.log）：DEBUG 级别，按日轮转保留 7 天，用于问题回溯
"""

import sys
from pathlib import Path

from loguru import logger

# 移除默认 handler
logger.remove()

# 控制台输出：INFO 级别，带颜色
logger.add(
    sys.stderr,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    ),
    colorize=True,
)

# 文件输出：DEBUG 级别，按日轮转，保留 7 天
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logger.add(
    str(_LOG_DIR / "app_{time:YYYY-MM-DD}.log"),
    level="DEBUG",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{name}:{function}:{line} - "
        "{message}"
    ),
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
    enqueue=True,  # 线程安全：异步写入，不阻塞主线程
)

__all__ = ["logger"]
