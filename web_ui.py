"""LLM Agent Web UI - 基于 Gradio 的交互界面。

提供美观的 Web 界面，支持：
- 多租户隔离（每个浏览器标签页 = 独立租户，刷新后可恢复）
- 多对话管理（新建/切换/删除对话，避免幻觉累积）
- 文件上传导入知识库
- 记忆和知识库状态查看
"""

import json
import os
import queue
import threading
import time
import uuid
from typing import Optional, List, Dict

import gradio as gr

from src.agent.events import AgentEvent, AgentStoppedError, EventType
from src.factory import (
    SharedComponents,
    TenantSession,
    Conversation,
    create_shared_components,
    create_tenant_session,
    create_conversation,
    restore_conversation,
)
from src.persistence import SessionStore
from src.utils.logger import logger

# JS: 从 sessionStorage 读取或生成 tenant_id（每个浏览器标签页独立），刷新后保持不变。
# Gradio 6.x: js 回调需要返回一个数组，对应 outputs。
_JS_LOAD_TENANT_ID = """
(..._args) => {
    const makeHex = (len) => {
        const bytesLen = Math.ceil(len / 2);
        let bytes;
        try {
            if (globalThis.crypto && globalThis.crypto.getRandomValues) {
                bytes = new Uint8Array(bytesLen);
                globalThis.crypto.getRandomValues(bytes);
            }
        } catch (e) {
            // ignore, fallback below
        }
        if (!bytes) {
            bytes = new Uint8Array(bytesLen);
            for (let i = 0; i < bytesLen; i++) {
                bytes[i] = Math.floor(Math.random() * 256);
            }
        }
        return Array.from(bytes)
            .map((b) => b.toString(16).padStart(2, '0'))
            .join('')
            .slice(0, len);
    };

    let tid = '';
    let err = '';
    let persisted = true;

    try {
        tid = sessionStorage.getItem('agent_tenant_id') || '';
        if (!tid) {
            // 优先 randomUUID（部分浏览器/非安全上下文可能不可用）
            if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
                tid = globalThis.crypto.randomUUID().replace(/-/g, '');
            } else {
                tid = makeHex(32);
            }
            sessionStorage.setItem('agent_tenant_id', tid);
        }
    } catch (e) {
        err = String(e);
        persisted = false;
        // 即使 storage 不可用，也确保生成一个临时 id 让页面可用
        if (!tid) {
            tid = makeHex(32);
        }
    }

    const meta = JSON.stringify({ persisted, err });
    // 同时更新隐藏 tenant_id(Textbox) 和 tenant_meta(Textbox)
    return [tid, meta];
}
"""


class AgentApp:
    """Agent 应用，管理共享组件和多租户会话。"""

    def __init__(self):
        self._shared: Optional[SharedComponents] = None
        self._tenants: Dict[str, TenantSession] = {}
        self._initialized = False
        self._tenant_warnings: Dict[str, str] = {}
        self._last_restore_ts: Dict[str, float] = {}
        self._session_store = SessionStore()
        self._stop_events: Dict[str, threading.Event] = {}  # per-tenant 停止信号

    def _ensure_initialized(self) -> Optional[str]:
        """确保共享组件已初始化。返回 None 表示成功，否则返回错误信息。"""
        if self._initialized:
            return None
        try:
            self._shared = create_shared_components()
            self._initialized = True
            return None
        except ValueError as e:
            return f"❌ {e}\n请在 .env 文件中配置 LLM_API_KEY"

    def _get_or_create_tenant(self, tenant_id: str) -> TenantSession:
        """获取或创建租户会话。"""
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

            # 恢复活跃对话 ID
            active_id = data.get("active_conv_id")
            if active_id and active_id in tenant.conversations:
                tenant.active_conv_id = active_id
            elif tenant.conversations:
                # 活跃对话不存在时，选最新的
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

    def restore_session(self, tenant_id: str, tenant_meta: str = ""):
        """页面加载/刷新时恢复会话。

        返回 (tenant_id, chat_history, conv_list_update, status)。
        """
        # 幂等/去重：同一 tenant_id 在很短时间内多次触发（Gradio 前端渲染导致）时直接复用
        now = time.monotonic()
        last_ts = self._last_restore_ts.get(tenant_id)
        if last_ts is not None and (now - last_ts) < 0.8:
            tenant = self._tenants.get(tenant_id)
            conv = tenant.get_active_conversation() if tenant else None
            history = conv.chat_history if conv else []
            conv_update = self._build_conv_choices(tenant_id)
            status = self._get_status(tenant_id)
            return tenant_id, history, conv_update, status
        self._last_restore_ts[tenant_id] = now

        # 解析前端 meta，用于提示“是否能持久化”
        warning = ""
        if tenant_meta:
            try:
                meta = json.loads(tenant_meta)
                persisted = bool(meta.get("persisted", True))
                err = str(meta.get("err", "") or "")
                if not persisted:
                    warning = "⚠️ 浏览器存储不可用：本次会话为临时会话，刷新可能丢失。"
                    if err:
                        warning += f"\n原因：{err}"
            except Exception as e:
                warning = f"⚠️ 会话元信息解析失败：{e}"

        if warning:
            self._tenant_warnings[tenant_id] = warning
        else:
            self._tenant_warnings.pop(tenant_id, None)

        logger.info(
            "restore_session | tenant_id={} (short={}) | tenants={} | warning={} ",
            tenant_id,
            tenant_id[:8] if tenant_id else "(空)",
            list(self._tenants.keys()),
            bool(warning),
        )
        err = self._ensure_initialized()
        if err:
            return tenant_id, [], gr.update(choices=[], value=None), err

        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.conversations:
            # 尝试从磁盘恢复
            if self._try_restore_tenant(tenant_id):
                tenant = self._tenants.get(tenant_id)
            else:
                # 无持久化数据，返回空白状态
                return tenant_id, [], gr.update(choices=[], value=None), self._get_status(tenant_id)

        # 恢复活跃对话的聊天历史
        conv = tenant.get_active_conversation()
        history = conv.chat_history if conv else []
        conv_update = self._build_conv_choices(tenant_id)
        status = self._get_status(tenant_id)
        return tenant_id, history, conv_update, status

    # ── 对话管理 ──

    def new_conversation(self, tenant_id: str):
        """新建对话。返回 (清空的聊天记录, 对话列表更新, 状态)。"""
        err = self._ensure_initialized()
        if err:
            return [], self._build_conv_choices(tenant_id), err

        tenant = self._get_or_create_tenant(tenant_id)
        create_conversation(self._shared, tenant)
        self._save_tenant(tenant_id)
        return [], self._build_conv_choices(tenant_id), self._get_status(tenant_id)

    def switch_conversation(self, tenant_id: str, conv_display: str):
        """切换到指定对话。返回 (对话历史, 对话列表更新, 状态)。"""
        tenant = self._get_or_create_tenant(tenant_id)
        conv_id = self._parse_conv_id(conv_display)
        if conv_id and conv_id in tenant.conversations:
            tenant.active_conv_id = conv_id
            conv = tenant.conversations[conv_id]
            self._save_tenant(tenant_id)
            return conv.chat_history, self._build_conv_choices(tenant_id), self._get_status(tenant_id)
        return [], self._build_conv_choices(tenant_id), self._get_status(tenant_id)

    def delete_conversation(self, tenant_id: str, conv_display: str):
        """删除指定对话。返回 (聊天记录, 对话列表更新, 状态)。"""
        tenant = self._get_or_create_tenant(tenant_id)
        conv_id = self._parse_conv_id(conv_display)
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
        return history, self._build_conv_choices(tenant_id), self._get_status(tenant_id)

    # ── 聊天 ──

    def chat(self, message: str, history: List[dict], tenant_id: str):
        """处理用户消息，实时展示思考过程，最终返回 Agent 回答。"""
        err = self._ensure_initialized()
        if err:
            history.append({"role": "assistant", "content": err})
            yield history
            return

        if not message.strip():
            yield history
            return

        tenant = self._get_or_create_tenant(tenant_id)
        conv = self._ensure_active_conversation(tenant)

        if conv.title == "新对话" and message.strip():
            conv.title = message.strip()[:20]

        history.append({"role": "user", "content": message})
        conv.chat_history = history
        yield history

        # 初始化停止信号
        stop_event = threading.Event()
        self._stop_events[tenant_id] = stop_event

        # 通过 Queue 接收 Agent 事件，实现主线程 yield 推送
        event_queue: queue.Queue = queue.Queue()
        result_holder: List = [None, None]  # [response, error]
        _SENTINEL = object()  # 结束信号

        def on_event(event: AgentEvent):
            # 在 Agent 子线程中检查停止信号，抛异常中断迭代
            if stop_event.is_set():
                raise AgentStoppedError("用户停止了对话")
            event_queue.put(event)

        def run_agent():
            try:
                result_holder[0] = conv.agent.run(message, on_event=on_event)
            except AgentStoppedError:
                result_holder[1] = AgentStoppedError("用户停止了对话")
            except Exception as e:
                result_holder[1] = e
            event_queue.put(_SENTINEL)

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

        # 实时接收事件，更新思考过程气泡
        thinking_lines: List[str] = []
        stopped = False

        while True:
            try:
                event = event_queue.get(timeout=0.1)
            except queue.Empty:
                # 主线程也检查停止信号（以防 Agent 阻塞在 LLM 调用中）
                if stop_event.is_set():
                    stopped = True
                    break
                continue

            if event is _SENTINEL:
                break

            line = self._format_event(event)
            if line:
                thinking_lines.append(line)
                thinking_content = "\n".join(thinking_lines)
                display = history + [{"role": "assistant",
                                      "content": thinking_content + "\n\n⏳ *思考中...*"}]
                yield display

        thread.join(timeout=5)

        # 清理停止信号
        self._stop_events.pop(tenant_id, None)

        # 判断是否被用户停止
        if stopped or isinstance(result_holder[1], AgentStoppedError):
            logger.info("对话已被用户停止 | tenant={}", tenant_id[:8])
            final_content = "⏹️ 对话已停止，你可以补充信息后重新提问。"
            if thinking_lines:
                thinking_summary = self._build_thinking_summary(thinking_lines)
                final_content = final_content + "\n\n" + thinking_summary
        elif result_holder[1]:
            logger.error("Agent 执行失败: {}", result_holder[1])
            final_content = f"❌ 执行失败: {result_holder[1]}"
        else:
            final_content = self._fix_markdown_tables(result_holder[0] or "")
            if thinking_lines:
                thinking_summary = self._build_thinking_summary(thinking_lines)
                final_content = final_content + "\n\n" + thinking_summary

        history.append({"role": "assistant", "content": final_content})
        conv.chat_history = history
        self._save_tenant(tenant_id)
        yield history

    def stop_chat(self, tenant_id: str):
        """停止当前正在进行的对话。"""
        stop_event = self._stop_events.get(tenant_id)
        if stop_event:
            stop_event.set()
            logger.info("停止信号已发送 | tenant={}", tenant_id[:8])

    @staticmethod
    def _format_event(event: AgentEvent) -> str:
        """将 AgentEvent 格式化为一行可读文本。"""
        if event.type == EventType.THINKING:
            return f"🔄 **第 {event.iteration}/{event.max_iterations} 轮思考**"

        if event.type == EventType.TOOL_CALL:
            args_preview = json.dumps(event.tool_args, ensure_ascii=False)
            if len(args_preview) > 80:
                args_preview = args_preview[:80] + "..."
            parallel_tag = ""
            if event.parallel_total > 1:
                parallel_tag = f" ⚡ [{event.parallel_index}/{event.parallel_total}]"
            return f"  🔧 调用工具: `{event.tool_name}`{parallel_tag} | 参数: `{args_preview}`"

        if event.type == EventType.TOOL_RESULT:
            status = "✅" if event.success else "❌"
            preview = event.tool_result_preview.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "..."
            parallel_tag = ""
            if event.parallel_total > 1:
                parallel_tag = f" [{event.parallel_index}/{event.parallel_total}]"
            return f"  {status} 结果{parallel_tag} ({event.duration_ms}ms): {preview}"

        if event.type == EventType.ANSWERING:
            return "💡 **正在生成回答...**"

        if event.type == EventType.MAX_ITERATIONS:
            return "⚠️ 达到最大迭代次数，正在总结..."

        if event.type == EventType.ERROR:
            return f"❌ 错误: {event.message}"

        return ""

    @staticmethod
    def _fix_markdown_tables(text: str) -> str:
        """修复 LLM 生成的 Markdown 表格格式问题。

        常见问题：
        1. 表格行之间有空行 → Markdown 解析器认为表格结束
        2. 分隔行缺失或格式不对
        3. 列数不一致

        修复策略：移除表格区域内的多余空行，确保连续性。
        """
        lines = text.split("\n")
        result: list[str] = []
        in_table = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            is_table_line = stripped.startswith("|") and stripped.endswith("|")

            if is_table_line:
                if not in_table:
                    in_table = True
                result.append(line)
            elif in_table:
                if stripped == "":
                    # 空行：看后续是否还有表格行，如果有则跳过空行
                    next_table = False
                    for j in range(i + 1, min(i + 3, len(lines))):
                        next_stripped = lines[j].strip()
                        if next_stripped.startswith("|") and next_stripped.endswith("|"):
                            next_table = True
                            break
                        if next_stripped:
                            break
                    if next_table:
                        continue  # 跳过表格中间的空行
                    else:
                        in_table = False
                        result.append(line)
                else:
                    in_table = False
                    result.append(line)
            else:
                result.append(line)

        return "\n".join(result)

    @staticmethod
    def _build_thinking_summary(thinking_lines: List[str]) -> str:
        """将思考过程构建为 Markdown 折叠块。"""
        # 统计轮次和工具调用数
        iterations = sum(1 for l in thinking_lines if l.startswith("🔄"))
        tool_calls = sum(1 for l in thinking_lines if l.strip().startswith("🔧"))
        parallel_calls = sum(1 for l in thinking_lines if "⚡" in l)
        summary_title = f"💭 思考过程 ({iterations} 轮迭代"
        if tool_calls:
            summary_title += f", {tool_calls} 次工具调用"
            if parallel_calls:
                summary_title += f", 含 {parallel_calls} 次并发"
        summary_title += ")"

        detail_content = "\n".join(thinking_lines)
        return f"<details>\n<summary>{summary_title}</summary>\n\n{detail_content}\n\n</details>"

    # ── 知识库 ──

    def upload_files(self, files) -> str:
        """上传文件到知识库。"""
        kb = self._shared.knowledge_base if self._shared else None
        if not kb:
            return "❌ 知识库未初始化"
        if not files:
            return "请选择文件上传"

        results = []
        for file in files:
            try:
                file_path = file.name if hasattr(file, "name") else str(file)
                chunks = kb.import_file(file_path)
                results.append(f"✅ {os.path.basename(file_path)}: {chunks} 个文本块")
            except Exception as e:
                results.append(f"❌ {os.path.basename(str(file))}: {e}")

        results.append(f"\n📚 知识库总量: {kb.count()} 个文本块")
        return "\n".join(results)

    def clear_knowledge_base(self) -> str:
        """清空知识库。"""
        kb = self._shared.knowledge_base if self._shared else None
        if kb:
            kb.clear()
            return "✅ 知识库已清空"
        return "❌ 知识库未初始化"

    # ── 状态 ──

    def _get_status(self, tenant_id: str) -> str:
        """获取系统状态。"""
        if not self._initialized or not self._shared:
            return "⚠️ Agent 未初始化，发送消息后将自动初始化"

        tenant = self._tenants.get(tenant_id)
        lines = [
            "🧠 系统状态：",
            f"  模型: {self._shared.llm_client.model}",
        ]

        warning = self._tenant_warnings.get(tenant_id)
        if warning:
            lines.append("")
            lines.append(warning)

        if tenant:
            conv = tenant.get_active_conversation()
            if conv:
                lines.append(f"  当前对话: {conv.title}")
                lines.append(f"  短期记忆: {conv.memory.token_count} tokens")
            lines.append(f"  对话数: {len(tenant.conversations)}")
            if tenant.vector_store:
                lines.append(f"  长期记忆: {tenant.vector_store.count()} 条")

        kb = self._shared.knowledge_base
        if kb:
            lines.append(f"  知识库: {kb.count()} 个文本块")
        return "\n".join(lines)

    def get_status(self, tenant_id: str) -> str:
        """公开的状态查询接口。"""
        return self._get_status(tenant_id)

    # ── 内部辅助 ──

    def _build_conv_choices(self, tenant_id: str):
        """构建对话列表 Radio 的 gr.update。"""
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.conversations:
            return gr.update(choices=[], value=None)

        lines = []
        active_line = None
        for info in tenant.get_conversation_list():
            marker = "▶ " if info["active"] else "  "
            line = f"{marker}[{info['id']}] {info['title']}"
            lines.append(line)
            if info["active"]:
                active_line = line
        return gr.update(choices=lines, value=active_line)

    @staticmethod
    def _parse_conv_id(conv_display: str) -> Optional[str]:
        """从对话列表的展示文本中解析出对话 ID。"""
        if not conv_display:
            return None
        text = conv_display.strip().lstrip("▶").strip()
        if text.startswith("[") and "]" in text:
            return text[1:text.index("]")]
        return None


def create_ui() -> gr.Blocks:
    """创建 Gradio Web 界面。"""
    app = AgentApp()

    with gr.Blocks(title="LLM ReAct Agent") as demo:
        # tenant_id 通过 JS 从 sessionStorage 读取（每个标签页独立），刷新后保持不变
        # 使用隐藏 Textbox 承载 tenant_id，确保前端 JS 更新后能参与后续事件的 inputs
        tenant_id = gr.Textbox(value="", visible=False, label="", show_label=False)
        tenant_meta = gr.Textbox(value="", visible=False, label="", show_label=False)
        saved_msg = gr.State("")

        gr.Markdown(
            "# 🤖 LLM ReAct Agent\n"
            "支持自主推理、工具调用、知识库问答、长期记忆的智能助手"
        )

        with gr.Row():
            # 左侧：对话列表 + 聊天区域
            with gr.Column(scale=3):
                with gr.Row():
                    with gr.Column(scale=1, min_width=180):
                        gr.Markdown("### 💬 对话列表")
                        new_conv_btn = gr.Button("➕ 新建对话", variant="primary", size="sm")
                        conv_list = gr.Radio(
                            choices=[],
                            label="",
                            show_label=False,
                            interactive=True,
                        )
                        del_conv_btn = gr.Button("🗑️ 删除当前对话", size="sm")

                    with gr.Column(scale=3):
                        chatbot = gr.Chatbot(
                            label="对话",
                            elem_classes=["chatbot-container"],
                        )
                        with gr.Row():
                            msg_input = gr.Textbox(
                                placeholder="输入消息...",
                                label="",
                                show_label=False,
                                scale=5,
                                container=False,
                            )
                            send_btn = gr.Button("发送", variant="primary", scale=1)
                            stop_btn = gr.Button("⏹ 停止", variant="stop", scale=1, visible=False)

            # 右侧：状态 + 知识库
            with gr.Column(scale=1):
                status_box = gr.Textbox(
                    label="📊 系统状态",
                    value="⚠️ 发送消息后自动初始化",
                    interactive=False,
                    lines=8,
                    elem_classes=["status-box"],
                )
                refresh_btn = gr.Button("🔄 刷新状态", size="sm")

                gr.Markdown("### 📚 知识库管理")
                file_upload = gr.File(
                    label="上传文档",
                    file_count="multiple",
                    file_types=[".txt", ".md", ".pdf"],
                )
                upload_btn = gr.Button("📥 导入到知识库", size="sm")
                upload_result = gr.Textbox(
                    label="导入结果",
                    interactive=False,
                    lines=4,
                    elem_classes=["status-box"],
                )
                clear_kb_btn = gr.Button("🗑️ 清空知识库", size="sm")

        # ── 辅助函数 ──

        def save_and_clear(message):
            """保存消息并清空输入框，同时切换按钮状态（发送→停止）。"""
            return message, "", gr.update(visible=False), gr.update(visible=True)

        def restore_buttons():
            """聊天结束后恢复按钮状态（停止→发送）。"""
            return gr.update(visible=True), gr.update(visible=False)

        def on_new_conv(tenant_id_val):
            history, conv_update, status = app.new_conversation(tenant_id_val)
            return history, conv_update, status

        def on_switch_conv(tenant_id_val, selected):
            if not selected:
                return gr.update(), gr.update(), gr.update()
            history, conv_update, status = app.switch_conversation(tenant_id_val, selected)
            return history, conv_update, status

        def on_delete_conv(tenant_id_val, selected):
            history, conv_update, status = app.delete_conversation(tenant_id_val, selected or "")
            return history, conv_update, status

        def on_chat_done(tenant_id_val):
            conv_update = app._build_conv_choices(tenant_id_val)
            status = app.get_status(tenant_id_val)
            return conv_update, status, gr.update(visible=True), gr.update(visible=False)

        # ── 页面加载：从 sessionStorage 恢复 tenant_id 并恢复会话 ──
        # 第一步：JS 从 sessionStorage 读取 tenant_id 写入隐藏 Textbox
        # 第二步：Python 根据 tenant_id 恢复对话列表和聊天历史
        demo.load(
            fn=None,
            inputs=[tenant_id],
            outputs=[tenant_id, tenant_meta],
            js=_JS_LOAD_TENANT_ID,
        )

        # 当 tenant_id 被前端 JS 写入后，触发后端恢复对话（避免依赖 js-only 事件的 .then 链）
        tenant_id.change(
            fn=app.restore_session,
            inputs=[tenant_id, tenant_meta],
            outputs=[tenant_id, chatbot, conv_list, status_box],
            show_progress="hidden",
        )

        # ── 事件绑定 ──

        new_conv_btn.click(
            fn=on_new_conv,
            inputs=[tenant_id],
            outputs=[chatbot, conv_list, status_box],
        )

        conv_list.input(
            fn=on_switch_conv,
            inputs=[tenant_id, conv_list],
            outputs=[chatbot, conv_list, status_box],
        )

        del_conv_btn.click(
            fn=on_delete_conv,
            inputs=[tenant_id, conv_list],
            outputs=[chatbot, conv_list, status_box],
        )

        # 发送消息（Enter 键）
        msg_input.submit(
            fn=save_and_clear,
            inputs=[msg_input],
            outputs=[saved_msg, msg_input, send_btn, stop_btn],
        ).then(
            fn=app.chat,
            inputs=[saved_msg, chatbot, tenant_id],
            outputs=[chatbot],
        ).then(
            fn=on_chat_done,
            inputs=[tenant_id],
            outputs=[conv_list, status_box, send_btn, stop_btn],
        )

        # 发送消息（按钮点击）
        send_btn.click(
            fn=save_and_clear,
            inputs=[msg_input],
            outputs=[saved_msg, msg_input, send_btn, stop_btn],
        ).then(
            fn=app.chat,
            inputs=[saved_msg, chatbot, tenant_id],
            outputs=[chatbot],
        ).then(
            fn=on_chat_done,
            inputs=[tenant_id],
            outputs=[conv_list, status_box, send_btn, stop_btn],
        )

        # 停止按钮
        stop_btn.click(
            fn=app.stop_chat,
            inputs=[tenant_id],
        )

        refresh_btn.click(
            fn=app.get_status, inputs=[tenant_id], outputs=[status_box],
        )
        upload_btn.click(
            fn=app.upload_files, inputs=[file_upload], outputs=[upload_result],
        ).then(
            fn=app.get_status, inputs=[tenant_id], outputs=[status_box],
        )
        clear_kb_btn.click(
            fn=app.clear_knowledge_base, outputs=[upload_result],
        ).then(
            fn=app.get_status, inputs=[tenant_id], outputs=[status_box],
        )

    return demo


if __name__ == "__main__":
    demo = create_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
        css="""
        .chatbot-container { height: 520px !important; }
        .status-box { font-family: monospace; font-size: 13px; }
        """,
    )
