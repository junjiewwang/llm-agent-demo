"""API 请求/响应模型定义。

所有 Pydantic 模型集中管理，保证 API 契约清晰。
SSE 事件类型包含当前已实现的和预留的扩展类型。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── SSE 事件类型 ──

class SSEEventType(str, Enum):
    """SSE 事件类型枚举。"""

    # 当前已实现
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_CONFIRM = "tool_confirm"
    TOOL_RESULT = "tool_result"
    ANSWERING = "answering"
    DONE = "done"
    ERROR = "error"
    MAX_ITERATIONS = "max_iterations"

    # 预留扩展
    ANSWER_TOKEN = "answer_token"  # 流式 token（未来 LLM 逐 token 输出）
    PLAN = "plan"  # 任务分解（未来 Plan-and-Execute 模式）


# ── 通用响应信封 ──

class ApiResponse(BaseModel):
    """统一 API 响应信封。"""

    success: bool = True
    data: Any = None
    error: Optional[str] = None


# ── 聊天相关 ──

class ChatRequest(BaseModel):
    """聊天请求。"""

    message: str = Field(..., min_length=1, max_length=10000, description="用户消息")


class ToolConfirmRequest(BaseModel):
    """工具执行确认请求。"""

    confirm_id: str = Field(..., description="确认请求唯一标识")
    approved: bool = Field(..., description="是否批准执行")


class ConversationInfo(BaseModel):
    """对话信息。"""

    id: str
    title: str
    active: bool = False


class StatusInfo(BaseModel):
    """系统状态信息。"""

    initialized: bool = False
    model: Optional[str] = None
    current_conversation: Optional[Dict[str, Any]] = None
    conversation_count: int = 0
    long_term_memory_count: int = 0
    knowledge_base_chunks: int = 0


class SessionData(BaseModel):
    """会话恢复数据。"""

    chat_history: List[dict] = Field(default_factory=list)
    conversations: List[ConversationInfo] = Field(default_factory=list)
    status: StatusInfo = Field(default_factory=StatusInfo)


class NewConversationData(BaseModel):
    """新建对话返回数据。"""

    conversation: ConversationInfo
    conversations: List[ConversationInfo] = Field(default_factory=list)
    status: StatusInfo = Field(default_factory=StatusInfo)


class ConversationActionData(BaseModel):
    """对话操作（切换/删除）返回数据。"""

    chat_history: List[dict] = Field(default_factory=list)
    conversations: List[ConversationInfo] = Field(default_factory=list)
    status: StatusInfo = Field(default_factory=StatusInfo)


class UploadResultItem(BaseModel):
    """单个文件上传结果。"""

    file: str
    chunks: int = 0
    error: Optional[str] = None


class UploadData(BaseModel):
    """文件上传返回数据。"""

    results: List[UploadResultItem] = Field(default_factory=list)
    total_chunks: int = 0
    error: Optional[str] = None


class AuthRequest(BaseModel):
    """用户认证请求（注册/登录）。"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class TokenResponse(BaseModel):
    """Token 响应。"""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str


class UserInfo(BaseModel):
    """用户信息。"""
    id: str
    username: str
    created_at: float

