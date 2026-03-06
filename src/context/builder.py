"""上下文构建器 - Zone 分层组装 LLM 请求上下文。

将"上下文组装"从 ConversationMemory 和 ReActAgent 中抽离，
作为独立的职责存在，解决以下问题：

1. KB/长期记忆注入直接写入 ConversationMemory 导致的污染
   - 被 _smart_truncate 视为 SYSTEM 消息永不截断
   - 被摘要压缩混入，产生无意义的总结
   - 破坏 system prompt 缓存前缀的稳定性

2. Agent 直接传 memory.messages 给 LLM，缺乏中间组装层

Zone 架构（从稳定到动态）：
┌──────────────────────────────────────────────┐
│ System Zone      — system prompt（稳定前缀）  │
├──────────────────────────────────────────────┤
│ Environment Zone — 运行时环境信息（每次更新）  │
├──────────────────────────────────────────────┤
│ Skill Zone       — 领域专家 prompt（按需注入） │
├──────────────────────────────────────────────┤
│ Inject Zone      — KB/长期记忆（按需临时注入） │
├──────────────────────────────────────────────┤
│ History Zone     — 对话历史（动态）            │
└──────────────────────────────────────────────┘
"""

from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from src.config import settings
from src.llm.base_client import Message, Role
from src.memory.token_counter import TokenCounter
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.skills.base import Skill
    from src.tools.base_tool import ToolRegistry

# 环境变量提供者类型：返回 key→value 的字典
EnvironmentProvider = Callable[[], Dict[str, str]]


def default_environment() -> Dict[str, str]:
    """默认的环境信息提供者：当前时间。"""
    now = datetime.now()
    return {
        "当前时间": now.strftime("%Y-%m-%d %H:%M:%S (%A)"),
    }


def tool_environment(registry: "ToolRegistry") -> EnvironmentProvider:
    """创建工具列表环境提供者。

    将已注册工具的摘要信息注入 Environment Zone，
    使 Skill 可以直接引用可用工具列表，无需运行时扫描目录。

    Args:
        registry: 工具注册中心实例。

    Returns:
        EnvironmentProvider 闭包。
    """
    def provider() -> Dict[str, str]:
        return {"可用工具": registry.get_tools_summary()}
    return provider


@dataclass
class ContextBuildStats:
    """build() 后各 Zone 的 Token 占用统计。"""

    # 各 Zone 实际 Token 使用量
    system_tokens: int = 0
    environment_tokens: int = 0
    skill_tokens: int = 0
    knowledge_tokens: int = 0
    memory_tokens: int = 0
    history_tokens: int = 0
    total_tokens: int = 0
    history_budget: int = 0  # History Zone 的动态预算

    # Zone 预算上限（Sprint 3）
    input_budget: int = 0
    skill_budget: int = 0
    knowledge_budget: int = 0
    memory_budget: int = 0

    # 是否发生截断（Sprint 3）
    skill_truncated: bool = False
    knowledge_truncated: bool = False
    memory_truncated: bool = False
    history_truncated: bool = False  # Phase 4 紧急截断

    # Tools schema 预留
    tools_token_reserve: int = 0

    # Archive Zone（Sprint 2: 对话归档检索）
    archive_tokens: int = 0
    archive_budget: int = 0
    archive_truncated: bool = False

    # 工具结果精简统计
    tool_results_compacted: int = 0  # 被精简的工具返回消息数

    # Session Summary（Sprint 3: 会话级概要）
    session_summary_tokens: int = 0

    @property
    def non_history_tokens(self) -> int:
        """History 以外所有 Zone 的 Token 总和。"""
        return self.total_tokens - self.history_tokens


def _summarize_json_result(tool_name: str, data: Any) -> str:
    """将 JSON 格式的工具返回提炼为一行摘要。

    覆盖常见数据结构：
    - list: "[工具 X 返回 N 条结果]" + 首条样例的 key
    - dict with list value: 找到主列表字段，报告条数
    - dict with status/result: 提取状态和关键字段
    - 其他: 报告 key 列表

    Args:
        tool_name: 工具名称。
        data: 已解析的 JSON 对象。

    Returns:
        精简摘要字符串。
    """
    if isinstance(data, list):
        count = len(data)
        if count == 0:
            return f"[工具 {tool_name} 返回空列表]"
        # 提取首条记录的 key 作为 schema 提示
        sample_keys = ""
        if isinstance(data[0], dict):
            keys = list(data[0].keys())[:5]
            sample_keys = f"，字段: {', '.join(keys)}"
        return f"[工具 {tool_name} 返回 {count} 条结果{sample_keys}]"

    if isinstance(data, dict):
        # 检查是否包含 status/error 字段
        status = data.get("status") or data.get("state") or data.get("code")
        if status is not None:
            msg = data.get("message") or data.get("msg") or data.get("detail") or ""
            summary = f"[工具 {tool_name} 返回 status={status}"
            if msg:
                summary += f", message={str(msg)[:100]}"
            summary += "]"
            return summary

        # 检查是否有主列表字段（如 data, items, results, records）
        for list_key in ("data", "items", "results", "records", "list"):
            if list_key in data and isinstance(data[list_key], list):
                count = len(data[list_key])
                return f"[工具 {tool_name} 返回 {count} 条 {list_key}]"

        # 兜底：列出 top-level keys
        keys = list(data.keys())[:8]
        return f"[工具 {tool_name} 返回 dict，keys: {', '.join(keys)}]"

    # 标量值
    return f"[工具 {tool_name} 返回: {str(data)[:200]}]"


class ContextBuilder:
    """Zone 分层上下文构建器。

    每次 LLM 调用前构建完整的 messages 列表，
    KB/长期记忆作为临时注入，不污染 ConversationMemory。

    用法：
        builder = ContextBuilder()
        messages = (
            builder
            .set_skill(skill)
            .set_knowledge(kb_results)
            .set_memory(memory_results)
            .build(conversation_messages)
        )
        # messages 可直接传给 LLM.chat()
    """

    def __init__(
        self,
        environment_providers: Optional[List[EnvironmentProvider]] = None,
        model: str = "gpt-4o",
    ):
        """
        Args:
            environment_providers: 环境信息提供者列表。每个提供者返回 Dict[str, str]，
                所有结果合并后作为 Environment Zone 内容。
                默认包含 default_environment（当前时间）。
            model: 模型名称，用于 TokenCounter 选择正确的编码器。
        """
        self._environment_providers: List[EnvironmentProvider] = (
            environment_providers if environment_providers is not None
            else [default_environment]
        )
        self._skill_messages: List[Message] = []
        self._knowledge_messages: List[Message] = []
        self._memory_messages: List[Message] = []
        self._archive_messages: List[Message] = []
        self._session_summary: Optional[Message] = None
        self._token_counter = TokenCounter(model)
        self._last_build_stats: Optional[ContextBuildStats] = None
        # 输入预算 = context_window - max_output_tokens
        self._input_budget = max(settings.llm.context_window - settings.agent.max_tokens, 0)
        # Tools schema 预留 token（由 set_tools_reserve() 设置）
        self._tools_token_reserve: int = 0

    @property
    def last_build_stats(self) -> Optional[ContextBuildStats]:
        """最近一次 build() 的各 Zone Token 统计。首次 build 前为 None。"""
        return self._last_build_stats

    @property
    def effective_input_budget(self) -> int:
        """扣除 tools schema 预留后的实际 messages 预算。

        LLM API 的实际输入 = messages tokens + tools schema tokens。
        ContextBuilder 只管 messages，因此 messages 预算需要扣除 tools 占用。
        """
        return max(self._input_budget - self._tools_token_reserve, 0)

    def set_tools_reserve(self, tools_schema: Optional[List[Dict[str, Any]]]) -> "ContextBuilder":
        """计算 tools schema 的 token 占用并预留。

        每次 Agent run() 开始时调用一次（tools 列表在运行期间不变），
        将 tools JSON Schema 的 token 数从 messages 预算中扣除，
        确保 messages + tools 不超过模型的 input 限制。

        Args:
            tools_schema: OpenAI tools 格式的工具定义列表。None 表示不使用工具。

        Returns:
            self（支持链式调用）。
        """
        if not tools_schema:
            self._tools_token_reserve = 0
            return self

        import json
        schema_text = json.dumps(tools_schema, ensure_ascii=False)
        self._tools_token_reserve = self._token_counter.count_text(schema_text)
        logger.debug("Tools schema 预留: {} tokens（{} 个工具）",
                     self._tools_token_reserve, len(tools_schema))
        return self

    def set_skills(self, skills: List["Skill"]) -> "ContextBuilder":
        """设置当前激活的 Skills（按需注入领域专家 prompt）。

        对于包含附属资源的 Skill，会在 system_prompt 后追加资源导航提示，
        引导 Agent 通过 fs_read 按需加载 Level 3 资源。

        Args:
            skills: 匹配到的 Skill 列表（通常 0~2 个）。
        """
        if not skills:
            self._skill_messages = []
            return self

        parts = []
        for s in skills:
            prompt = s.system_prompt
            # 追加资源导航提示（Level 3 渐进式披露）
            resource_hint = self._build_resource_hint(s)
            if resource_hint:
                prompt = f"{prompt}\n\n{resource_hint}"
            parts.append(prompt)

        self._skill_messages = [
            Message(
                role=Role.SYSTEM,
                content="\n\n".join(parts),
            )
        ]
        skill_names = [s.name for s in skills]
        logger.debug("ContextBuilder: 设置 {} 个 Skill: {}", len(skills), skill_names)
        return self

    @staticmethod
    def _build_resource_hint(skill: "Skill") -> str:
        """为包含附属资源的 Skill 构建资源导航提示。

        仅列出文件路径索引，Agent 可通过 fs_read 按需加载具体内容，
        实现 Level 3 渐进式披露，避免一次性注入过多 token。

        Args:
            skill: Skill 实例。

        Returns:
            资源导航提示字符串；无资源时返回空字符串。
        """
        if not skill.has_resources:
            return ""

        lines = ["---", "📂 可用资源（按需使用 fs_read 读取）:"]

        if skill.references:
            lines.append("  参考资料:")
            for ref in skill.references:
                full_path = f"{skill.base_dir}/{ref}" if skill.base_dir else ref
                lines.append(f"    - {full_path}")

        if skill.scripts:
            lines.append("  脚本:")
            for script in skill.scripts:
                full_path = f"{skill.base_dir}/{script}" if skill.base_dir else script
                lines.append(f"    - {full_path}")

        return "\n".join(lines)

    def set_knowledge(self, results: List[dict]) -> "ContextBuilder":
        """设置知识库检索结果（临时注入，不持久化）。

        Args:
            results: 知识库检索结果列表，每项含 'text' 和 'metadata'。
        """
        if not results:
            self._knowledge_messages = []
            return self

        kb_text = "\n\n".join(
            f"[文档片段 {i + 1}] (来源: {r['metadata'].get('filename', '未知')})\n{r['text']}"
            for i, r in enumerate(results)
        )
        self._knowledge_messages = [
            Message(
                role=Role.SYSTEM,
                content=f"[知识库检索结果]\n{kb_text}",
            )
        ]
        logger.debug("ContextBuilder: 设置 {} 条知识库片段", len(results))
        return self

    def set_memory(self, results: List[dict], relevance_threshold: float = 0.8) -> "ContextBuilder":
        """设置长期记忆检索结果（临时注入，不持久化）。

        Args:
            results: 长期记忆检索结果列表，每项含 'text' 和 'distance'。
            relevance_threshold: 相关度阈值（cosine distance），低于此值才认为相关。
        """
        if not results:
            self._memory_messages = []
            return self

        # 过滤不相关结果 + 去重
        relevant = [r for r in results if r.get("distance", 1.0) < relevance_threshold]
        seen_texts = set()
        unique_results = []
        for r in relevant:
            text_key = r["text"][:100]
            if text_key not in seen_texts:
                seen_texts.add(text_key)
                unique_results.append(r)

        if not unique_results:
            self._memory_messages = []
            return self

        memory_text = "\n".join(f"- {r['text']}" for r in unique_results)
        self._memory_messages = [
            Message(
                role=Role.SYSTEM,
                content=f"[相关历史记忆]\n{memory_text}",
            )
        ]
        logger.debug("ContextBuilder: 设置 {} 条长期记忆（去重后）", len(unique_results))
        return self

    def set_archive(self, results: List[dict], relevance_threshold: float = 0.8) -> "ContextBuilder":
        """设置对话归档检索结果（临时注入，不持久化）。

        Args:
            results: ConversationArchive.search() 返回的结果列表，
                每项含 'text', 'metadata', 'distance'。
            relevance_threshold: 相关度阈值（cosine distance），低于此值才认为相关。
        """
        if not results:
            self._archive_messages = []
            return self

        relevant = [r for r in results if r.get("distance", 1.0) < relevance_threshold]
        if not relevant:
            self._archive_messages = []
            return self

        archive_text = "\n\n".join(
            f"[历史交互 {i + 1}]\n{r['text']}"
            for i, r in enumerate(relevant)
        )
        self._archive_messages = [
            Message(
                role=Role.SYSTEM,
                content=f"[相关历史对话]\n{archive_text}",
            )
        ]
        logger.debug("ContextBuilder: 设置 {} 条对话归档片段", len(relevant))
        return self

    def clear_injections(self) -> "ContextBuilder":
        """清除所有临时注入（Skills + KB + 长期记忆 + 归档），用于新一轮对话。

        注意：Session Summary 不清除——它跨越整个对话生命周期，
        由 set_session_summary() 显式更新。
        """
        self._skill_messages = []
        self._knowledge_messages = []
        self._memory_messages = []
        self._archive_messages = []
        return self

    def set_session_summary(self, summary: str) -> "ContextBuilder":
        """设置当前会话概要（注入 History Zone 头部）。

        Session Summary 是 History Zone 的全局概要，逻辑上属于 History，
        不作为独立 Zone 管理预算。token 占用很小（~200），从 History Zone
        预算中扣除。

        Args:
            summary: 会话概要文本。为空时清除。
        """
        if not summary or not summary.strip():
            self._session_summary = None
            return self

        self._session_summary = Message(
            role=Role.SYSTEM,
            content=f"[当前会话概要] {summary}",
        )
        logger.debug("ContextBuilder: 设置 Session Summary（{}字符）", len(summary))
        return self

    def estimate_compression_needed(self, conversation_messages: List[Message]) -> Optional["CompressionEstimate"]:
        """估算是否需要压缩 History Zone。

        在正式 build 之前调用，用当前已设置的注入内容估算 non-history tokens，
        再与 history tokens 比较，判断是否超过水位线。

        注意：
        - 估算时也应用 Zone budget cap，以获取截断后的真实 non-history tokens。
        - 使用 effective_input_budget（已扣除 tools schema 预留），确保预算准确。

        Args:
            conversation_messages: ConversationMemory 中的消息列表（含 system prompt）。

        Returns:
            CompressionEstimate 如果需要压缩；None 如果不需要。
        """
        effective_budget = self.effective_input_budget
        if effective_budget <= 0:
            return None

        # 拆分 system prompt 和 history
        system_msgs = []
        history_msgs = []
        for msg in conversation_messages:
            if msg.role == Role.SYSTEM:
                system_msgs.append(msg)
            else:
                history_msgs.append(msg)

        # 估算各 non-history Zone 的 token（应用 zone budget cap）
        count = self._token_counter.count_messages
        env_msg = self._build_environment_message()

        skill_budget, knowledge_budget, memory_budget, archive_budget = self._compute_zone_budgets()
        _, skill_tokens, _ = self._truncate_zone(self._skill_messages, skill_budget)
        _, kb_tokens, _ = self._truncate_zone(self._knowledge_messages, knowledge_budget)
        _, mem_tokens, _ = self._truncate_zone(self._memory_messages, memory_budget)
        _, arc_tokens, _ = self._truncate_zone(self._archive_messages, archive_budget)

        non_history_tokens = (
            count(system_msgs)
            + (count([env_msg]) if env_msg else 0)
            + skill_tokens
            + kb_tokens
            + mem_tokens
            + arc_tokens
            + (count([self._session_summary]) if self._session_summary else 0)
        )

        history_budget_val = max(effective_budget - non_history_tokens, 0)
        # 估算时也应用工具结果精简（与 build() Phase 3 一致）
        compacted_history, _ = self._compact_tool_results(history_msgs, settings.agent.recent_window_size)
        history_tokens = count(compacted_history)
        threshold = settings.agent.compression_threshold

        if history_budget_val > 0 and history_tokens > history_budget_val * threshold:
            target_tokens = int(history_budget_val * settings.agent.compression_target_ratio)
            return CompressionEstimate(
                history_tokens=history_tokens,
                history_budget=history_budget_val,
                target_tokens=target_tokens,
            )
        return None

    def _build_environment_message(self, *, compact: bool = False) -> Optional[Message]:
        """收集所有环境信息提供者的数据，构建 Environment Zone 消息。

        Args:
            compact: 紧凑模式。为 True 时只保留单行值（如当前时间），
                跳过多行值（如工具列表）。用于 Plan-Execute 执行器的
                上下文隔离——Function Calling 的 tools 参数已携带工具
                schema，无需在 SYSTEM 消息中重复列出工具列表，避免
                工具列表描述与步骤指令争夺 LLM 注意力。

        Returns:
            环境信息消息；无提供者或全部失败时返回 None。
        """
        if not self._environment_providers:
            return None

        env_items: Dict[str, str] = {}
        for provider in self._environment_providers:
            try:
                env_items.update(provider())
            except Exception as e:
                logger.warning("环境信息提供者执行失败: {}", e)

        if not env_items:
            return None

        # 单行值用 " | " 紧凑拼接，多行值（如工具列表）独立成段
        inline_parts = []
        block_parts = []
        for k, v in env_items.items():
            if "\n" in v:
                block_parts.append(v)
            else:
                inline_parts.append(f"{k}: {v}")

        sections = []
        if inline_parts:
            sections.append(" | ".join(inline_parts))
        if not compact:
            sections.extend(block_parts)

        if not sections:
            return None

        return Message(
            role=Role.SYSTEM,
            content="\n\n".join(sections),
        )

    def _compute_zone_budgets(self) -> tuple:
        """计算可截断 Zone 的预算上限。

        使用 effective_input_budget（已扣除 tools schema 预留）作为基准。

        Returns:
            (skill_budget, knowledge_budget, memory_budget, archive_budget) 四元组，单位为 tokens。
        """
        budget = self.effective_input_budget
        return (
            int(budget * settings.agent.skill_zone_max_ratio),
            int(budget * settings.agent.knowledge_zone_max_ratio),
            int(budget * settings.agent.memory_zone_max_ratio),
            int(budget * settings.agent.archive_zone_max_ratio),
        )

    def _truncate_zone(self, messages: List[Message], budget: int) -> tuple:
        """按预算截断 Zone 消息。

        截断策略：
        - 如果 budget <= 0，直接返回空
        - 逐条累加 token，超出预算时停止
        - 对于单条大消息（如 KB 合并为一条），按文本行级截断

        Args:
            messages: Zone 的原始消息列表。
            budget: Token 预算上限。

        Returns:
            (truncated_messages, actual_tokens, was_truncated) 三元组。
        """
        if not messages or budget <= 0:
            return [], 0, False

        count = self._token_counter.count_messages
        total_tokens = count(messages)

        # 未超预算，原样返回
        if total_tokens <= budget:
            return messages, total_tokens, False

        # 多条消息：逐条累加，溢出时丢弃后续
        if len(messages) > 1:
            kept = []
            accumulated = 0
            for msg in messages:
                msg_tokens = count([msg])
                if accumulated + msg_tokens > budget:
                    break
                kept.append(msg)
                accumulated = msg_tokens  # count_messages 包含全局开销，这里用累积值
            # 重新精确计算
            if kept:
                actual = count(kept)
                return kept, actual, True
            return [], 0, True

        # 单条大消息：按行截断内容
        msg = messages[0]
        content = msg.content or ""
        lines = content.split("\n")

        # 保留首行标题（如 "[知识库检索结果]"）+ 逐行累加
        kept_lines = [lines[0]] if lines else []
        for line in lines[1:]:
            candidate = "\n".join(kept_lines + [line])
            candidate_msg = [Message(role=msg.role, content=candidate)]
            if count(candidate_msg) > budget:
                break
            kept_lines.append(line)

        truncated_content = "\n".join(kept_lines)
        if truncated_content != content:
            truncated_content += "\n[... 已截断以适应上下文预算 ...]"
        truncated_msg = Message(role=msg.role, content=truncated_content)
        actual_tokens = count([truncated_msg])
        return [truncated_msg], actual_tokens, True

    def _compact_tool_results(
        self,
        history_msgs: List[Message],
        recent_window_size: int,
    ) -> tuple:
        """精简 Recent Window 之外的工具返回消息，降低 History Zone token 占用。

        策略：
        - 最近 recent_window_size 条消息保持原样（完整保留工具交互细节）
        - 更早的 TOOL role 消息：内容替换为一行摘要
        - 更早的 ASSISTANT(tool_calls) 消息：保持原样（结构信息需保留给 LLM 理解对话流）
        - 非 TOOL role 的普通消息：保持原样

        此方法不修改 ConversationMemory 的实际数据，仅影响本次 build() 的输出，
        与 Phase 4 紧急截断的设计理念一致。

        Args:
            history_msgs: 对话历史消息列表（不含 system prompt）。
            recent_window_size: 最近 K 条消息完整保留的窗口大小。

        Returns:
            (compacted_messages, compacted_count) 二元组。
            compacted_messages 与输入长度相同，仅 TOOL 消息内容被替换；
            compacted_count 为被精简的 TOOL 消息数。
        """
        if not history_msgs or recent_window_size <= 0:
            return history_msgs, 0

        total = len(history_msgs)
        if total <= recent_window_size:
            return history_msgs, 0

        # 分界点：recent_window_size 之前的是旧消息区域
        old_boundary = total - recent_window_size
        compacted = []
        compacted_count = 0

        for i, msg in enumerate(history_msgs):
            if i < old_boundary and msg.role == Role.TOOL:
                # 精简旧的工具返回内容
                compact_content = self._make_tool_compact_summary(msg)
                compacted.append(Message(
                    role=msg.role,
                    content=compact_content,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                ))
                compacted_count += 1
            else:
                compacted.append(msg)

        if compacted_count > 0:
            old_tokens = self._token_counter.count_messages(history_msgs)
            new_tokens = self._token_counter.count_messages(compacted)
            saved = old_tokens - new_tokens
            logger.info(
                "工具结果精简 | 精简 {} 条旧 tool 消息 | tokens: {} → {} (节省 {}, {:.0f}%)",
                compacted_count, old_tokens, new_tokens, saved,
                (saved / old_tokens * 100) if old_tokens > 0 else 0,
            )

        return compacted, compacted_count

    @staticmethod
    def _make_tool_compact_summary(msg: Message) -> str:
        """为单条 TOOL 消息生成精简摘要。

        策略：
        - 尝试解析 JSON 提取结构信息（条目数、状态等）
        - 识别错误/失败消息并保留
        - 兜底：截取前 100 字符 + 省略标记

        Args:
            msg: TOOL role 的消息。

        Returns:
            精简后的摘要字符串。
        """
        content = msg.content or ""
        tool_name = msg.name or "unknown"

        if not content.strip():
            return f"[工具 {tool_name} 返回空结果]"

        # 识别错误消息 — 保留完整内容（通常较短）
        error_indicators = ("error", "错误", "失败", "exception", "traceback", "failed")
        content_lower = content.lower()
        if any(ind in content_lower for ind in error_indicators):
            # 错误信息通常不长，保留但截断到 500 字符
            if len(content) <= 500:
                return content
            return content[:500] + "\n[... 错误详情已截断 ...]"

        # 尝试 JSON 解析，提取结构信息
        import json
        try:
            data = json.loads(content)
            return _summarize_json_result(tool_name, data)
        except (json.JSONDecodeError, TypeError):
            pass

        # 兜底：纯文本截断
        if len(content) <= 150:
            return content
        return f"[工具 {tool_name} 执行完成，返回 {len(content)} 字符结果]\n{content[:100]}..."

    def _emergency_truncate_history(
        self,
        system_msgs: List[Message],
        env_msg: Optional[Message],
        skill_msgs: List[Message],
        kb_msgs: List[Message],
        mem_msgs: List[Message],
        arc_msgs: List[Message],
        history_msgs: List[Message],
        budget: int,
    ) -> tuple:
        """紧急截断 History Zone，确保总 messages tokens ≤ budget。

        从 history_msgs 头部（最早的消息）开始逐条移除，
        直到重组后的 result 总 token 数在预算内。

        注意：不修改 ConversationMemory 的实际数据，仅影响本次 build() 输出。

        Args:
            system_msgs: System Zone 消息。
            env_msg: Environment Zone 消息（可能为 None）。
            skill_msgs: Skill Zone 消息。
            kb_msgs: Knowledge Zone 消息。
            mem_msgs: Memory Zone 消息。
            arc_msgs: Archive Zone 消息。
            history_msgs: History Zone 消息（会被修改）。
            budget: 有效 messages 预算。

        Returns:
            (result, remaining_history_msgs, history_tokens) 三元组。
        """
        count = self._token_counter.count_messages

        # 构造非 history 部分
        non_history = list(system_msgs)
        if env_msg:
            non_history.append(env_msg)
        non_history.extend(skill_msgs)
        non_history.extend(kb_msgs)
        non_history.extend(mem_msgs)
        non_history.extend(arc_msgs)
        non_history_tokens = count(non_history) if non_history else 0

        # 计算 history 可用预算
        history_budget = max(budget - non_history_tokens, 0)

        # 从头部移除旧消息，直到 history 部分 ≤ history_budget
        remaining = list(history_msgs)
        while remaining and count(remaining) > history_budget:
            remaining.pop(0)

        result = non_history + remaining
        history_tokens = count(remaining) if remaining else 0

        logger.info("紧急截断完成 | 移除 {} 条旧 history | 剩余 {} 条 | history_tokens={}",
                    len(history_msgs) - len(remaining), len(remaining), history_tokens)
        return result, remaining, history_tokens

    def build(
        self,
        conversation_messages: List[Message],
        max_history: Optional[int] = None,
        compact_env: bool = False,
    ) -> List[Message]:
        """组装完整的 LLM 请求上下文。

        Zone 顺序：System → Environment → Skill → Inject(KB + Memory) → History(对话历史)

        可截断 Zone（Skill/Knowledge/Memory）按预算上限截断，
        多余空间自动归还给 History Zone。

        Args:
            conversation_messages: ConversationMemory 中的消息列表（含 system prompt）。
            max_history: 可选，限制 History Zone 的最大消息条数（从末尾保留）。
            compact_env: 紧凑环境模式。为 True 时 Environment Zone 只保留
                单行值（如当前时间），跳过多行值（如工具列表）。
                用于 Plan-Execute 执行器——tools 参数已携带完整
                工具 schema，无需在 SYSTEM 消息中重复。

        Returns:
            组装后的完整 messages 列表，可直接传给 LLM.chat()。
        """
        # 拆分 conversation_messages：system prompt vs 对话历史
        system_msgs = []
        history_msgs = []
        for msg in conversation_messages:
            if msg.role == Role.SYSTEM:
                system_msgs.append(msg)
            else:
                history_msgs.append(msg)

        # 如果指定了 max_history，按条数截断对话历史（保留最近的）
        if max_history is not None and len(history_msgs) > max_history:
            removed = len(history_msgs) - max_history
            history_msgs = history_msgs[-max_history:]
            logger.debug("ContextBuilder: 按条数截断历史消息，移除了 {} 条，保留 {} 条", removed, max_history)

        # Phase 1: 不可截断 Zone
        result = []
        result.extend(system_msgs)                    # System Zone（稳定前缀）

        env_msg = self._build_environment_message(compact=compact_env)  # Environment Zone
        if env_msg:
            result.append(env_msg)

        # Phase 2: 可截断 Zone — 按预算上限截断
        skill_budget, knowledge_budget, memory_budget, archive_budget = self._compute_zone_budgets()

        skill_msgs, skill_tokens, skill_truncated = self._truncate_zone(self._skill_messages, skill_budget)
        kb_msgs, kb_tokens, kb_truncated = self._truncate_zone(self._knowledge_messages, knowledge_budget)
        mem_msgs, mem_tokens, mem_truncated = self._truncate_zone(self._memory_messages, memory_budget)
        arc_msgs, arc_tokens, arc_truncated = self._truncate_zone(self._archive_messages, archive_budget)

        result.extend(skill_msgs)                     # Skill Zone（按预算截断）
        result.extend(kb_msgs)                        # Knowledge Zone（按预算截断）
        result.extend(mem_msgs)                       # Memory Zone（按预算截断）
        result.extend(arc_msgs)                       # Archive Zone（按预算截断）

        # Phase 3: History Zone（剩余全部空间）
        # 精简 Recent Window 之外的工具返回消息，降低 token 占用
        history_msgs, tool_compacted_count = self._compact_tool_results(
            history_msgs, settings.agent.recent_window_size,
        )
        # Session Summary 注入 History Zone 头部（逻辑上是 History 的全局概要）
        session_summary_tokens = 0
        if self._session_summary:
            session_summary_tokens = count([self._session_summary])
            history_msgs = [self._session_summary] + history_msgs
        result.extend(history_msgs)

        # 各 Zone Token 统计
        count = self._token_counter.count_messages
        system_tokens = count(system_msgs)
        env_tokens = count([env_msg]) if env_msg else 0
        history_tokens = count(history_msgs)

        effective_budget = self.effective_input_budget
        non_history_tokens = (
            system_tokens + env_tokens + skill_tokens + kb_tokens
            + mem_tokens + arc_tokens + session_summary_tokens
        )
        history_budget_val = max(effective_budget - non_history_tokens, 0) if effective_budget > 0 else 0

        # Phase 4: 最终安全检查 — 确保 total messages tokens ≤ effective_input_budget
        # 当 tools schema 占用未被纳入预算、或 tiktoken 估算偏差时，这是最后的兜底
        history_truncated = False
        total_tokens = count(result)
        if effective_budget > 0 and total_tokens > effective_budget:
            overflow = total_tokens - effective_budget
            logger.warning(
                "上下文溢出 | total={} > budget={}, 溢出={} tokens, 紧急截断 History",
                total_tokens, effective_budget, overflow,
            )
            result, history_msgs, history_tokens = self._emergency_truncate_history(
                system_msgs, env_msg, skill_msgs, kb_msgs, mem_msgs, arc_msgs,
                history_msgs, effective_budget,
            )
            history_truncated = True
            total_tokens = count(result)

        if skill_truncated or kb_truncated or mem_truncated or arc_truncated or history_truncated:
            logger.info(
                "Zone 截断 | skill: {}→{}/{} | kb: {}→{}/{} | mem: {}→{}/{} | arc: {}→{}/{} | history: {}",
                "截断" if skill_truncated else "正常", skill_tokens, skill_budget,
                "截断" if kb_truncated else "正常", kb_tokens, knowledge_budget,
                "截断" if mem_truncated else "正常", mem_tokens, memory_budget,
                "截断" if arc_truncated else "正常", arc_tokens, archive_budget,
                "紧急截断" if history_truncated else "正常",
            )

        self._last_build_stats = ContextBuildStats(
            system_tokens=system_tokens,
            environment_tokens=env_tokens,
            skill_tokens=skill_tokens,
            knowledge_tokens=kb_tokens,
            memory_tokens=mem_tokens,
            archive_tokens=arc_tokens,
            history_tokens=history_tokens,
            total_tokens=total_tokens,
            history_budget=history_budget_val,
            input_budget=self._input_budget,
            skill_budget=skill_budget,
            knowledge_budget=knowledge_budget,
            memory_budget=memory_budget,
            archive_budget=archive_budget,
            skill_truncated=skill_truncated,
            knowledge_truncated=kb_truncated,
            memory_truncated=mem_truncated,
            archive_truncated=arc_truncated,
            history_truncated=history_truncated,
            tools_token_reserve=self._tools_token_reserve,
            tool_results_compacted=tool_compacted_count,
            session_summary_tokens=session_summary_tokens,
        )

        env_count = 1 if env_msg else 0
        skill_count = len(skill_msgs)
        inject_count = len(kb_msgs) + len(mem_msgs) + len(arc_msgs)

        logger.debug(
            "ContextBuilder.build | system={} env={} skill={} inject={} history={} total={} | tokens={} budget={}",
            len(system_msgs), env_count, skill_count, inject_count, len(history_msgs), len(result),
            self._last_build_stats.total_tokens, history_budget_val,
        )
        return result


@dataclass
class CompressionEstimate:
    """压缩估算结果，由 estimate_compression_needed() 返回。"""

    history_tokens: int  # 当前 History Zone 的 token 数
    history_budget: int  # History Zone 的动态预算
    target_tokens: int  # 压缩后的目标 token 数
