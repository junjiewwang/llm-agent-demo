"""Agent 业务服务层。

从 web_ui.py 的 AgentApp 中提取的纯业务逻辑，不依赖任何 UI 框架（Gradio/FastAPI）。
供 FastAPI API 层和 CLI 调用。

职责：
- 共享组件初始化（懒加载）
- 多租户会话管理（创建/恢复/持久化）
- 对话 CRUD（新建/切换/删除）
- 聊天执行（通过生成器 yield AgentEvent）
- 知识库管理（上传/清空）
- 系统状态查询

注意：
    _tenants 字典是进程级内存缓存，SessionStore（JSON 文件）才是真正的数据源。
    当前为单进程架构，若未来需要多实例部署，需将 _tenants 替换为分布式缓存（如 Redis）。
"""

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Union

from src.agent.events import AgentEvent, AgentStoppedError, EventType
from src.commands import CommandContext, CommandRegistry
from src.config import settings
from src.factory import (
    SharedComponents,
    TenantSession,
    Conversation,
    create_shared_components,
    create_tenant_session,
    create_conversation,
    restore_conversation,
    create_command_registry,
)
from src.observability.instruments import start_thread_with_context
from src.persistence import SessionStore
from src.utils.logger import logger


@dataclass
class ChatResult:
    """聊天最终结果，在所有 AgentEvent 之后返回。"""

    content: str
    """Agent 最终回答文本（如果被停止则为空字符串）。"""

    stopped: bool = False
    """是否被用户主动停止。"""

    error: Optional[str] = None
    """执行错误信息（如果有）。"""

    usage: Optional[Dict] = None
    """本次回答的 token 用量摘要（message_usage_enabled 开启时填充）。"""


# chat() 生成器 yield 的联合类型：过程中 yield AgentEvent，最终 yield ChatResult
ChatYield = Union[AgentEvent, ChatResult]


class AgentService:
    """Agent 核心业务服务（无 UI 依赖）。

    线程安全说明：
        - _tenants 通过 GIL 保证基本的读写安全
        - _stop_events 同上
        - SessionStore 内部有 threading.Lock 保证写操作安全
    """

    def __init__(self):
        self._shared: Optional[SharedComponents] = None
        self._initialized = False
        # 进程级内存缓存：tenant_id -> TenantSession
        # 注意：这不是数据源，SessionStore（JSON 文件）才是。多实例部署时需替换为分布式缓存。
        self._tenants: Dict[str, TenantSession] = {}
        self._session_store = SessionStore()
        self._stop_events: Dict[str, threading.Event] = {}
        # 工具执行确认机制：confirm_id → (Event, approved)
        self._confirm_events: Dict[str, threading.Event] = {}
        self._confirm_results: Dict[str, bool] = {}
        # 系统命令注册器
        self._command_registry: Optional[CommandRegistry] = None

    # ── 初始化 ──

    def ensure_initialized(self) -> None:
        """确保共享组件已初始化。

        Raises:
            ValueError: LLM API Key 未配置时抛出。
        """
        if self._initialized:
            return
        self._shared = create_shared_components()
        self._command_registry = create_command_registry()
        self._initialized = True

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def shared(self) -> Optional[SharedComponents]:
        return self._shared

    # ── 租户管理 ──

    def _get_or_create_tenant(self, tenant_id: str) -> TenantSession:
        """获取或创建租户会话（内存缓存层）。"""
        if tenant_id not in self._tenants:
            self._tenants[tenant_id] = create_tenant_session(tenant_id)
        return self._tenants[tenant_id]

    def _save_tenant(self, tenant_id: str) -> None:
        """将租户会话持久化到磁盘。"""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return

        conversations = {}
        for conv_id, conv in tenant.conversations.items():
            conversations[conv_id] = {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "chat_history": conv.chat_history,
                "memory_messages": conv.memory.serialize()["messages"],
                "system_prompt_count": conv.memory.serialize()["system_prompt_count"],
            }

        self._session_store.save_tenant(
            tenant_id=tenant_id,
            active_conv_id=tenant.active_conv_id,
            conversations=conversations,
        )

    def _try_restore_tenant(self, tenant_id: str) -> bool:
        """尝试从磁盘恢复租户会话。

        Returns:
            True 表示成功恢复，False 表示无持久化数据或恢复失败。
        """
        data = self._session_store.load_tenant(tenant_id)
        if not data:
            return False

        try:
            tenant = self._get_or_create_tenant(tenant_id)
            conv_data_map = data.get("conversations", {})

            for conv_id, conv_data in conv_data_map.items():
                restore_conversation(self._shared, tenant, conv_data)

            active_id = data.get("active_conv_id")
            if active_id and active_id in tenant.conversations:
                tenant.active_conv_id = active_id
            elif tenant.conversations:
                latest = max(tenant.conversations.values(), key=lambda c: c.created_at)
                tenant.active_conv_id = latest.id

            logger.info(
                "租户会话恢复成功 | tenant={} | convs={}",
                tenant_id[:8], len(tenant.conversations),
            )
            return True
        except Exception as e:
            logger.error("租户会话恢复失败 | tenant={} | err={}", tenant_id[:8], e)
            return False

    def _ensure_active_conversation(self, tenant: TenantSession) -> Conversation:
        """确保租户有一个活跃对话，没有则自动创建。"""
        conv = tenant.get_active_conversation()
        if not conv:
            conv = create_conversation(self._shared, tenant)
        return conv

    # ── 会话恢复 ──

    def restore_session(self, tenant_id: str) -> dict:
        """页面加载/刷新时恢复会话。

        Returns:
            {
                "chat_history": List[dict],
                "conversations": List[dict],  # [{id, title, active}, ...]
                "status": dict,
            }
        """
        self.ensure_initialized()

        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.conversations:
            if not self._try_restore_tenant(tenant_id):
                return {
                    "chat_history": [],
                    "conversations": [],
                    "status": self.get_status(tenant_id),
                }
            tenant = self._tenants.get(tenant_id)

        conv = tenant.get_active_conversation() if tenant else None
        history = conv.chat_history if conv else []

        return {
            "chat_history": history,
            "conversations": self.get_conversation_list(tenant_id),
            "status": self.get_status(tenant_id),
        }

    # ── 对话管理 ──

    def get_conversation_list(self, tenant_id: str) -> List[dict]:
        """获取对话列表。

        Returns:
            [{id, title, active}, ...] 按创建时间倒序。
        """
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.conversations:
            return []
        return tenant.get_conversation_list()

    def new_conversation(self, tenant_id: str) -> dict:
        """新建对话。

        Returns:
            {"conversation": {id, title, active}, "conversations": [...], "status": dict}
        """
        self.ensure_initialized()

        tenant = self._get_or_create_tenant(tenant_id)
        conv = create_conversation(self._shared, tenant)
        self._save_tenant(tenant_id)

        return {
            "conversation": {"id": conv.id, "title": conv.title, "active": True},
            "conversations": self.get_conversation_list(tenant_id),
            "status": self.get_status(tenant_id),
        }

    def switch_conversation(self, tenant_id: str, conv_id: str) -> dict:
        """切换到指定对话。

        Returns:
            {"chat_history": [...], "conversations": [...], "status": dict}
        """
        tenant = self._get_or_create_tenant(tenant_id)
        if conv_id and conv_id in tenant.conversations:
            tenant.active_conv_id = conv_id
            conv = tenant.conversations[conv_id]
            self._save_tenant(tenant_id)
            return {
                "chat_history": conv.chat_history,
                "conversations": self.get_conversation_list(tenant_id),
                "status": self.get_status(tenant_id),
            }
        return {
            "chat_history": [],
            "conversations": self.get_conversation_list(tenant_id),
            "status": self.get_status(tenant_id),
        }

    def delete_conversation(self, tenant_id: str, conv_id: str) -> dict:
        """删除指定对话。

        Returns:
            {"chat_history": [...], "conversations": [...], "status": dict}
        """
        tenant = self._get_or_create_tenant(tenant_id)
        if conv_id and conv_id in tenant.conversations:
            del tenant.conversations[conv_id]
            if tenant.active_conv_id == conv_id:
                tenant.active_conv_id = None
                if tenant.conversations:
                    latest = max(tenant.conversations.values(), key=lambda c: c.created_at)
                    tenant.active_conv_id = latest.id

        conv = tenant.get_active_conversation()
        history = conv.chat_history if conv else []
        self._save_tenant(tenant_id)

        return {
            "chat_history": history,
            "conversations": self.get_conversation_list(tenant_id),
            "status": self.get_status(tenant_id),
        }

    # ── 聊天 ──

    def chat(self, tenant_id: str, message: str) -> Generator[ChatYield, None, None]:
        """处理用户消息，生成器模式 yield 事件流。

        Yields:
            AgentEvent — 思考过程事件（THINKING / TOOL_CALL / TOOL_RESULT / ANSWERING / ...）
            ChatResult — 最终结果（最后一个 yield）

        使用示例:
            for item in service.chat(tenant_id, message):
                if isinstance(item, AgentEvent):
                    # 推送 SSE 事件
                elif isinstance(item, ChatResult):
                    # 推送最终结果 + 更新对话列表
        """
        self.ensure_initialized()

        if not message.strip():
            yield ChatResult(content="", error="消息不能为空")
            return

        # ── 系统命令拦截 ──
        # 以 "/" 开头的消息在进入 Agent 之前短路处理，不消耗 LLM token
        if message.strip().startswith("/") and self._command_registry:
            tenant = self._get_or_create_tenant(tenant_id)
            conv = self._ensure_active_conversation(tenant)
            ctx = CommandContext(
                tenant_id=tenant_id,
                vector_store=tenant.vector_store,
                conversation=conv,
                knowledge_base=self._shared.knowledge_base if self._shared else None,
                shared=self._shared,
            )
            result = self._command_registry.dispatch(message.strip(), ctx)
            if result is not None:
                # 写入 chat_history（持久化），不写入 ConversationMemory（LLM 不可见）
                conv.chat_history.append({"role": "user", "content": message})
                conv.chat_history.append({"role": "assistant", "content": result})
                self._save_tenant(tenant_id)
                yield ChatResult(content=result)
                return

        tenant = self._get_or_create_tenant(tenant_id)
        conv = self._ensure_active_conversation(tenant)

        # 首条消息自动设置对话标题
        if conv.title == "新对话" and message.strip():
            conv.title = message.strip()[:20]

        # 记录用户消息到 chat_history
        conv.chat_history.append({"role": "user", "content": message})

        # 初始化停止信号
        stop_event = threading.Event()
        self._stop_events[tenant_id] = stop_event

        # 通过 Queue 在 Agent 子线程和主生成器之间传递事件
        event_queue: queue.Queue = queue.Queue()
        result_holder: List = [None, None]  # [response, error]
        _SENTINEL = object()

        def on_event(event: AgentEvent):
            if stop_event.is_set():
                raise AgentStoppedError("用户停止了对话")
            event_queue.put(event)

        def run_agent():
            try:
                result_holder[0] = conv.agent.run(
                    message,
                    on_event=on_event,
                    wait_for_confirmation=lambda cid: self._wait_for_confirmation(
                        cid, stop_event, timeout=300,
                    ),
                )
            except AgentStoppedError:
                result_holder[1] = AgentStoppedError("用户停止了对话")
            except Exception as e:
                result_holder[1] = e
            event_queue.put(_SENTINEL)

        # 使用 start_thread_with_context 自动传播 OTel Context (L2→L3)
        thread = start_thread_with_context(run_agent, daemon=True, name="agent-run")

        # 实时 yield 事件
        stopped = False
        while True:
            try:
                event = event_queue.get(timeout=0.1)
            except queue.Empty:
                if stop_event.is_set():
                    stopped = True
                    break
                continue

            if event is _SENTINEL:
                break

            yield event

        thread.join(timeout=5)
        self._stop_events.pop(tenant_id, None)

        # 构造最终结果
        usage = None
        if settings.agent.message_usage_enabled:
            metrics = getattr(conv.agent, 'last_metrics', None)
            if metrics:
                usage = metrics.usage_summary()

        if stopped or isinstance(result_holder[1], AgentStoppedError):
            logger.info("对话已被用户停止 | tenant={}", tenant_id[:8])
            result = ChatResult(content="", stopped=True, usage=usage)
        elif result_holder[1]:
            logger.error("Agent 执行失败: {}", result_holder[1])
            result = ChatResult(content="", error=str(result_holder[1]), usage=usage)
        else:
            result = ChatResult(content=result_holder[0] or "", usage=usage)

        # 将 Agent 回答写入 chat_history 并持久化（含 usage）
        def _make_entry(content: str) -> dict:
            entry: dict = {"role": "assistant", "content": content}
            if result.usage:
                entry["usage"] = result.usage
            return entry

        if result.content:
            conv.chat_history.append(_make_entry(result.content))
        elif result.stopped:
            conv.chat_history.append(_make_entry("[对话已停止]"))
        elif result.error:
            conv.chat_history.append(_make_entry(f"[错误] {result.error}"))

        conv.chat_history = conv.chat_history
        self._save_tenant(tenant_id)
        yield result

    def stop_chat(self, tenant_id: str) -> bool:
        """停止当前正在进行的对话。

        Returns:
            True 表示成功发送停止信号，False 表示没有正在进行的对话。
        """
        stop_event = self._stop_events.get(tenant_id)
        if stop_event:
            stop_event.set()
            logger.info("停止信号已发送 | tenant={}", tenant_id[:8])
            return True
        return False

    # ── 工具执行确认 ──

    def confirm_tool(self, confirm_id: str, approved: bool) -> bool:
        """用户对工具执行做出确认决策。

        Args:
            confirm_id: 确认请求的唯一标识（由 TOOL_CONFIRM 事件携带）。
            approved: True=批准执行，False=拒绝执行。

        Returns:
            True 表示确认成功（找到对应的等待事件），False 表示不存在或已过期。
        """
        event = self._confirm_events.get(confirm_id)
        if not event:
            return False
        self._confirm_results[confirm_id] = approved
        event.set()
        logger.info("工具确认已处理 | confirm_id={} | approved={}", confirm_id[:8], approved)
        return True

    def _wait_for_confirmation(
        self, confirm_id: str, stop_event: threading.Event, timeout: float = 300,
    ) -> Optional[bool]:
        """Agent 线程调用：阻塞等待用户确认。

        同时监听 stop_event，用户点击"停止"时提前退出。

        Args:
            confirm_id: 确认请求唯一标识。
            stop_event: 当前对话的停止信号。
            timeout: 最大等待时间（秒）。

        Returns:
            True=批准，False=拒绝，None=超时或被停止。
        """
        event = threading.Event()
        self._confirm_events[confirm_id] = event
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                # 每 0.5s 检查一次确认信号和停止信号
                if event.wait(timeout=0.5):
                    return self._confirm_results.pop(confirm_id, None)
                if stop_event.is_set():
                    logger.info("确认等待被停止信号中断 | confirm_id={}", confirm_id[:8])
                    return None
            logger.warning("确认等待超时 | confirm_id={}", confirm_id[:8])
            return None
        finally:
            self._confirm_events.pop(confirm_id, None)
            self._confirm_results.pop(confirm_id, None)

    def is_chatting(self, tenant_id: str) -> bool:
        """检查指定租户是否正在聊天中。"""
        return tenant_id in self._stop_events

    # ── 知识库 ──

    def upload_files(self, file_paths: List[str]) -> dict:
        """上传文件到知识库。

        Args:
            file_paths: 文件路径列表。

        Returns:
            {"results": [{"file": str, "chunks": int, "error": str?}, ...], "total_chunks": int}
        """
        kb = self._shared.knowledge_base if self._shared else None
        if not kb:
            return {"results": [], "total_chunks": 0, "error": "知识库未初始化"}

        results = []
        for file_path in file_paths:
            try:
                chunks = kb.import_file(file_path)
                results.append({
                    "file": os.path.basename(file_path),
                    "chunks": chunks,
                })
            except Exception as e:
                results.append({
                    "file": os.path.basename(file_path),
                    "chunks": 0,
                    "error": str(e),
                })

        return {"results": results, "total_chunks": kb.count()}

    def clear_knowledge_base(self) -> bool:
        """清空知识库。

        Returns:
            True 表示成功，False 表示知识库未初始化。
        """
        kb = self._shared.knowledge_base if self._shared else None
        if kb:
            kb.clear()
            return True
        return False

    # ── 状态 ──

    def get_status(self, tenant_id: str) -> dict:
        """获取系统状态。

        Returns:
            {
                "initialized": bool,
                "model": str?,
                "current_conversation": {id, title, memory_tokens}?,
                "conversation_count": int,
                "long_term_memory_count": int,
                "knowledge_base_chunks": int,
            }
        """
        if not self._initialized or not self._shared:
            return {"initialized": False}

        tenant = self._tenants.get(tenant_id)
        status: dict = {
            "initialized": True,
            "model": self._shared.llm_client.model,
            "conversation_count": len(tenant.conversations) if tenant else 0,
            "long_term_memory_count": 0,
            "knowledge_base_chunks": 0,
        }

        if tenant:
            conv = tenant.get_active_conversation()
            if conv:
                status["current_conversation"] = {
                    "id": conv.id,
                    "title": conv.title,
                    "memory_tokens": conv.memory.token_count,
                }
            if tenant.vector_store:
                status["long_term_memory_count"] = tenant.vector_store.count()

        kb = self._shared.knowledge_base
        if kb:
            status["knowledge_base_chunks"] = kb.count()

        return status
