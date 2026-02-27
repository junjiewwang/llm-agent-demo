"""FastAPI 应用实例。

职责：
- 创建 FastAPI app
- 注册 CORS 中间件
- 挂载路由
- 生产模式下托管 React 静态文件
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.dependencies import get_service
from src.api.routers import chat, session, knowledge, status, auth, skills
from src.observability import init_telemetry, shutdown_telemetry
from src.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化 OTel + AgentService，关闭时清理。"""
    # OpenTelemetry 初始化（OTEL_ENABLED=false 时为 no-op）
    init_telemetry()

    service = get_service()
    try:
        service.ensure_initialized()
        logger.info("AgentService 初始化成功")
    except ValueError as e:
        logger.warning("AgentService 初始化跳过（将在首次请求时重试）: {}", e)

    yield

    # 清理 OTel 资源（flush pending spans/metrics）
    shutdown_telemetry()


def create_app() -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(
        title="LLM ReAct Agent API",
        description="支持自主推理、工具调用、知识库问答、长期记忆的智能助手 API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS：开发模式允许 Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite dev server
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册 API 路由
    app.include_router(auth.router, prefix="/api", tags=["认证"])
    app.include_router(session.router, prefix="/api", tags=["会话管理"])
    app.include_router(chat.router, prefix="/api", tags=["聊天"])
    app.include_router(knowledge.router, prefix="/api", tags=["知识库"])
    app.include_router(status.router, prefix="/api", tags=["系统状态"])
    app.include_router(skills.router, prefix="/api", tags=["技能管理"])

    # 生产模式：托管 React 构建产物
    frontend_dist = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
    if os.path.isdir(frontend_dist):
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
        logger.info("已挂载前端静态文件: {}", frontend_dist)

    return app
