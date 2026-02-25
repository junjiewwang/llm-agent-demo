"""会话管理路由。

GET  /api/session          — 恢复会话
GET  /api/conversations     — 获取对话列表
POST /api/conversations     — 新建对话
PUT  /api/conversations/{id}/activate — 切换对话
DELETE /api/conversations/{id}        — 删除对话
"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_service, get_tenant_id
from src.api.schemas import (
    ApiResponse,
    SessionData,
    NewConversationData,
    ConversationActionData,
    ConversationInfo,
)
from src.services import AgentService

router = APIRouter()


@router.get("/session", summary="恢复会话")
def restore_session(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    try:
        data = service.restore_session(tenant_id)
        return ApiResponse(data=SessionData(**data))
    except ValueError as e:
        return ApiResponse(success=False, error=str(e))


@router.get("/conversations", summary="获取对话列表")
def list_conversations(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    conversations = service.get_conversation_list(tenant_id)
    return ApiResponse(data=[ConversationInfo(**c) for c in conversations])


@router.post("/conversations", summary="新建对话")
def create_conversation(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    try:
        data = service.new_conversation(tenant_id)
        return ApiResponse(data=NewConversationData(**data))
    except ValueError as e:
        return ApiResponse(success=False, error=str(e))


@router.put("/conversations/{conv_id}/activate", summary="切换对话")
def switch_conversation(
    conv_id: str,
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    data = service.switch_conversation(tenant_id, conv_id)
    return ApiResponse(data=ConversationActionData(**data))


@router.delete("/conversations/{conv_id}", summary="删除对话")
def delete_conversation(
    conv_id: str,
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    data = service.delete_conversation(tenant_id, conv_id)
    return ApiResponse(data=ConversationActionData(**data))
