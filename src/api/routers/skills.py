"""技能管理路由。

GET  /api/skills                      — 获取所有 Skill 列表
POST /api/skills/{skill_name}/toggle  — 启用/禁用指定 Skill

注意：Skill 是全局共享资源（SharedComponents 级别），不区分租户。
"""

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_service
from src.api.schemas import ApiResponse, SkillInfo, ToggleSkillRequest
from src.services import AgentService

router = APIRouter()


@router.get("/skills", summary="获取所有 Skill 列表")
def list_skills(
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """返回所有已注册 Skill 的信息（含启停状态、工具依赖满足情况等）。"""
    service.ensure_initialized()
    skills = service.list_skills()
    return ApiResponse(data=[SkillInfo(**s) for s in skills])


@router.post("/skills/{skill_name}/toggle", summary="启用/禁用 Skill")
def toggle_skill(
    skill_name: str,
    body: ToggleSkillRequest,
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """切换指定 Skill 的启停状态。"""
    service.ensure_initialized()
    success = service.toggle_skill(skill_name, body.enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' 不存在")
    return ApiResponse(data={"name": skill_name, "enabled": body.enabled})
