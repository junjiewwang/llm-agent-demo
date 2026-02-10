"""Agent 运行时事件，用于向外部传递思考过程。

通过回调函数 (on_event) 将 Agent 内部的推理状态实时传递给调用方（如 WebUI），
实现思考过程的可视化，而不破坏 Agent 的内聚性。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class AgentStoppedError(Exception):
    """用户主动停止 Agent 运行时抛出的异常。

    通过事件回调机制在 _emit() 中检测停止信号并抛出，
    Agent 的 run() 方法会捕获此异常并执行清理。
    """


class EventType(str, Enum):
    """Agent 事件类型。"""

    THINKING = "thinking"  # LLM 开始思考（新一轮迭代）
    TOOL_CALL = "tool_call"  # 即将调用工具
    TOOL_RESULT = "tool_result"  # 工具执行完成
    ANSWERING = "answering"  # LLM 开始生成最终回答
    ERROR = "error"  # 执行出错
    MAX_ITERATIONS = "max_iterations"  # 达到最大迭代次数，强制总结


@dataclass(frozen=True)
class AgentEvent:
    """Agent 运行时事件。

    不可变对象，每个事件描述 Agent 内部的一个状态变化。
    调用方通过 on_event 回调接收并展示。
    """

    type: EventType
    iteration: int = 0
    max_iterations: int = 0
    tool_name: str = ""
    tool_args: Dict = field(default_factory=dict)
    tool_result_preview: str = ""
    duration_ms: int = 0
    success: bool = True
    message: str = ""
