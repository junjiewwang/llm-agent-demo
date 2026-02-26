"""FastAPI 依赖注入。

提供 AgentService 单例和公共参数提取。
"""

from typing import Optional

from fastapi import Query, Header, HTTPException

from src.services import AgentService
from src.services.auth_service import AuthService

# 全局单例（进程内共享）
_service: Optional[AgentService] = None
_auth_service: Optional[AuthService] = None


def get_service() -> AgentService:
    """获取 AgentService 单例。"""
    global _service
    if _service is None:
        _service = AgentService()
    return _service


def get_auth_service() -> AuthService:
    """获取 AuthService 单例。"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


def get_tenant_id(
    tenant_id: Optional[str] = Query(None, description="租户 ID (访客模式)"),
    authorization: Optional[str] = Header(None, description="Bearer Token"),
) -> str:
    """提取 tenant_id。

    优先级：
    1. Authorization Header (Bearer Token) -> 解析出 user_id 作为 tenant_id
    2. Query Parameter (tenant_id) -> 访客模式或旧版兼容
    """
    # 1. 尝试解析 Token
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        auth_service = get_auth_service()
        user_id = auth_service.verify_token(token)
        if user_id:
            return user_id

    # 2. 回退到 Query Parameter
    if tenant_id:
        return tenant_id

    # 3. 均未提供
    raise HTTPException(
        status_code=401,
        detail="Authentication required: Provide 'tenant_id' query param or 'Authorization' header",
    )
