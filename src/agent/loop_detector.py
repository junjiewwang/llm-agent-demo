"""循环检测器 - 识别 Agent 无限重试和任务偏离的工具调用模式。

解决的问题：
当 LLM 陷入"调用同一工具 → 相同参数 → 相同结果 → 再次调用"的死循环时，
靠 max_iterations 硬上限终止太晚（可能已经浪费了大量 token 和时间）。

循环检测器通过记录最近的工具调用模式，在连续重复出现时提前发出预警，
让 Agent 主动插入引导 prompt 告知 LLM 停止重试并换种方式回答。

检测策略（三层）：
- L1 精确匹配：将每次工具调用转为 fingerprint（tool_name + 参数摘要），
  如果最近 N 次调用中出现连续相同的 fingerprint，判定为循环。
- L2 语义匹配：同一工具连续返回空/无效结果达到阈值时，
  即使参数不同也判定为语义级循环（解决"换参数重试同一工具"的盲区）。
- L3 任务偏离检测：当连续 N 次工具调用与步骤目标所需的工具不匹配时，
  判定为注意力漂移（解决"LLM 被上下文污染导致调用无关工具"的问题）。
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.utils.logger import logger


# 连续相同调用达到此次数即判定为循环
DEFAULT_REPEAT_THRESHOLD = 3
# 保留最近多少条记录用于模式匹配
DEFAULT_WINDOW_SIZE = 10
# 同一工具连续空结果达到此次数即判定为语义级循环
DEFAULT_EMPTY_RESULT_THRESHOLD = 3
# 连续调用无关工具达到此次数即判定为任务偏离
DEFAULT_DRIFT_THRESHOLD = 2

# 空结果的判定模式：去除空白后为空、或仅包含"无输出"等提示
_EMPTY_PATTERNS = frozenset({
    "", "(无输出)", "(no output)", "none", "null",
})


def _is_empty_result(result: str) -> bool:
    """判断工具返回是否为空/无效结果。"""
    stripped = result.strip()
    if not stripped:
        return True
    return stripped.lower() in _EMPTY_PATTERNS


@dataclass
class LoopDetector:
    """工具调用循环检测器（三层检测）。

    用法：
        detector = LoopDetector()
        # 设置当前步骤的预期工具（可选，用于 L3 偏离检测）
        detector.set_expected_tools(["kubectl", "docker"])
        # 每次工具调用后记录
        detector.record(tool_name, arguments_str)
        # 工具结果返回后记录（用于语义级检测）
        detector.record_result(tool_name, result_str)
        # 检查是否进入循环
        if detector.is_looping():
            # 插入引导 prompt 让 LLM 换种方式回答

    三层检测策略：
    - L1 精确匹配：连续 N 次完全相同的 fingerprint（tool_name + 参数 MD5）
    - L2 语义匹配：同一工具连续 N 次返回空/无效结果（参数可不同）
    - L3 任务偏离：连续 N 次调用的工具不在预期工具列表中
    """

    repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD
    window_size: int = DEFAULT_WINDOW_SIZE
    empty_result_threshold: int = DEFAULT_EMPTY_RESULT_THRESHOLD
    drift_threshold: int = DEFAULT_DRIFT_THRESHOLD
    _fingerprints: List[str] = field(default_factory=list)
    # L2 语义检测：tool_name → 连续空结果计数
    _empty_result_streaks: Dict[str, int] = field(default_factory=dict)
    # 最后一次触发语义循环的工具名
    _semantic_loop_tool: Optional[str] = field(default=None)
    # L3 任务偏离检测
    _expected_tools: Optional[List[str]] = field(default=None)
    _consecutive_drift_count: int = field(default=0)
    _drift_detected: bool = field(default=False)
    _drift_tools: List[str] = field(default_factory=list)

    def set_expected_tools(self, tool_names: Optional[List[str]]) -> None:
        """设置当前步骤的预期工具列表（用于 L3 任务偏离检测）。

        从 step_prompt 中的关键词推断应该使用哪些工具。
        如果为 None，则禁用 L3 检测。

        Args:
            tool_names: 预期工具名称列表，None 表示不限制。
        """
        self._expected_tools = tool_names
        self._consecutive_drift_count = 0
        self._drift_detected = False
        self._drift_tools = []

    def record(self, tool_name: str, arguments: str) -> None:
        """记录一次工具调用（L1 精确匹配 + L3 偏离检测）。"""
        fp = self._make_fingerprint(tool_name, arguments)
        self._fingerprints.append(fp)
        # 只保留最近的 window_size 条
        if len(self._fingerprints) > self.window_size:
            self._fingerprints = self._fingerprints[-self.window_size:]

        # L3 任务偏离检测
        if self._expected_tools is not None:
            if tool_name not in self._expected_tools:
                self._consecutive_drift_count += 1
                self._drift_tools.append(tool_name)
                if self._consecutive_drift_count >= self.drift_threshold:
                    self._drift_detected = True
                    logger.warning(
                        "检测到任务偏离 | 连续 {} 次调用非预期工具: {} | 预期: {}",
                        self._consecutive_drift_count,
                        self._drift_tools[-self.drift_threshold:],
                        self._expected_tools,
                    )
            else:
                # 调用了预期工具，重置偏离计数
                self._consecutive_drift_count = 0
                self._drift_tools = []
                self._drift_detected = False

    def record_result(self, tool_name: str, result: str) -> None:
        """记录工具返回结果（L2 语义匹配）。

        当同一工具连续返回空/无效结果时，累加计数。
        任何非空结果会重置该工具的计数。
        """
        if _is_empty_result(result):
            streak = self._empty_result_streaks.get(tool_name, 0) + 1
            self._empty_result_streaks[tool_name] = streak
            if streak >= self.empty_result_threshold:
                self._semantic_loop_tool = tool_name
                logger.warning(
                    "检测到语义级循环 | 工具 '{}' 连续 {} 次返回空结果",
                    tool_name, streak,
                )
        else:
            # 非空结果，重置该工具的连续空结果计数
            self._empty_result_streaks[tool_name] = 0
            if self._semantic_loop_tool == tool_name:
                self._semantic_loop_tool = None

    def is_looping(self) -> bool:
        """检测是否进入循环模式（L1/L2/L3 任一触发即判定）。"""
        return self._is_exact_looping() or self._is_semantic_looping() or self._is_drifting()

    def _is_exact_looping(self) -> bool:
        """L1 精确匹配：最近连续 repeat_threshold 次调用的 fingerprint 相同。"""
        if len(self._fingerprints) < self.repeat_threshold:
            return False

        recent = self._fingerprints[-self.repeat_threshold:]
        is_loop = len(set(recent)) == 1
        if is_loop:
            logger.warning(
                "检测到精确循环 | 最近 {} 次调用相同: {}",
                self.repeat_threshold,
                recent[0][:40],
            )
        return is_loop

    def _is_semantic_looping(self) -> bool:
        """L2 语义匹配：同一工具连续空结果达到阈值。"""
        return self._semantic_loop_tool is not None

    def _is_drifting(self) -> bool:
        """L3 任务偏离检测：连续调用非预期工具达到阈值。"""
        return self._drift_detected

    def get_loop_summary(self) -> Optional[str]:
        """如果处于循环中，返回循环摘要信息供 Agent 使用。"""
        # 优先返回任务偏离提示（最高优先级，因为偏离会导致后续全部无效）
        if self._is_drifting():
            drift_tools = list(set(self._drift_tools[-self.drift_threshold:]))
            expected = self._expected_tools or []
            # 消费一次偏离信号
            self._drift_detected = False
            self._consecutive_drift_count = 0
            self._drift_tools = []
            return (
                f"⚠️ 系统检测到你连续调用了与当前步骤目标无关的工具（{', '.join(drift_tools)}）。\n"
                f"当前步骤应该使用的工具是: {', '.join(expected)}。\n"
                f"请立即回到当前步骤的目标上来：\n"
                f"1. 使用正确的工具（{', '.join(expected)}）来完成当前步骤\n"
                f"2. 不要调用与当前步骤无关的工具\n"
                f"3. 如果当前步骤的任务已经完成，请直接给出结果"
            )

        # 其次返回语义级循环提示
        if self._is_semantic_looping():
            tool_name = self._semantic_loop_tool
            streak = self._empty_result_streaks.get(tool_name, 0)
            # 消费一次语义循环信号，避免后续每次迭代都重复提示
            self._semantic_loop_tool = None
            self._empty_result_streaks[tool_name] = 0
            return (
                f"系统检测到你已经连续 {streak} 次调用工具 '{tool_name}'，"
                f"每次使用不同的参数但都返回了空结果。"
                f"这说明确实没有相关数据，空结果本身就是有效信息。\n"
                f"请不要再尝试用不同参数调用此工具，改为：\n"
                f"1. 将「没有查询到相关数据」作为本步骤的结论\n"
                f"2. 基于已有的工具返回结果直接给出回答\n"
                f"3. 如果确实无数据，如实告诉用户即可"
            )

        if self._is_exact_looping():
            last_fp = self._fingerprints[-1]
            tool_name = last_fp.split(":")[0] if ":" in last_fp else "unknown"
            return (
                f"系统检测到你已经连续 {self.repeat_threshold} 次调用工具 '{tool_name}' "
                f"并使用了相同的参数，但问题仍未解决。"
                f"请不要再重复调用此工具，改为：\n"
                f"1. 根据已有的工具返回结果直接给出回答\n"
                f"2. 或者尝试换一种方式（不同参数、不同工具）来解决问题\n"
                f"3. 如果确实无法解决，请如实告诉用户"
            )

        return None

    def reset(self) -> None:
        """重置检测器（新一轮对话开始时调用）。"""
        self._fingerprints.clear()
        self._empty_result_streaks.clear()
        self._semantic_loop_tool = None
        self._expected_tools = None
        self._consecutive_drift_count = 0
        self._drift_detected = False
        self._drift_tools = []

    @staticmethod
    def _make_fingerprint(tool_name: str, arguments: str) -> str:
        """生成工具调用的指纹。

        使用 tool_name + 参数内容的 hash，避免存储完整参数。
        """
        args_hash = hashlib.md5(arguments.encode()).hexdigest()[:12]
        return f"{tool_name}:{args_hash}"
