"""MCP Server 管理路由。

GET  /api/mcp/status  — 查询所有 MCP Server 的健康状态
POST /api/mcp/reload  — 重新加载 .mcp.json 配置（断开现有连接，重新发现并注册工具）
"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_service
from src.api.schemas import ApiResponse
from src.services import AgentService
from src.utils.logger import logger

router = APIRouter(prefix="/mcp")


@router.get("/status", summary="查询 MCP Server 状态")
def mcp_status(
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """返回所有 MCP Server 的连接状态、工具数量、重连计数等信息。"""
    mcp_manager = _get_mcp_manager(service)
    if mcp_manager is None:
        return ApiResponse(data={"servers": {}, "message": "MCP 未启用"})

    health = mcp_manager.health_check()
    return ApiResponse(data={
        "servers": health,
        "connected": mcp_manager.connected_servers,
        "total_servers": len(health),
    })


@router.post("/reload", summary="重新加载 MCP 配置")
def mcp_reload(
    service: AgentService = Depends(get_service),
) -> ApiResponse:
    """重新加载 .mcp.json 配置。

    流程：
    1. 关闭现有 MCP 连接
    2. 从 ToolRegistry 移除旧的 MCP 工具
    3. 重新读取 .mcp.json
    4. 连接新配置的 Server 并注册工具

    注意：此操作不影响内置工具和正在进行的对话。
    """
    if not service.shared:
        return ApiResponse(success=False, error="服务未初始化")

    registry = service.shared.tool_registry
    old_manager = service.shared.mcp_manager

    # 1. 关闭旧连接
    if old_manager:
        # 收集旧的 MCP 工具名（mcp__ 前缀）
        old_mcp_tools = [name for name in registry.tool_names if name.startswith("mcp__")]
        old_manager.shutdown()

        # 2. 从 ToolRegistry 移除旧的 MCP 工具
        for name in old_mcp_tools:
            registry.unregister(name)
        logger.info("MCP 热重载：已移除 {} 个旧工具", len(old_mcp_tools))

    # 3. 重新发现并注册
    from src.tools.mcp import MCPToolManager
    new_manager = MCPToolManager(config_path=".mcp.json")
    try:
        count = new_manager.discover_and_register(registry)
        if count > 0:
            service.shared.mcp_manager = new_manager
            logger.info("MCP 热重载完成：注册 {} 个工具", count)
            return ApiResponse(data={
                "reloaded": True,
                "tools_registered": count,
                "connected_servers": new_manager.connected_servers,
            })
        else:
            # 没有工具，清理
            new_manager.shutdown()
            service.shared.mcp_manager = None
            return ApiResponse(data={
                "reloaded": True,
                "tools_registered": 0,
                "message": "没有 enabled 的 MCP Server 或无工具",
            })
    except Exception as e:
        logger.error("MCP 热重载失败: {}", e)
        new_manager.shutdown()
        service.shared.mcp_manager = None
        return ApiResponse(success=False, error=f"MCP 重载失败: {e}")


def _get_mcp_manager(service: AgentService):
    """安全获取 MCPToolManager 实例。"""
    if service.shared and service.shared.mcp_manager:
        return service.shared.mcp_manager
    return None
