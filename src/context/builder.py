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
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from src.llm.base_client import Message, Role
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
    ):
        """
        Args:
            environment_providers: 环境信息提供者列表。每个提供者返回 Dict[str, str]，
                所有结果合并后作为 Environment Zone 内容。
                默认包含 default_environment（当前时间）。
        """
        self._environment_providers: List[EnvironmentProvider] = (
            environment_providers if environment_providers is not None
            else [default_environment]
        )
        self._skill_messages: List[Message] = []
        self._knowledge_messages: List[Message] = []
        self._memory_messages: List[Message] = []

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

    def clear_injections(self) -> "ContextBuilder":
        """清除所有临时注入（Skills + KB + 长期记忆），用于新一轮对话。"""
        self._skill_messages = []
        self._knowledge_messages = []
        self._memory_messages = []
        return self

    def _build_environment_message(self) -> Optional[Message]:
        """收集所有环境信息提供者的数据，构建 Environment Zone 消息。

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
        sections.extend(block_parts)

        return Message(
            role=Role.SYSTEM,
            content="\n\n".join(sections),
        )

    def build(self, conversation_messages: List[Message]) -> List[Message]:
        """组装完整的 LLM 请求上下文。

        Zone 顺序：System → Environment → Skill → Inject(KB + Memory) → History(对话历史)

        Args:
            conversation_messages: ConversationMemory 中的消息列表（含 system prompt）。

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

        # Zone 组装：System → Environment → Skill → KB → Memory → History
        result = []
        result.extend(system_msgs)                   # System Zone（稳定前缀）

        env_msg = self._build_environment_message()   # Environment Zone
        if env_msg:
            result.append(env_msg)

        result.extend(self._skill_messages)           # Skill Zone（领域专家）
        result.extend(self._knowledge_messages)       # Inject Zone - KB
        result.extend(self._memory_messages)          # Inject Zone - Memory
        result.extend(history_msgs)                   # History Zone（动态）

        skill_count = len(self._skill_messages)
        inject_count = len(self._knowledge_messages) + len(self._memory_messages)
        env_count = 1 if env_msg else 0
        logger.debug(
            "ContextBuilder.build | system={} env={} skill={} inject={} history={} total={}",
            len(system_msgs), env_count, skill_count, inject_count, len(history_msgs), len(result),
        )
        return result
