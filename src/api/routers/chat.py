"""聊天路由。

POST /api/chat        — 聊天（SSE 流式响应）
POST /api/chat/stop   — 停止聊天
GET  /api/chat/status  — 检查聊天状态（预留）
"""

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agent.events import AgentEvent, EventType
from src.api.dependencies import get_service, get_tenant_id
from src.api.schemas import ApiResponse, ChatRequest, SSEEventType, ToolConfirmRequest
from src.observability.instruments import start_thread_with_context
from src.services import AgentService
from src.services.agent_service import ChatResult

router = APIRouter()

_SENTINEL = object()


def _agent_event_to_sse(event: AgentEvent) -> dict:
    """将 AgentEvent 转换为 SSE event dict。"""
    event_type_map = {
        EventType.THINKING: SSEEventType.THINKING,
        EventType.TOOL_CALL: SSEEventType.TOOL_CALL,
        EventType.TOOL_CONFIRM: SSEEventType.TOOL_CONFIRM,
        EventType.TOOL_RESULT: SSEEventType.TOOL_RESULT,
        EventType.ANSWERING: SSEEventType.ANSWERING,
        EventType.MAX_ITERATIONS: SSEEventType.MAX_ITERATIONS,
        EventType.ERROR: SSEEventType.ERROR,
        EventType.STATUS: SSEEventType.STATUS,
        EventType.PLAN_CREATED: SSEEventType.PLAN_CREATED,
        EventType.STEP_START: SSEEventType.STEP_START,
        EventType.STEP_DONE: SSEEventType.STEP_DONE,
        EventType.REPLAN: SSEEventType.REPLAN,
    }
    sse_type = event_type_map.get(event.type, SSEEventType.ERROR)

    data = {}
    if event.type == EventType.THINKING:
        data = {"iteration": event.iteration, "max_iterations": event.max_iterations}
    elif event.type == EventType.TOOL_CALL:
        data = {
            "tool_name": event.tool_name,
            "tool_args": event.tool_args,
            "parallel_total": event.parallel_total,
            "parallel_index": event.parallel_index,
        }
    elif event.type == EventType.TOOL_CONFIRM:
        data = {
            "confirm_id": event.confirm_id,
            "tool_name": event.tool_name,
            "tool_args": event.tool_args,
        }
    elif event.type == EventType.TOOL_RESULT:
        data = {
            "tool_name": event.tool_name,
            "success": event.success,
            "duration_ms": event.duration_ms,
            "tool_result_preview": event.tool_result_preview,
            "parallel_total": event.parallel_total,
            "parallel_index": event.parallel_index,
        }
    elif event.type == EventType.ANSWERING:
        data = {}
    elif event.type == EventType.MAX_ITERATIONS:
        data = {"message": "达到最大迭代次数，正在总结"}
    elif event.type == EventType.ERROR:
        data = {"message": event.message}
    elif event.type == EventType.STATUS:
        data = {"message": event.message}
    elif event.type == EventType.PLAN_CREATED:
        data = {
            "plan": event.plan,
            "total_steps": event.total_steps,
            "message": event.message,
        }
    elif event.type == EventType.STEP_START:
        data = {
            "step_id": event.step_id,
            "step_index": event.step_index,
            "total_steps": event.total_steps,
            "message": event.message,
        }
    elif event.type == EventType.STEP_DONE:
        data = {
            "step_id": event.step_id,
            "step_index": event.step_index,
            "total_steps": event.total_steps,
            "step_status": event.step_status,
            "message": event.message,
        }
    elif event.type == EventType.REPLAN:
        data = {
            "step_index": event.step_index,
            "total_steps": event.total_steps,
            "message": event.message,
        }

    return {"event": sse_type.value, "data": json.dumps(data, ensure_ascii=False)}


def _chat_result_to_sse(result: ChatResult, service: AgentService, tenant_id: str) -> dict:
    """将 ChatResult 转换为最终 SSE done/error event。"""
    if result.error:
        return {
            "event": SSEEventType.ERROR.value,
            "data": json.dumps({"message": result.error}, ensure_ascii=False),
        }

    data = {
        "content": result.content,
        "stopped": result.stopped,
        "chat_history": [],
        "conversations": service.get_conversation_list(tenant_id),
        "status": service.get_status(tenant_id),
    }

    # 消息级 token 用量（feature flag 控制）
    if result.usage:
        data["usage"] = result.usage

    # 获取当前对话的 chat_history
    tenant = service._tenants.get(tenant_id)
    if tenant:
        conv = tenant.get_active_conversation()
        if conv:
            data["chat_history"] = conv.chat_history

    return {
        "event": SSEEventType.DONE.value,
        "data": json.dumps(data, ensure_ascii=False),
    }


@router.post("/chat", summary="聊天（SSE 流式响应）")
async def chat(
    request: ChatRequest,
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
):
    """SSE 流式聊天接口。

    AgentService.chat() 是同步生成器（内部有阻塞的 queue.get），
    不能直接在 asyncio 事件循环中迭代。
    解决方案：在独立线程中运行同步生成器，通过 asyncio.Queue 桥接到异步生成器。
    """
    async_queue: asyncio.Queue = asyncio.Queue()
    # 在主协程（asyncio 线程）中捕获 event loop 引用，传递给子线程
    loop = asyncio.get_running_loop()

    def _run_sync_generator():
        """在独立线程中运行同步生成器，将结果推入 asyncio.Queue。"""
        try:
            for item in service.chat(tenant_id, request.message):
                loop.call_soon_threadsafe(async_queue.put_nowait, item)
        except Exception as e:
            loop.call_soon_threadsafe(async_queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(async_queue.put_nowait, _SENTINEL)

    async def event_generator():
        # 使用 start_thread_with_context 自动传播 OTel Context (L1→L2)
        thread = start_thread_with_context(
            _run_sync_generator, daemon=True, name="sse-bridge",
        )

        while True:
            item = await async_queue.get()

            if item is _SENTINEL:
                break

            if isinstance(item, Exception):
                yield {
                    "event": SSEEventType.ERROR.value,
                    "data": json.dumps({"message": str(item)}, ensure_ascii=False),
                }
                break

            if isinstance(item, AgentEvent):
                yield _agent_event_to_sse(item)
            elif isinstance(item, ChatResult):
                yield _chat_result_to_sse(item, service, tenant_id)

    return EventSourceResponse(event_generator())


@router.post("/chat/stop", summary="停止聊天")
def stop_chat(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    success = service.stop_chat(tenant_id)
    if success:
        return ApiResponse(data={"message": "停止信号已发送"})
    return ApiResponse(success=False, error="没有正在进行的对话")


@router.post("/chat/confirm", summary="确认或拒绝工具执行")
def confirm_tool(
    request: ToolConfirmRequest,
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """用户对高风险工具执行做出确认决策。

    Agent 线程在发送 TOOL_CONFIRM 事件后阻塞等待，
    此接口唤醒等待线程并传递用户的审批决策。
    """
    success = service.confirm_tool(request.confirm_id, request.approved)
    if success:
        action = "批准" if request.approved else "拒绝"
        return ApiResponse(data={"message": f"已{action}执行"})
    raise HTTPException(status_code=404, detail="确认请求不存在或已过期")


@router.get("/chat/status", summary="检查聊天状态（预留）")
def chat_status(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """预留接口：SSE 断连后前端可查询 Agent 是否仍在运行。"""
    is_running = service.is_chatting(tenant_id)
    return ApiResponse(data={"is_running": is_running})
