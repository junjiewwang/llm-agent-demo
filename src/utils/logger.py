"""日志配置模块。"""

import sys

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

__all__ = ["logger"]
