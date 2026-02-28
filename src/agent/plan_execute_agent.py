"""Plan-and-Execute Agent 实现。

架构：Planner（LLM 生成计划）→ Executor（每步复用 ReAct 子循环）→ Monitor（Replan 判断）。

核心流程：
1. 用户输入 → Planner 生成 Plan（步骤列表）
2. 逐步执行：每步构造子目标 → 复用 ReAct 子循环完成 → 记录结果
3. 每步执行后判断是否需要 Replan（步骤失败 / 结果偏离预期）
4. 所有步骤完成 → 综合各步结果生成最终回答

与 ReActAgent 的关系：
- PlanExecuteAgent 是 ReAct 的上层编排，不是替代
- 每个步骤的执行仍然是一个完整的 ReAct 循环（Think → Act → Observe）
- PlanExecuteAgent 额外提供：任务分解、步骤跟踪、Replan 能力
"""

import time
from typing import Optional, TYPE_CHECKING

from src.agent.base_agent import BaseAgent, OnEventCallback, WaitForConfirmation
from src.agent.events import AgentEvent, AgentStoppedError, EventType
from src.agent.loop_detector import LoopDetector
from src.agent.metrics import RunMetrics
from src.agent.plan import Plan, PlanStep, StepStatus, create_plan, replan
from src.agent.tool_executor import ToolExecutorMixin
from src.config import settings
from src.context.builder import ContextBuilder
from src.environment.adapter_base import EnvironmentAdapter
from src.llm.base_client import BaseLLMClient, Message, Role
from src.memory.conversation import ConversationMemory
from src.memory.vector_store import VectorStore
from src.observability import get_tracer
from src.observability.instruments import trace_span, set_span_content
from src.tools.base_tool import ToolRegistry
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.rag.knowledge_base import KnowledgeBase
    from src.skills.router import SkillRouter

_tracer = get_tracer(__name__)

# 步骤执行的子目标 prompt 模板
_STEP_PROMPT_TEMPLATE = """你正在执行一个多步骤计划的第 {step_index}/{total_steps} 步。

总目标：{goal}

当前步骤：{step_description}
{tool_hint_line}
{context_line}

请专注完成当前步骤，给出这一步的执行结果。
提示：如果当前步骤需要获取多项独立信息，请在一次回复中同时调用多个工具（并行），而不是逐个调用，以提高执行效率。

重要约束：
- 当工具已返回结果时，你必须基于工具返回的实际数据来总结和回答当前步骤的问题
- 不要忽略工具结果，不要回答与当前步骤无关的内容
- 你的回答必须围绕「当前步骤」的目标，不要描述你的能力或局限性"""

# 最终综合回答 prompt
_FINAL_SYNTHESIS_PROMPT = """你刚刚完成了一个多步骤计划。请根据各步骤的执行结果，给用户一个完整、连贯的最终回答。

用户原始问题：{goal}

各步骤执行结果：
{step_results}

请综合以上结果，直接回答用户的问题。不要逐步列举执行过程，直接给出最终答案。"""


class PlanExecuteAgent(BaseAgent, ToolExecutorMixin):
    """Plan-and-Execute Agent。

    先通过 LLM 将用户任务分解为步骤列表（Plan），
    然后逐步执行每个步骤（每步复用 ReAct 子循环），
    最终综合所有步骤结果给出最终回答。

    支持：
    - 任务分解：LLM 自动将复杂任务拆分为 3-7 步
    - 步骤跟踪：每步有独立的状态（PENDING → RUNNING → COMPLETED/FAILED）
    - Replan：步骤失败时可重新规划剩余步骤（最多 MAX_REPLAN 次）
    - 事件通知：PLAN_CREATED / STEP_START / STEP_DONE / REPLAN 全链路事件
    - 可观测性：共享 RunMetrics，记录所有子循环的 LLM/工具调用
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        tool_registry: ToolRegistry,
        memory: ConversationMemory,
        context_builder: ContextBuilder,
        vector_store: Optional[VectorStore] = None,
        knowledge_base: Optional["KnowledgeBase"] = None,
        skill_router: Optional["SkillRouter"] = None,
        env_adapter: Optional[EnvironmentAdapter] = None,
        max_iterations: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(llm_client, tool_registry, memory)
        self._context_builder = context_builder
        self._vector_store = vector_store
        self._knowledge_base = knowledge_base
        self._skill_router = skill_router
        self._env_adapter = env_adapter
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._temperature = temperature or settings.agent.temperature
        self._step_temperature = settings.agent.step_temperature  # 步骤执行独立低温
        self._max_tokens = max_tokens or settings.agent.max_tokens
        self._last_metrics: Optional[RunMetrics] = None

        # 每步子循环的最大迭代数（比主 Agent 少，避免单步耗时过长）
        self._step_max_iterations = min(self._max_iterations, 10)
        self._loop_detector = LoopDetector()
        self._current_snapshot_pos: Optional[int] = None  # 当前步骤的 Scratchpad 快照位置

    @property
    def context_builder(self) -> ContextBuilder:
        return self._context_builder

    @property
    def last_metrics(self) -> Optional[RunMetrics]:
        return self._last_metrics

    def run(
        self,
        user_input: str,
        on_event: OnEventCallback = None,
        wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """Plan-and-Execute 主流程。"""
        metrics = RunMetrics(max_iterations=self._max_iterations)

        def _emit(event: AgentEvent) -> None:
            if on_event:
                try:
                    on_event(event)
                except AgentStoppedError:
                    raise
                except Exception as e:
                    logger.warning("事件回调异常: {}", e)

        with trace_span(_tracer, "plan_execute_agent.run",
                        {"agent.type": "plan_execute"}) as span:
            set_span_content(span, "agent.input", user_input)
            try:
                result = self._run_plan_execute(
                    user_input, metrics, _emit, wait_for_confirmation,
                )
                set_span_content(span, "agent.output", result)
                return result
            except AgentStoppedError:
                logger.info("用户停止了 Plan-Execute Agent | 迭代: {}", metrics.iterations)
                self._context_builder.clear_injections()
                metrics.finish()
                self._last_metrics = metrics
                raise

    def _run_plan_execute(
        self, user_input: str, metrics: RunMetrics,
        _emit, wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """Plan-Execute 核心流程。"""

        # ── Phase 1: 生成计划（静默调用，简单任务不打扰用户） ──
        plan = create_plan(self._llm, user_input, temperature=0.3)

        if not plan or len(plan.steps) == 0:
            logger.warning("计划生成失败或任务较简单，回退到直接回答")
            return self._fallback_direct_answer(user_input, metrics, _emit, wait_for_confirmation)

        # 发送 PLAN_CREATED 事件
        _emit(AgentEvent(
            type=EventType.PLAN_CREATED,
            plan=plan.to_dict(),
            total_steps=len(plan.steps),
            message=f"已生成 {len(plan.steps)} 步执行计划",
        ))

        logger.info("Plan-Execute 开始 | 目标: {} | 步骤数: {}", user_input[:50], len(plan.steps))

        # 将用户原始消息写入对话历史（持久化，不在 Scratchpad 范围内）
        self._memory.add_user_message(user_input)

        # ── Phase 2: 逐步执行 ──
        while not plan.is_complete:
            step = plan.current_step
            if step is None:
                break

            step.status = StepStatus.RUNNING

            _emit(AgentEvent(
                type=EventType.STEP_START,
                step_id=step.id,
                step_index=plan.current_step_index,
                total_steps=len(plan.steps),
                message=step.description,
            ))

            logger.info("执行步骤 {}/{}: {}", plan.current_step_index + 1,
                        len(plan.steps), step.description[:80])

            # 执行子循环
            step_result = self._execute_step(
                plan, step, metrics, _emit, wait_for_confirmation,
            )

            if step_result is not None:
                step.status = StepStatus.COMPLETED
                step.result_summary = step_result[:500]
            else:
                step.status = StepStatus.FAILED
                step.result_summary = "执行失败"

            _emit(AgentEvent(
                type=EventType.STEP_DONE,
                step_id=step.id,
                step_index=plan.current_step_index,
                total_steps=len(plan.steps),
                step_status=step.status.value,
                message=step.result_summary[:200],
            ))

            logger.info("步骤 {} 完成 | 状态: {} | 累计指标: {} | 结果: {}",
                        step.id, step.status.value, metrics.summary(),
                        step.result_summary[:100])

            # ── Phase 2.5: Replan 判断 ──
            if step.status == StepStatus.FAILED and plan.replan_count < Plan.MAX_REPLAN:
                new_steps = self._try_replan(plan, _emit)
                if new_steps is not None:
                    # 替换剩余步骤
                    plan.steps = plan.completed_steps + [step] + new_steps
                    plan.replan_count += 1
                    # current_step_index 指向失败步骤的下一个
                    plan.current_step_index = len(plan.completed_steps) + 1
                    continue

            plan.advance()

        # ── Phase 3: 综合回答 ──
        final_answer = self._synthesize_answer(plan, metrics)
        self._context_builder.clear_injections()
        metrics.finish()
        self._last_metrics = metrics
        logger.info("Plan-Execute 完成 | {} | {}", plan.progress_summary, metrics.summary())
        return final_answer

    def _execute_step(
        self, plan: Plan, step: PlanStep, metrics: RunMetrics,
        _emit, wait_for_confirmation: WaitForConfirmation = None,
    ) -> Optional[str]:
        """执行计划中的单个步骤，复用 ReAct 子循环。

        采用 Scratchpad 架构实现步骤级上下文隔离：
        1. snapshot() — 记录步骤开始前的消息位置
        2. 在 Scratchpad 中执行 ReAct 子循环（Think → Act → Observe）
        3. rollback_to_snapshot() — 销毁步骤的中间过程消息
        4. settle_step_result() — 仅沉淀一条精简的结果摘要

        这将 Token 消耗从 O(k²) 降低到 O(k)：后续步骤只看到前面步骤的
        结果摘要，而不是全部的中间推理和工具输出。

        Returns:
            步骤执行结果文本，失败返回 None。
        """
        # 构造步骤子目标 prompt（包含已完成步骤的结果摘要）
        context_parts = []
        for s in plan.completed_steps:
            context_parts.append(f"- {s.description}: {s.result_summary}")
        context_line = ""
        if context_parts:
            context_line = "已完成的步骤结果：\n" + "\n".join(context_parts)

        tool_hint_line = ""
        if step.tool_hint:
            tool_hint_line = f"建议使用工具：{step.tool_hint}"

        step_prompt = _STEP_PROMPT_TEMPLATE.format(
            step_index=plan.current_step_index + 1,
            total_steps=len(plan.steps),
            goal=plan.goal,
            step_description=step.description,
            tool_hint_line=tool_hint_line,
            context_line=context_line,
        )

        tools_schema = None

        # ── Scratchpad: 快照当前消息位置 ──
        snapshot_pos = self._memory.snapshot()
        self._current_snapshot_pos = snapshot_pos
        # 每步开始时重置循环检测器，避免跨步骤的误判
        self._loop_detector.reset()
        # L3 任务偏离检测：从 step 描述和 tool_hint 推断预期工具
        expected_tools = self._infer_expected_tools(step)
        if expected_tools:
            self._loop_detector.set_expected_tools(expected_tools)

        try:
            # 注入知识、记忆、技能（首步注入，后续复用）
            if plan.current_step_index == 0:
                self._inject_context(plan.goal, metrics)
                # 首步时设置 tools schema 预留（tools 在运行期间不变）
                tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None
                self._context_builder.set_tools_reserve(tools_schema)
            else:
                tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None

            # 执行器上下文隔离：清除 Skill 和 Memory 注入
            # 原因：Scratchpad 局部视图中消息极少（System + step_prompt + 工具交互），
            # 任何 SYSTEM 级注入信息的相对权重都会被极度放大，有"劫持"执行器的风险：
            # - Skill prompt → LLM 按 Skill 策略行事而忽略 step_prompt
            # - 长期记忆 → LLM 把旧记忆当作"先例"照搬（如"无法查询token用量"）
            # step_prompt 已包含总目标和步骤指令，执行器只需工具即可完成任务。
            # KB 保留，因为它提供的是与当前查询直接相关的事实性文档片段。
            self._context_builder.set_skills([])
            self._context_builder.set_memory([])

            # 将子目标作为用户消息加入对话（在 Scratchpad 中）
            self._memory.add_user_message(step_prompt)

            # 检查并压缩
            self._check_and_compress(_emit)

            # ReAct 子循环
            step_result = None
            for iteration in range(1, self._step_max_iterations + 1):
                metrics.iterations += 1

                logger.info("步骤 {}/{} 迭代 [{}/{}]",
                            plan.current_step_index + 1, len(plan.steps),
                            iteration, self._step_max_iterations)

                _emit(AgentEvent(
                    type=EventType.THINKING,
                    iteration=iteration,
                    max_iterations=self._step_max_iterations,
                ))

                # 执行器上下文隔离（LangGraph State Scoping）：
                # 只携带 System Prompt + 当前步骤的 Scratchpad 消息，
                # 不携带 Scratchpad 之前的全局对话历史，避免消息条数超限。
                # step_prompt 已包含总目标和已完成步骤摘要，无需冗余历史。
                # compact_env=True：跳过工具列表注入，Function Calling 的 tools
                # 参数已携带完整 schema，无需 SYSTEM 消息重复，避免工具列表
                # 描述与步骤指令争夺 LLM 注意力导致回答偏离。
                # 使用 memory 中同步调整后的快照位置，防止 _smart_truncate
                # 截断旧消息导致 snapshot_pos 指向空区域。
                effective_snapshot = self._memory.active_snapshot_pos
                if effective_snapshot is None:
                    effective_snapshot = snapshot_pos
                scoped_messages = self._memory.messages_from(effective_snapshot)
                context_messages = self._context_builder.build(
                    scoped_messages, compact_env=True,
                )

                # ── 可观测性：打印 LLM 调用前的 context 摘要 ──
                self._log_context_summary(
                    context_messages, tools_schema,
                    step_index=plan.current_step_index + 1,
                    total_steps=len(plan.steps),
                    iteration=iteration,
                )

                response = self._llm.chat(
                    messages=context_messages,
                    tools=tools_schema,
                    temperature=self._step_temperature,
                    max_tokens=self._max_tokens,
                )
                metrics.record_llm_call(response.usage, call_type="step_chat")

                if not response.tool_calls:
                    self._memory.add_assistant_message(response)
                    logger.info("步骤 {}/{} 给出回答 | 迭代: {}",
                                plan.current_step_index + 1, len(plan.steps), iteration)
                    _emit(AgentEvent(
                        type=EventType.ANSWERING,
                        iteration=iteration,
                        max_iterations=self._step_max_iterations,
                    ))
                    step_result = response.content or ""
                    break

                # 工具调用
                logger.info("步骤 {}/{} 调用 {} 个工具",
                            plan.current_step_index + 1, len(plan.steps),
                            len(response.tool_calls))
                self._memory.add_assistant_message(response)
                self.execute_tool_calls(
                    response.tool_calls, metrics, _emit, wait_for_confirmation,
                )

                # 循环检测：如果检测到重复调用模式，插入引导 prompt
                loop_hint = self._loop_detector.get_loop_summary()
                if loop_hint:
                    logger.warning("步骤 {}/{} 循环检测触发，插入引导 prompt",
                                   plan.current_step_index + 1, len(plan.steps))
                    self._memory.add_message(
                        Message(role=Role.USER, content=loop_hint)
                    )
            else:
                # 超过子循环迭代限制，强制总结
                logger.warning("步骤 {}/{} 达到最大迭代数 {}，强制总结",
                              plan.current_step_index + 1, len(plan.steps),
                              self._step_max_iterations)
                step_result = self._force_step_answer(step, plan.goal, metrics)

            # ── Scratchpad: 回滚中间过程，沉淀结果 ──
            # 使用 memory 同步调整后的快照位置（_smart_truncate 可能已偏移原始 pos）
            effective_rollback_pos = self._memory.active_snapshot_pos
            if effective_rollback_pos is None:
                effective_rollback_pos = snapshot_pos
            removed = self._memory.rollback_to_snapshot(effective_rollback_pos)
            self._current_snapshot_pos = None
            if step_result is not None:
                self._memory.settle_step_result(step.description, step_result[:500])
            logger.debug("步骤 {} Scratchpad 清理 | 移除 {} 条中间消息", step.id, removed)

            return step_result

        except AgentStoppedError:
            # 被停止时也要回滚，避免半完成的中间消息污染后续
            eff_pos = self._memory.active_snapshot_pos or snapshot_pos
            self._memory.rollback_to_snapshot(eff_pos)
            self._current_snapshot_pos = None
            raise
        except Exception as e:
            # 异常时回滚 Scratchpad，避免污染
            eff_pos = self._memory.active_snapshot_pos or snapshot_pos
            self._memory.rollback_to_snapshot(eff_pos)
            self._current_snapshot_pos = None
            logger.error("步骤 {} 执行异常: {} | messages数: {} | tools数: {}",
                         step.id, e, len(self._memory.messages),
                         len(tools_schema) if tools_schema else 0)
            return None

    def _inject_context(self, query: str, metrics: RunMetrics) -> None:
        """注入知识库、长期记忆和 Skills（首步时调用一次）。"""
        # 知识库
        if self._knowledge_base and self._knowledge_base.count() > 0:
            threshold = settings.agent.kb_relevance_threshold
            results = self._knowledge_base.search(query, top_k=3, relevance_threshold=threshold)
            self._context_builder.set_knowledge(results)
            if results:
                metrics.kb_chunks_injected = len(results)
        else:
            self._context_builder.set_knowledge([])

        # 长期记忆
        if self._vector_store and self._vector_store.count() > 0:
            threshold = settings.agent.memory_relevance_threshold
            results = self._vector_store.search(query, top_k=3)
            self._context_builder.set_memory(results, relevance_threshold=threshold)
            relevant = [r for r in results if r.get("distance", 1.0) < threshold]
            if relevant:
                metrics.memory_items_injected = len(relevant)
        else:
            self._context_builder.set_memory([])

        # Skills
        if self._skill_router:
            matches = self._skill_router.match(query)
            if matches:
                self._context_builder.set_skills([m.skill for m in matches])
            else:
                self._context_builder.set_skills([])
        else:
            self._context_builder.set_skills([])

    def _check_and_compress(self, _emit) -> None:
        """检查并执行上下文压缩。"""
        estimate = self._context_builder.estimate_compression_needed(self._memory.messages)
        if not estimate:
            return

        _emit(AgentEvent(
            type=EventType.STATUS,
            message="🧠 正在整理长期记忆...",
        ))
        self._memory.compress(target_tokens=estimate.target_tokens)
        _emit(AgentEvent(
            type=EventType.STATUS,
            message="✅ 记忆整理完成",
        ))

    def _log_context_summary(
        self, context_messages: list, tools_schema: Optional[list],
        step_index: int, total_steps: int, iteration: int,
    ) -> None:
        """打印步骤 LLM 调用前的上下文摘要，用于调试和可观测性。"""
        from collections import Counter
        role_counts = Counter()
        for msg in context_messages:
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            role_counts[role] += 1

        tools_count = len(tools_schema) if tools_schema else 0
        msg_preview = []
        for i, msg in enumerate(context_messages):
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            content = (msg.content or "")[:80].replace("\n", "\\n")
            msg_preview.append(f"  [{i}] {role}: {content}...")

        logger.info(
            "步骤 {}/{} 迭代 {} LLM 调用摘要 | messages: {} 条 ({}) | tools: {} 个 | temp: {}",
            step_index, total_steps, iteration,
            len(context_messages),
            ", ".join(f"{r}={c}" for r, c in role_counts.items()),
            tools_count,
            self._step_temperature,
        )
        logger.debug("步骤 {}/{} context_messages 详情:\n{}", step_index, total_steps,
                      "\n".join(msg_preview))

    def _infer_expected_tools(self, step: PlanStep) -> Optional[list]:
        """从步骤描述和 tool_hint 推断预期工具列表（用于 L3 偏离检测）。

        基于关键词匹配将步骤目标映射到可能需要的工具集合。
        返回 None 表示无法推断（不启用 L3 检测）。
        """
        # 如果步骤有明确的 tool_hint，直接使用
        if step.tool_hint:
            return [step.tool_hint]

        # 从步骤描述中关键词匹配
        desc = step.description.lower()
        inferred = set()

        # 关键词 → 工具名映射（可扩展）
        keyword_tool_map = {
            ("kubernetes", "k8s", "kubectl", "集群", "节点", "pod", "deployment",
             "daemonset", "statefulset", "namespace", "event", "事件", "工作负载"): "kubectl",
            ("docker", "容器", "镜像", "container", "image"): "docker",
            ("http", "api", "url", "curl", "请求", "接口"): "curl",
            ("文件", "读取", "file", "read", "write", "目录"): "file_reader",
            ("搜索", "search", "查找", "检索"): "web_search",
        }

        for keywords, tool_name in keyword_tool_map.items():
            if any(kw in desc for kw in keywords):
                inferred.add(tool_name)

        return list(inferred) if inferred else None

    def _force_step_answer(self, step: PlanStep, goal: str, metrics: RunMetrics) -> str:
        """强制当前步骤生成回答。

        在达到最大迭代数时调用。prompt 中重新注入步骤目标，
        避免 LLM 在偏离后生成无关的总结。
        """
        self._memory.add_user_message(
            f"你已达到最大迭代次数。请立即停止工具调用，直接给出当前步骤的执行结果。\n\n"
            f"提醒 - 总目标：{goal}\n"
            f"提醒 - 当前步骤：{step.description}\n\n"
            f"请根据以上工具调用返回的实际数据，总结当前步骤的执行结果。"
            f"如果工具没有返回有效数据，请如实说明。"
        )
        # 使用 Scratchpad 局部视图，与 _execute_step 保持一致
        # 优先使用 memory 同步调整后的快照位置
        effective_pos = self._memory.active_snapshot_pos
        if effective_pos is None:
            effective_pos = self._current_snapshot_pos
        if effective_pos is not None:
            scoped_messages = self._memory.messages_from(effective_pos)
        else:
            scoped_messages = self._memory.messages
        context_messages = self._context_builder.build(
            scoped_messages, compact_env=True,
        )
        response = self._llm.chat(
            messages=context_messages,
            tools=None,
            temperature=self._step_temperature,
            max_tokens=self._max_tokens,
        )
        metrics.record_llm_call(response.usage, call_type="force_step_answer")
        self._memory.add_assistant_message(response)
        return response.content or ""

    def _try_replan(self, plan: Plan, _emit) -> Optional[list]:
        """尝试重新规划剩余步骤。"""
        _emit(AgentEvent(
            type=EventType.REPLAN,
            step_index=plan.current_step_index,
            total_steps=len(plan.steps),
            message=f"步骤失败，正在重新规划（第 {plan.replan_count + 1} 次）...",
        ))

        new_steps = replan(self._llm, plan, temperature=0.3)
        if new_steps:
            logger.info("Replan 成功 | 新步骤数: {}", len(new_steps))
            _emit(AgentEvent(
                type=EventType.PLAN_CREATED,
                plan=Plan(
                    goal=plan.goal,
                    steps=plan.completed_steps + new_steps,
                    current_step_index=len(plan.completed_steps),
                    replan_count=plan.replan_count + 1,
                ).to_dict(),
                total_steps=len(plan.completed_steps) + len(new_steps),
                message=f"已重新规划，剩余 {len(new_steps)} 步",
            ))
        return new_steps

    def _synthesize_answer(self, plan: Plan, metrics: RunMetrics) -> str:
        """综合所有步骤结果，生成最终回答。

        使用 Scratchpad 局部视图：synthesis_prompt 已自包含所有步骤结果，
        不需要携带全局对话历史，避免消息条数超限。
        """
        step_results = "\n".join(
            f"{i + 1}. {s.description}\n   结果: {s.result_summary}"
            for i, s in enumerate(plan.steps)
            if s.status == StepStatus.COMPLETED
        )

        if not step_results:
            return "抱歉，计划中的所有步骤都未能成功执行。"

        synthesis_prompt = _FINAL_SYNTHESIS_PROMPT.format(
            goal=plan.goal,
            step_results=step_results,
        )

        # 综合阶段的上下文隔离：
        # 1. 快照 → 写入 synthesis_prompt → 用局部视图 build → 回滚
        # 2. 不使用工具，清除 tools 预留
        snapshot_pos = self._memory.snapshot()
        self._memory.add_user_message(synthesis_prompt)
        self._context_builder.set_tools_reserve(None)

        scoped_messages = self._memory.messages_from(snapshot_pos)
        context_messages = self._context_builder.build(scoped_messages)
        response = self._llm.chat(
            messages=context_messages,
            tools=None,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        metrics.record_llm_call(response.usage, call_type="synthesize")

        # 回滚 synthesis 的临时消息，将最终回答沉淀到全局历史
        self._memory.rollback_to_snapshot(snapshot_pos)
        self._memory.add_assistant_message(response)
        return response.content or "抱歉，无法综合执行结果。"

    def _fallback_direct_answer(
        self, user_input: str, metrics: RunMetrics,
        _emit, wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """计划生成失败时，回退为直接 ReAct 模式。"""
        from src.agent.react_agent import ReActAgent

        logger.info("回退到 ReAct 直接回答模式")

        # 构造临时 ReActAgent，共享所有组件
        react = ReActAgent(
            llm_client=self._llm,
            tool_registry=self._tools,
            memory=self._memory,
            context_builder=self._context_builder,
            vector_store=self._vector_store,
            knowledge_base=self._knowledge_base,
            skill_router=self._skill_router,
            env_adapter=self._env_adapter,
            max_iterations=self._max_iterations,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        result = react.run(user_input, on_event=_emit,
                          wait_for_confirmation=wait_for_confirmation)
        # 合并 metrics：将 ReAct 子 agent 的明细记录合入主 metrics
        if react.last_metrics:
            rm = react.last_metrics
            metrics.iterations = rm.iterations
            metrics.llm_calls.extend(rm.llm_calls)
            metrics.tool_calls.extend(rm.tool_calls)
            metrics.total_input_tokens += rm.total_input_tokens
            metrics.total_output_tokens += rm.total_output_tokens
            metrics.kb_chunks_injected = rm.kb_chunks_injected
            metrics.memory_items_injected = rm.memory_items_injected
        metrics.finish()
        self._last_metrics = metrics
        return result
