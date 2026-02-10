"""重试机制模块。

为 LLM API 调用提供统一的重试策略，处理常见的瞬态错误：
- 网络超时
- API 限流 (429)
- 服务端错误 (500/502/503)
"""

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from src.utils.logger import logger

# tenacity 需要标准 logging logger
_std_logger = logging.getLogger("retry")


def llm_retry(max_attempts: int = 3):
    """LLM API 调用重试装饰器。

    指数退避策略：1s → 2s → 4s，最多重试 max_attempts 次。
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            ConnectionError,
            TimeoutError,
            OSError,
        )),
        before_sleep=_log_retry,
        reraise=True,
    )


def _log_retry(retry_state):
    """重试前记录日志。"""
    logger.warning(
        "LLM 调用失败，{:.1f}s 后第 {} 次重试 | 错误: {}",
        retry_state.next_action.sleep,
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )
