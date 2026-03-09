"""Agent 组件工厂模块。

提供分层的组件创建逻辑，支持多租户和多对话：

层级结构：
- SharedComponents（全局单例）：LLM Client、ToolRegistry、KnowledgeBase
- TenantSession（每个用户/浏览器标签页）：VectorStore（长期记忆）、多个 Conversation
- Conversation（每次新建对话）：ConversationMemory、ReActAgent
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from src.agent import ReActAgent
from src.agent.base_agent import BaseAgent
from src.agent.plan_execute_agent import PlanExecuteAgent
from src.context import ContextBuilder
from src.context.builder import default_environment, tool_environment
from src.environment.tool_env_adapter import ToolEnvAdapter
from src.llm import OpenAIClient
from src.memory import ConversationMemory, MemoryGovernor, VectorStore, ConversationArchive
from src.memory.session_summary import SessionSummary
from src.rag import KnowledgeBase
from src.skills import SkillRegistry, SkillRouter, load_from_directory
from src.tools import (
    ToolRegistry, CalculatorTool, DateTimeTool, WebSearchTool, KnowledgeSearchTool,
)
from src.tools.filesystem import Sandbox, FileReaderTool, FileWriterTool
from src.tools.devops import BashExecutor, ExecuteCommandTool
from src.tools.devops.policies import ALL_POLICIES, PIPE_TOOLS
from src.tools.mcp import MCPToolManager
from src.config import settings
from src.utils.logger import logger

SYSTEM_PROMPT = """你是一个智能助手，能够自主思考和使用工具来帮助用户解决各种问题。

重要原则：
- 你必须基于事实和已知信息回答，绝对不要编造或猜测你不确定的内容
- 如果上下文中包含 [知识库检索结果]，你必须优先基于这些内容回答
- 如果你不确定答案，请如实说明，不要胡编

你的工作方式：
1. 分析用户的问题，判断是否需要使用工具
2. 如果需要，选择合适的工具并调用
3. 根据工具返回的结果，继续思考或给出最终回答
4. 如果一个工具不够，可以连续调用多个工具
5. 如果上下文中已有知识库检索结果，优先基于它们回答；历史记忆仅供参考，涉及状态查询时仍须调用工具
6. 批量调用策略（重要，严格遵守）：
   - 当你已经获得了目标列表（如 namespace 列表、文件列表、容器列表），必须在一次回复中对所有目标同时发起工具调用，绝不逐个调用
   - 先收集，再批量：如果需要先查询有哪些目标，用一次调用获取列表，下一轮立即对所有目标批量操作
   - 每轮回复可以同时包含多个工具调用，系统会自动并发执行

效率原则（最高优先级，严格遵守）：
- 每次工具返回结果后，你必须先判断："已有信息是否足以回答用户的核心问题？"。如果够了，立即回答，禁止继续调用工具
- 只回答用户问的问题，不要主动扩展到用户没问的维度
- 如果一种方式已经拿到了数据，不要换另一种方式再查一遍同样的数据
- 绝大多数查询类问题应在 1-2 次工具调用内完成。超过 3 次时你必须有充分理由
- 反面案例（禁止模仿）：
  * 用户问"DNS地址是多少" → 拿到 ClusterIP 后还去查 Corefile、Pod 状态（错误：过度探索）
  * 用户问"容器IP是多少" → 拿到 IP 列表后还去查 docker network、k8s pods（错误：扩展到用户没问的维度）
  * 用同一个 inspect 命令换不同 format 重复查同一数据（错误：重复查询）

时效性原则（严格遵守）：
- 当用户请求"查看/查询/检查/获取"某个系统或服务的当前状态、数据、列表时，你必须调用工具获取最新数据，不得仅凭记忆或历史对话中的旧数据回答
- 即使上下文中已有相似的历史记忆，对于状态类、实时数据类查询仍必须重新调用工具刷新
- 历史记忆仅用于提供背景和辅助判断，不能替代实时查询

能力边界（严格遵守）：
- 你只能使用当前已注册的工具，不要尝试通过写脚本、读取二进制文件等间接方式来模拟不存在的工具功能
- 如果用户需要的功能没有对应的工具，直接告知用户当前不支持该操作，并建议管理员启用相关工具
- 不要写 shell 脚本试图间接执行命令，不要读取 /usr/bin 等系统目录下的二进制文件
- 如果连续 2 次工具调用都未能取得有效进展，应停止尝试并向用户说明情况
- 当工具执行失败时，必须如实告知用户失败原因（如权限不足、命令不被允许、参数错误等），不得隐瞒错误或回避工具报错，更不能臆测其他无关原因来代替真实原因。可以在说明失败原因后，再提供替代方案或建议

请用简洁、准确的语言回答问题。"""


# ── 数据模型 ──

@dataclass
class Conversation:
    """单个对话，拥有独立的短期记忆和 Agent。"""

    id: str
    title: str
    memory: ConversationMemory
    agent: BaseAgent  # ReActAgent 或 PlanExecuteAgent
    session_summary: Optional[SessionSummary] = None
    created_at: float = field(default_factory=time.time)
    chat_history: List[dict] = field(default_factory=list)


@dataclass
class SharedComponents:
    """全局共享组件（进程内单例）。"""

    llm_client: OpenAIClient
    tool_registry: ToolRegistry
    knowledge_base: Optional[KnowledgeBase] = None
    skill_router: Optional[SkillRouter] = None
    mcp_manager: Optional[MCPToolManager] = None


@dataclass
class TenantSession:
    """单个租户的会话，管理长期记忆和多个对话。"""

    tenant_id: str
    vector_store: Optional[VectorStore]
    conversation_archive: Optional[ConversationArchive] = None
    governor: Optional[MemoryGovernor] = None
    conversations: Dict[str, Conversation] = field(default_factory=dict)
    active_conv_id: Optional[str] = None

    def get_active_conversation(self) -> Optional[Conversation]:
        """获取当前活跃对话。"""
        if self.active_conv_id and self.active_conv_id in self.conversations:
            return self.conversations[self.active_conv_id]
        return None

    def get_conversation_list(self) -> List[dict]:
        """返回对话列表（按创建时间倒序），用于 UI 展示。"""
        convs = sorted(
            self.conversations.values(),
            key=lambda c: c.created_at,
            reverse=True,
        )
        return [
            {"id": c.id, "title": c.title, "active": c.id == self.active_conv_id, "created_at": c.created_at}
            for c in convs
        ]


# ── 旧的兼容接口（供 main.py CLI 使用） ──

@dataclass
class AgentComponents:
    """Agent 所有组件的容器（CLI 模式用，保持向后兼容）。"""

    llm_client: OpenAIClient
    memory: ConversationMemory
    tool_registry: ToolRegistry
    agent: ReActAgent
    vector_store: Optional[VectorStore] = None
    knowledge_base: Optional[KnowledgeBase] = None


# ── 工厂函数 ──

def create_tool_registry(
    knowledge_base: Optional[KnowledgeBase],
) -> tuple[ToolRegistry, Optional[MCPToolManager]]:
    """创建并注册所有可用工具（含 MCP 外部工具）。

    Returns:
        (ToolRegistry, MCPToolManager or None) 元组。
    """
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(DateTimeTool())
    registry.register(WebSearchTool())
    registry.register(KnowledgeSearchTool(knowledge_base=knowledge_base))

    # 文件系统工具：共享同一个 Sandbox 实例
    fs_config = settings.filesystem
    exclude = [p.strip() for p in fs_config.exclude.split(",") if p.strip()]
    allowed_dirs = [p.strip() for p in fs_config.allowed_dirs.split(",") if p.strip()] or None
    writable_dirs = [p.strip() for p in fs_config.writable_dirs.split(",") if p.strip()] or None
    sandbox = Sandbox(
        root=fs_config.sandbox_dir or None,
        allowed_dirs=allowed_dirs,
        writable_dirs=writable_dirs,
        exclude_patterns=exclude or None,
        max_file_size=fs_config.max_file_size,
        max_depth=fs_config.max_depth,
        max_results=fs_config.max_results,
    )
    registry.register(FileReaderTool(sandbox))
    registry.register(FileWriterTool(sandbox))

    # 标准化别名：使 Skill 可以用通用名称引用工具
    registry.register_alias("fs_read", "file_reader")
    registry.register_alias("fs_write", "file_writer")

    # DevOps 工具：按配置按需注册
    _register_devops_tools(registry)

    # MCP 外部工具：从 .mcp.json 发现并注册
    mcp_manager = _register_mcp_tools(registry)

    return registry, mcp_manager


def _register_devops_tools(registry: ToolRegistry) -> None:
    """按配置注册统一命令执行工具（execute_command）。

    根据 COMMAND_ALLOWED_BINARIES 配置，从 ALL_POLICIES 中筛选
    对应的 BinaryPolicy，构建统一 BashExecutor 并注册单一工具。
    """
    cmd_config = settings.command

    if not cmd_config.enabled:
        return

    # 解析允许的二进制列表
    allowed = [b.strip() for b in cmd_config.allowed_binaries.split(",") if b.strip()]
    if not allowed:
        logger.warning("COMMAND_ENABLED=true 但 COMMAND_ALLOWED_BINARIES 为空，跳过注册")
        return

    # 从预置策略集中筛选
    policies = {}
    for binary in allowed:
        if binary in ALL_POLICIES:
            policies[binary] = ALL_POLICIES[binary]
        else:
            logger.warning("二进制 '{}' 没有预置安全策略，跳过", binary)

    if not policies:
        logger.warning("没有有效的二进制策略，跳过注册")
        return

    # 解析特殊配置
    ns_whitelist = None
    if "kubectl" in policies and cmd_config.kubectl_allowed_namespaces:
        ns_whitelist = frozenset(
            ns.strip()
            for ns in cmd_config.kubectl_allowed_namespaces.split(",")
            if ns.strip()
        )

    curl_allowed_hosts = None
    if "curl" in policies and cmd_config.curl_allowed_hosts:
        curl_allowed_hosts = frozenset(
            h.strip()
            for h in cmd_config.curl_allowed_hosts.split(",")
            if h.strip()
        )

    # 构建统一执行器
    executor = BashExecutor(
        policies=policies,
        pipe_tools=PIPE_TOOLS,
        timeout=cmd_config.timeout,
        max_output_chars=cmd_config.max_output_chars,
        namespace_whitelist=ns_whitelist,
        curl_allowed_hosts=curl_allowed_hosts,
    )

    # 注册统一工具
    registry.register(
        ExecuteCommandTool(executor=executor, allowed_binaries=list(policies.keys()))
    )
    logger.info(
        "execute_command 工具已注册 | 允许的二进制: {} | namespace限制={} | host限制={}",
        sorted(policies.keys()),
        sorted(ns_whitelist) if ns_whitelist else "无",
        sorted(curl_allowed_hosts) if curl_allowed_hosts else "无",
    )


def _register_mcp_tools(registry: ToolRegistry) -> Optional[MCPToolManager]:
    """从 .mcp.json 发现 MCP 外部工具并注册到 ToolRegistry。

    单个 Server 连接失败不阻塞其他 Server 和整体启动流程。

    Returns:
        MCPToolManager 实例（用于 shutdown），无配置时返回 None。
    """
    manager = MCPToolManager(config_path=".mcp.json")
    try:
        count = manager.discover_and_register(registry)
        if count > 0:
            return manager
    except Exception as e:
        logger.warning("MCP 工具发现失败（不影响内置工具）: {}", e)

    # 没有注册任何工具时，清理资源
    try:
        manager.shutdown()
    except Exception:
        pass
    return None


def create_skill_router(tool_registry: ToolRegistry) -> SkillRouter:
    """创建 Skill 路由器，从配置目录自动发现并加载所有 SKILL.md。

    扫描 SKILLS_DIRS 配置的目录（逗号分隔，支持多目录），
    自动加载所有包含 SKILL.md 的子目录为 Skill，并过滤 SKILLS_DISABLED 中指定的 Skill。

    目录结构：
        skills/
        ├── k8s-troubleshooting/
        │   └── SKILL.md
        └── k8s-resource-analysis/
            └── SKILL.md

    Args:
        tool_registry: 工具注册中心，用于校验 Skill 依赖的工具是否可用。

    Returns:
        配置好的 SkillRouter 实例。
    """
    from pathlib import Path

    skills_config = settings.skills
    registry = SkillRegistry()

    # 解析配置：扫描目录和禁用列表
    scan_dirs = [d.strip() for d in skills_config.dirs.split(",") if d.strip()]
    disabled = {s.strip() for s in skills_config.disabled.split(",") if s.strip()}

    if disabled:
        logger.info("Skills 禁用列表: {}", disabled)

    # 从每个目录自动发现并加载 SKILL.md
    total_loaded = 0
    for dir_path in scan_dirs:
        path = Path(dir_path)
        skills = load_from_directory(path, disabled_skills=disabled)
        for skill in skills:
            try:
                registry.register(skill)
                total_loaded += 1
            except ValueError as e:
                logger.warning("Skill 注册失败: {}", e)

    # 校验 Skill 依赖的工具
    warnings = registry.validate_tools(tool_registry.tool_names)
    for w in warnings:
        logger.warning(w)

    logger.info(
        "Skills 系统初始化完成 | 扫描目录: {} | 已加载: {} 个 | 已注册: {}",
        scan_dirs, total_loaded, registry.skill_names,
    )
    return SkillRouter(registry)


def _log_feature_flags() -> None:
    """输出 Agent Feature Flags 状态摘要，便于启动时确认功能开关。"""
    cfg = settings.agent

    def _flag(val: bool) -> str:
        return "ON" if val else "OFF"

    lines = [
        "Feature Flags 状态:",
        f"  message_usage    = {_flag(cfg.message_usage_enabled)}",
        f"  plan_execute     = {_flag(cfg.plan_execute_enabled)}",
        f"  policy           = {_flag(cfg.policy_enabled)}",
        f"  memory_governor  = {_flag(cfg.memory_governor_enabled)}"
        f"  (interval={cfg.memory_governor_interval}s,"
        f" ttl={cfg.memory_default_ttl_days}d,"
        f" min_score={cfg.memory_min_value_score},"
        f" merge_threshold={cfg.memory_merge_threshold})",
        f"  env_adapter      = {_flag(cfg.env_adapter_enabled)}",
    ]
    logger.info("\n".join(lines))


def create_shared_components() -> SharedComponents:
    """创建全局共享组件。

    Raises:
        ValueError: LLM API Key 未配置时抛出。
    """
    llm_client = OpenAIClient()

    knowledge_base: Optional[KnowledgeBase] = None
    try:
        knowledge_base = KnowledgeBase()
    except Exception as e:
        logger.warning("知识库初始化失败: {}", e)

    tool_registry, mcp_manager = create_tool_registry(knowledge_base)
    skill_router = create_skill_router(tool_registry)

    _log_feature_flags()

    return SharedComponents(
        llm_client=llm_client,
        tool_registry=tool_registry,
        knowledge_base=knowledge_base,
        skill_router=skill_router,
        mcp_manager=mcp_manager,
    )


def create_tenant_session(tenant_id: str) -> TenantSession:
    """为租户创建会话（包含独立的长期记忆、对话归档和可选的 Governor）。"""
    # ChromaDB collection name 要求 3-63 字符，[a-zA-Z0-9._-]，不能以 _ 结尾
    safe_id = tenant_id[:16] if tenant_id else uuid.uuid4().hex[:16]
    vector_store: Optional[VectorStore] = None
    governor: Optional[MemoryGovernor] = None
    conversation_archive: Optional[ConversationArchive] = None

    try:
        vector_store = VectorStore(
            collection_name=f"mem-{safe_id}",
            persist_directory=f".agent_data/memory/{safe_id}",
            default_ttl_days=settings.agent.memory_default_ttl_days,
        )
    except Exception as e:
        logger.warning("租户 {} 长期记忆初始化失败: {}", safe_id[:8], e)

    # ConversationArchive: 对话历史的向量化归档存储
    try:
        conversation_archive = ConversationArchive(
            collection_name=f"archive-{safe_id}",
            persist_directory=f".agent_data/archive/{safe_id}",
        )
    except Exception as e:
        logger.warning("租户 {} 对话归档初始化失败: {}", safe_id[:8], e)

    # Governor: Feature Flag 开启时创建并启动后台治理
    if vector_store and settings.agent.memory_governor_enabled:
        governor = MemoryGovernor(vector_store)
        governor.start_background()
        logger.info("租户 {} Memory Governor 已启动", safe_id[:8])

    return TenantSession(
        tenant_id=tenant_id,
        vector_store=vector_store,
        conversation_archive=conversation_archive,
        governor=governor,
    )


def _create_agent(
    shared: SharedComponents,
    tenant: TenantSession,
    memory: ConversationMemory,
    context_builder: ContextBuilder,
    session_summary: Optional[SessionSummary] = None,
    env_adapter=None,
) -> BaseAgent:
    """根据 feature flag 创建 ReActAgent 或 PlanExecuteAgent。

    共享相同的构造参数，唯一差异是 Agent 类型。
    plan_execute_enabled=True 时创建 PlanExecuteAgent，否则创建 ReActAgent。
    """
    common_kwargs = dict(
        llm_client=shared.llm_client,
        tool_registry=shared.tool_registry,
        memory=memory,
        context_builder=context_builder,
        vector_store=tenant.vector_store,
        conversation_archive=tenant.conversation_archive,
        session_summary=session_summary,
        knowledge_base=shared.knowledge_base,
        skill_router=shared.skill_router,
        env_adapter=env_adapter,
    )

    if settings.agent.plan_execute_enabled:
        logger.debug("创建 PlanExecuteAgent")
        return PlanExecuteAgent(**common_kwargs)

    return ReActAgent(**common_kwargs)


def create_conversation(
    shared: SharedComponents,
    tenant: TenantSession,
    title: str = "新对话",
    max_memory_tokens: int = 8000,
) -> Conversation:
    """在租户会话内创建一个新的对话。"""
    conv_id = uuid.uuid4().hex[:12]

    memory = ConversationMemory(
        system_prompt=SYSTEM_PROMPT,
        max_tokens=max_memory_tokens,
        model=shared.llm_client.model,
    )
    memory.set_llm_client(shared.llm_client)

    # ContextBuilder 负责 Zone 分层上下文组装（KB/记忆临时注入，不污染对话历史）
    context_builder = ContextBuilder(
        environment_providers=[default_environment, tool_environment(shared.tool_registry)],
        model=shared.llm_client.model,
    )

    # Environment Adapter: Feature Flag 开启时包装 ToolRegistry
    env_adapter = None
    if settings.agent.env_adapter_enabled:
        env_adapter = ToolEnvAdapter(shared.tool_registry)

    # Session Summary: 会话级增量摘要管理器
    session_summary = SessionSummary()

    agent = _create_agent(
        shared=shared,
        tenant=tenant,
        memory=memory,
        context_builder=context_builder,
        session_summary=session_summary,
        env_adapter=env_adapter,
    )

    conv = Conversation(
        id=conv_id,
        title=title,
        memory=memory,
        agent=agent,
        session_summary=session_summary,
    )

    tenant.conversations[conv_id] = conv
    tenant.active_conv_id = conv_id
    logger.info("新建对话 {} (租户 {})", conv_id, tenant.tenant_id[:8])
    return conv


def restore_conversation(
    shared: SharedComponents,
    tenant: TenantSession,
    conv_data: dict,
    max_memory_tokens: int = 8000,
) -> Conversation:
    """从持久化数据恢复一个对话。

    Args:
        shared: 全局共享组件。
        tenant: 所属租户会话。
        conv_data: 序列化的对话数据，包含：
            id, title, created_at, chat_history, memory_messages, system_prompt_count
    """
    conv_id = conv_data["id"]

    # 重建 ConversationMemory：先创建空实例，再从持久化数据恢复
    memory = ConversationMemory(
        system_prompt=None,  # 不设 system prompt，由 restore_from 恢复
        max_tokens=max_memory_tokens,
        model=shared.llm_client.model,
    )
    memory.set_llm_client(shared.llm_client)

    # 恢复消息记录
    memory_data = {
        "messages": conv_data.get("memory_messages", []),
        "system_prompt_count": conv_data.get("system_prompt_count", 0),
    }
    memory.restore_from(memory_data)

    # 将 system prompt 更新为最新版本，确保新规则（如时效性原则）对旧对话也生效
    memory.update_system_prompt(SYSTEM_PROMPT)

    context_builder = ContextBuilder(
        environment_providers=[default_environment, tool_environment(shared.tool_registry)],
        model=shared.llm_client.model,
    )

    env_adapter = None
    if settings.agent.env_adapter_enabled:
        env_adapter = ToolEnvAdapter(shared.tool_registry)

    # 恢复 SessionSummary
    session_summary = SessionSummary()
    summary_data = conv_data.get("session_summary")
    if summary_data:
        session_summary.restore_from(summary_data)

    agent = _create_agent(
        shared=shared,
        tenant=tenant,
        memory=memory,
        context_builder=context_builder,
        session_summary=session_summary,
        env_adapter=env_adapter,
    )

    conv = Conversation(
        id=conv_id,
        title=conv_data.get("title", "恢复的对话"),
        memory=memory,
        agent=agent,
        session_summary=session_summary,
        created_at=conv_data.get("created_at", time.time()),
        chat_history=conv_data.get("chat_history", []),
    )

    tenant.conversations[conv_id] = conv
    logger.info("恢复对话 {} (租户 {})", conv_id, tenant.tenant_id[:8])
    return conv


def create_command_registry():
    """创建系统命令注册器，注册所有可用命令。"""
    from src.commands import CommandRegistry
    from src.commands.memory_cmd import MemoryCommand
    from src.commands.context_cmd import ContextCommand
    from src.commands.status_cmd import StatusCommand
    from src.commands.help_cmd import HelpCommand

    registry = CommandRegistry()
    registry.register(MemoryCommand())
    registry.register(ContextCommand())
    registry.register(StatusCommand())
    # HelpCommand 需要引用 registry 来展示所有命令
    registry.register(HelpCommand(registry))
    return registry


def create_agent(max_memory_tokens: int = 8000) -> AgentComponents:
    """创建完整的 Agent（CLI 兼容接口，单租户单对话模式）。

    Raises:
        ValueError: LLM API Key 未配置时抛出。
    """
    shared = create_shared_components()
    tenant = create_tenant_session("cli_default")
    conv = create_conversation(shared, tenant, title="CLI 对话", max_memory_tokens=max_memory_tokens)

    return AgentComponents(
        llm_client=shared.llm_client,
        memory=conv.memory,
        tool_registry=shared.tool_registry,
        agent=conv.agent,
        vector_store=tenant.vector_store,
        knowledge_base=shared.knowledge_base,
    )
