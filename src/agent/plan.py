"""Plan-and-Execute 数据模型。

定义任务计划的数据结构，用于 PlanExecuteAgent 的计划生成与执行跟踪。

Plan 是一次性生成的任务分解结果，PlanStep 是计划中的每一步。
Planner 负责调用 LLM 生成 Plan，PlanExecuteAgent 逐步执行并跟踪状态。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from src.llm.base_client import BaseLLMClient, Message, Role
from src.utils.logger import logger


class StepStatus(str, Enum):
    """计划步骤的执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """计划中的单个步骤。

    Attributes:
        id: 步骤唯一标识（如 "step-1"）。
        description: 步骤描述（自然语言）。
        status: 当前执行状态。
        result_summary: 执行完成后的结果摘要（由 ReAct 子循环的回答填充）。
        tool_hint: 可选的工具提示（Planner 建议使用的工具名称）。
    """

    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    result_summary: str = ""
    tool_hint: Optional[str] = None

    def to_dict(self) -> dict:
        """序列化为字典（用于事件传输）。"""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "result_summary": self.result_summary,
            "tool_hint": self.tool_hint,
        }


@dataclass
class Plan:
    """任务执行计划。

    由 Planner 一次性生成，包含目标和步骤列表。
    PlanExecuteAgent 按 current_step_index 逐步执行。

    Attributes:
        goal: 用户原始目标。
        steps: 有序的步骤列表。
        current_step_index: 当前执行到的步骤索引。
        replan_count: 重新规划次数（用于限制 replan 频率）。
    """

    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    current_step_index: int = 0
    replan_count: int = 0

    MAX_REPLAN = 2  # 最大重新规划次数

    @property
    def current_step(self) -> Optional[PlanStep]:
        """获取当前待执行的步骤。"""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        """计划是否已全部执行完毕。"""
        return self.current_step_index >= len(self.steps)

    @property
    def completed_steps(self) -> List[PlanStep]:
        """已完成的步骤列表。"""
        return [s for s in self.steps if s.status == StepStatus.COMPLETED]

    @property
    def progress_summary(self) -> str:
        """进度摘要文本。"""
        completed = len(self.completed_steps)
        total = len(self.steps)
        return f"{completed}/{total} 步已完成"

    def advance(self) -> None:
        """推进到下一步。"""
        self.current_step_index += 1

    def to_dict(self) -> dict:
        """序列化为字典（用于事件传输）。"""
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "replan_count": self.replan_count,
        }


# ── Planner: 调用 LLM 生成计划 ──

# 简单任务判断：若 LLM 认为不需要计划，返回 None
_SIMPLE_TASK_THRESHOLD = 2  # 步骤数 <= 此值视为简单任务，退化为直接 ReAct

_PLANNER_SYSTEM_PROMPT = """你是一个任务规划专家。根据用户的目标，将其分解为可按顺序执行的步骤。

输出规则：
1. 以纯 JSON 格式输出，不要包含任何 Markdown 标记（如 ```json）
2. 每个步骤应该是一个独立的、可验证的子任务
3. 步骤数控制在 3-7 步之间
4. 如果任务很简单（如单轮问答、简单计算），步骤数可以是 1-2 步
5. 每步的 description 应清晰具体，让执行者（另一个 AI）能理解该做什么
6. tool_hint 是可选的，如果你知道该步骤适合用某个工具，可以提示

输出格式：
{
  "steps": [
    {"description": "步骤描述", "tool_hint": "可选工具名称或null"}
  ]
}"""

_REPLAN_SYSTEM_PROMPT = """你是一个任务规划专家。根据已完成的步骤结果和剩余目标，重新规划后续步骤。

已完成的步骤和结果会在用户消息中提供。你需要：
1. 分析已完成步骤的结果，判断是否需要调整后续计划
2. 只输出**剩余**需要执行的步骤（不包含已完成的）
3. 以纯 JSON 格式输出，不要包含任何 Markdown 标记

输出格式：
{
  "steps": [
    {"description": "步骤描述", "tool_hint": "可选工具名称或null"}
  ]
}"""


def create_plan(llm: BaseLLMClient, user_input: str,
                temperature: float = 0.3, max_tokens: int = 1024) -> Optional[Plan]:
    """调用 LLM 生成任务执行计划。

    Args:
        llm: LLM 客户端。
        user_input: 用户原始输入。
        temperature: 规划用的温度（较低以提高确定性）。
        max_tokens: 规划输出的最大 token 数。

    Returns:
        Plan 实例，如果解析失败返回 None。
    """
    import json

    messages = [
        Message(role=Role.SYSTEM, content=_PLANNER_SYSTEM_PROMPT),
        Message(role=Role.USER, content=f"用户目标：{user_input}"),
    ]

    try:
        response = llm.chat(messages=messages, temperature=temperature, max_tokens=max_tokens)
        content = (response.content or "").strip()

        # 容错：去除可能的 Markdown 代码块标记
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        data = json.loads(content)
        steps_data = data.get("steps", [])

        if not steps_data:
            logger.warning("Planner 返回空步骤列表")
            return None

        steps = []
        for i, s in enumerate(steps_data):
            steps.append(PlanStep(
                id=f"step-{i + 1}",
                description=s.get("description", ""),
                tool_hint=s.get("tool_hint"),
            ))

        # 简单任务判断：步骤数过少说明任务不需要 Plan 编排，退化为直接 ReAct
        if len(steps) <= _SIMPLE_TASK_THRESHOLD:
            logger.info("任务较简单（{} 步 <= 阈值 {}），跳过 Plan 模式",
                        len(steps), _SIMPLE_TASK_THRESHOLD)
            return None

        plan = Plan(goal=user_input, steps=steps)
        logger.info("计划生成成功 | 目标: {} | 步骤数: {}", user_input[:50], len(steps))
        return plan

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Planner 输出解析失败: {} | 原始内容: {}", e, content[:200] if 'content' in dir() else "N/A")
        return None
    except Exception as e:
        logger.error("计划生成异常: {}", e)
        return None


def replan(llm: BaseLLMClient, plan: Plan,
           temperature: float = 0.3, max_tokens: int = 1024) -> Optional[List[PlanStep]]:
    """根据已完成步骤的结果重新规划剩余步骤。

    Args:
        llm: LLM 客户端。
        plan: 当前计划（包含已完成步骤的结果）。
        temperature: 规划温度。
        max_tokens: 最大输出 token。

    Returns:
        新的步骤列表（仅剩余部分），解析失败返回 None。
    """
    import json

    # 构建已完成步骤的上下文
    completed_ctx = "\n".join(
        f"- 步骤 {s.id}: {s.description} → 结果: {s.result_summary}"
        for s in plan.completed_steps
    )
    remaining_ctx = "\n".join(
        f"- 步骤 {s.id}: {s.description}"
        for s in plan.steps[plan.current_step_index:]
    )

    user_msg = (
        f"原始目标：{plan.goal}\n\n"
        f"已完成的步骤：\n{completed_ctx}\n\n"
        f"原计划剩余步骤：\n{remaining_ctx}\n\n"
        f"请根据已完成步骤的结果，重新规划剩余步骤。"
    )

    messages = [
        Message(role=Role.SYSTEM, content=_REPLAN_SYSTEM_PROMPT),
        Message(role=Role.USER, content=user_msg),
    ]

    try:
        response = llm.chat(messages=messages, temperature=temperature, max_tokens=max_tokens)
        content = (response.content or "").strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        data = json.loads(content)
        steps_data = data.get("steps", [])

        # 从当前索引继续编号
        base_index = plan.current_step_index
        new_steps = []
        for i, s in enumerate(steps_data):
            new_steps.append(PlanStep(
                id=f"step-{base_index + i + 1}",
                description=s.get("description", ""),
                tool_hint=s.get("tool_hint"),
            ))

        logger.info("重新规划成功 | 新步骤数: {}", len(new_steps))
        return new_steps

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Replan 输出解析失败: {}", e)
        return None
    except Exception as e:
        logger.error("重新规划异常: {}", e)
        return None
