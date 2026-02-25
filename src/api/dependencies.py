"""FastAPI 依赖注入。

提供 AgentService 单例和公共参数提取。
"""

from typing import Optional

from fastapi import Query

from src.services import AgentService

# 全局单例（进程内共享）
_service: Optional[AgentService] = None


def get_service() -> AgentService:
    """获取 AgentService 单例。"""
    global _service
    if _service is None:
        _service = AgentService()
    return _service


def get_tenant_id(tenant_id: str = Query(..., description="租户 ID")) -> str:
    """从 Query Parameter 提取 tenant_id。"""
    return tenant_id
