"""认证服务模块。

负责用户注册、登录校验、JWT 生成与验证。
"""
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import jwt
from passlib.context import CryptContext

from src.config import settings
from src.persistence.user_store import UserStore
from src.utils.logger import logger

# 使用 pbkdf2_sha256 替代 bcrypt：
# passlib 与 bcrypt>=4.1 存在已知兼容性 bug（detect_wrap_bug 使用超长测试字符串），
# pbkdf2_sha256 同样安全，且无密码长度限制、无外部 C 依赖。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class AuthService:
    """认证服务。"""

    def __init__(self):
        self._store = UserStore()
        self._secret_key = settings.auth.secret_key
        self._algorithm = settings.auth.algorithm
        self._expire_minutes = settings.auth.access_token_expire_minutes

    def _verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    def _get_password_hash(self, password: str) -> str:
        return pwd_context.hash(password)

    def _create_access_token(self, data: dict) -> str:
        """生成 JWT Token。"""
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(minutes=self._expire_minutes)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self._secret_key, algorithm=self._algorithm)
        return encoded_jwt

    def register_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """注册新用户。

        Returns:
            user_data: 注册成功返回用户数据，用户名已存在返回 None
        """
        password_hash = self._get_password_hash(password)
        return self._store.create_user(username, password_hash)

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """验证用户登录。

        Returns:
            user_data: 验证成功返回用户数据，失败返回 None
        """
        user = self._store.get_user_by_username(username)
        if not user:
            return None
        if not self._verify_password(password, user["password_hash"]):
            return None
        return user

    def create_token_for_user(self, user: Dict[str, Any]) -> str:
        """为用户生成 Access Token。"""
        # sub (Subject) 字段存储 user_id，即系统的 tenant_id
        access_token = self._create_access_token(
            data={"sub": user["id"], "username": user["username"]}
        )
        return access_token

    def verify_token(self, token: str) -> Optional[str]:
        """验证 Token 并返回 user_id (tenant_id)。

        Returns:
            user_id: 验证成功返回 ID，失效或非法返回 None
        """
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
            user_id: str = payload.get("sub")
            if user_id is None:
                return None
            return user_id
        except jwt.PyJWTError as e:
            logger.warning("Token 验证失败: {}", e)
            return None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取用户信息。"""
        return self._store.get_user_by_id(user_id)
