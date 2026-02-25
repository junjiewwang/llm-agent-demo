"""系统状态路由。

GET /api/status — 获取系统状态
"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_service, get_tenant_id
from src.api.schemas import ApiResponse, StatusInfo
from src.services import AgentService

router = APIRouter()


@router.get("/status", summary="获取系统状态")
def get_status(
    tenant_id: str = Depends(get_tenant_id),
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    data = service.get_status(tenant_id)
    return ApiResponse(data=StatusInfo(**data))
