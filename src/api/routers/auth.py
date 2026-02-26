from fastapi import APIRouter, HTTPException, Depends

from src.api.dependencies import get_auth_service, get_tenant_id
from src.api.schemas import ApiResponse, AuthRequest, TokenResponse, UserInfo
from src.services.auth_service import AuthService

router = APIRouter()


@router.post("/auth/register", summary="注册新用户")
def register(
    req: AuthRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse:
    user = auth_service.register_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=400, detail="Username already exists")

    token = auth_service.create_token_for_user(user)
    return ApiResponse(
        data=TokenResponse(
            access_token=token,
            user_id=user["id"],
            username=user["username"],
        )
    )


@router.post("/auth/login", summary="用户登录")
def login(
    req: AuthRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse:
    user = auth_service.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = auth_service.create_token_for_user(user)
    return ApiResponse(
        data=TokenResponse(
            access_token=token,
            user_id=user["id"],
            username=user["username"],
        )
    )


@router.get("/auth/me", summary="获取当前用户信息")
def get_current_user(
    user_id: str = Depends(get_tenant_id),
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse:
    # 注意：get_tenant_id 可能会返回 query param 中的随机 tenant_id
    # 如果该 ID 在 UserStore 中找不到，说明是访客模式，不应该调用此接口或返回空
    user = auth_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found (Guest mode?)")

    return ApiResponse(
        data=UserInfo(
            id=user["id"],
            username=user["username"],
            created_at=user["created_at"],
        )
    )
