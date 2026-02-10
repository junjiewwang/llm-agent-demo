"""循环检测器 - 识别 Agent 无限重试的工具调用模式。

解决的问题：
当 LLM 陷入"调用同一工具 → 相同参数 → 相同结果 → 再次调用"的死循环时，
靠 max_iterations 硬上限终止太晚（可能已经浪费了大量 token 和时间）。

循环检测器通过记录最近的工具调用模式，在连续重复出现时提前发出预警，
让 Agent 主动插入引导 prompt 告知 LLM 停止重试并换种方式回答。

检测策略：
- 将每次工具调用转为 fingerprint（tool_name + 参数摘要）
- 如果最近 N 次调用中出现连续相同的 fingerprint，判定为循环
"""

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

from src.utils.logger import logger


# 连续相同调用达到此次数即判定为循环
DEFAULT_REPEAT_THRESHOLD = 3
# 保留最近多少条记录用于模式匹配
DEFAULT_WINDOW_SIZE = 10


@dataclass
class LoopDetector:
    """工具调用循环检测器。

    用法：
        detector = LoopDetector()
        # 每次工具调用后记录
        detector.record(tool_name, arguments_str)
        # 检查是否进入循环
        if detector.is_looping():
            # 插入引导 prompt 让 LLM 换种方式回答
    """

    repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD
    window_size: int = DEFAULT_WINDOW_SIZE
    _fingerprints: List[str] = field(default_factory=list)

    def record(self, tool_name: str, arguments: str) -> None:
        """记录一次工具调用。"""
        fp = self._make_fingerprint(tool_name, arguments)
        self._fingerprints.append(fp)
        # 只保留最近的 window_size 条
        if len(self._fingerprints) > self.window_size:
            self._fingerprints = self._fingerprints[-self.window_size:]

    def is_looping(self) -> bool:
        """检测是否进入循环模式。

        判定规则：最近连续 repeat_threshold 次调用的 fingerprint 相同。
        """
        if len(self._fingerprints) < self.repeat_threshold:
            return False

        recent = self._fingerprints[-self.repeat_threshold:]
        is_loop = len(set(recent)) == 1
        if is_loop:
            logger.warning(
                "检测到工具调用循环 | 最近 {} 次调用相同: {}",
                self.repeat_threshold,
                recent[0][:40],
            )
        return is_loop

    def get_loop_summary(self) -> Optional[str]:
        """如果处于循环中，返回循环摘要信息供 Agent 使用。"""
        if not self.is_looping():
            return None

        # 从 fingerprint 中提取工具名
        last_fp = self._fingerprints[-1]
        # fingerprint 格式：tool_name:hash
        tool_name = last_fp.split(":")[0] if ":" in last_fp else "unknown"
        return (
            f"系统检测到你已经连续 {self.repeat_threshold} 次调用工具 '{tool_name}' "
            f"并使用了相同或非常相似的参数，但问题仍未解决。"
            f"请不要再重复调用此工具，改为：\n"
            f"1. 根据已有的工具返回结果直接给出回答\n"
            f"2. 或者尝试换一种方式（不同参数、不同工具）来解决问题\n"
            f"3. 如果确实无法解决，请如实告诉用户"
        )

    def reset(self) -> None:
        """重置检测器（新一轮对话开始时调用）。"""
        self._fingerprints.clear()

    @staticmethod
    def _make_fingerprint(tool_name: str, arguments: str) -> str:
        """生成工具调用的指纹。

        使用 tool_name + 参数内容的 hash，避免存储完整参数。
        """
        args_hash = hashlib.md5(arguments.encode()).hexdigest()[:12]
        return f"{tool_name}:{args_hash}"
