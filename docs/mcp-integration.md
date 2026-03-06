# MCP (Model Context Protocol) 外部工具集成

## 1. 需求背景

当前 Agent 的工具体系是内置封闭的（Calculator、WebSearch、FileReader/Writer、ExecuteCommand 等），无法动态接入外部工具生态。通过集成 MCP 协议，Agent 可以：

- **动态发现外部工具**：通过 `.mcp.json` 配置文件声明 MCP Server，启动时自动发现并注册
- **兼容标准生态**：配置格式与 Cursor / Claude Desktop 完全兼容，可直接复用社区 MCP Server
- **双传输协议**：支持 stdio（本地子进程）和 Streamable HTTP（远程服务）

## 2. 配置格式

项目根目录 `.mcp.json`：

```json
{
  "mcpServers": {
    "docker-lima": {
      "command": "uvx",
      "args": ["mcp-server-docker"],
      "env": {
        "DOCKER_HOST": "unix:///Users/xxx/.lima/docker/sock/docker.sock"
      },
      "description": "Lima VM rootful docker",
      "disabled": true
    },
    "tapd": {
      "url": "https://mcp-oa.tapd.woa.com/mcp/",
      "timeout": 20000,
      "headers": {
        "X-Tapd-Access-Token": "xxx"
      },
      "transportType": "streamable-http",
      "description": "TAPD 项目管理",
      "disabled": false
    }
  }
}
```

传输类型自动推断：有 `command` → stdio，有 `url` → streamable-http。

## 3. 架构设计

### 3.1 总体架构

```
factory.py create_tool_registry()
    │
    ├── 内置工具注册（已有逻辑，不变）
    │
    └── MCPToolManager.discover_and_register(registry)  ← 新增
         │
         ├── 解析 .mcp.json
         ├── 对每个 enabled server:
         │    ├── 建立连接（stdio_client / streamable_http_client）
         │    ├── session.initialize()
         │    ├── session.list_tools() → 发现工具
         │    └── 为每个 tool 创建 MCPTool(BaseTool) 注册到 registry
         └── 返回注册的工具总数
```

### 3.2 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `MCPDefaults` | `src/tools/mcp/config.py` | 默认常量集中管理（超时、重连参数等） |
| `MCPServerConfig` | `src/tools/mcp/config.py` | `.mcp.json` 配置解析，dataclass 建模 |
| `MCPTool` | `src/tools/mcp/mcp_tool.py` | BaseTool 适配器，将 MCP tool 适配为内置工具接口 |
| `ConnectionStatus` | `src/tools/mcp/manager.py` | Server 连接状态枚举（disconnected/connecting/connected/reconnecting/failed） |
| `ServerState` | `src/tools/mcp/manager.py` | 单个 Server 的运行时状态追踪（session/重连计数/错误等） |
| `MCPToolManager` | `src/tools/mcp/manager.py` | MCP Server 生命周期管理（连接/发现/健康检查/重连/关闭） |

### 3.3 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 适配模式 | MCPTool 继承 BaseTool | 零侵入，复用 ToolRegistry 执行/截断/可观测等全部机制 |
| 命名空间 | `mcp__{server}__{tool}` | 防冲突 + 可溯源 + Cursor 命名约定一致 |
| 生命周期 | 随 App 启动/关闭 | MCP Server 进程作为子进程管理，FastAPI lifespan 优雅关闭 |
| 同步/异步桥接 | 独立事件循环 + `run_coroutine_threadsafe` | BaseTool.execute 同步接口，需桥接到 MCP 异步 call_tool |
| 结构化契约 | MCPTool 默认关闭 | MCP 工具的参数验证由 Server 端负责 |
| 配置格式 | 兼容 Cursor .mcp.json | 零学习成本，可直接复用现有 MCP 配置 |
| session 持有方式 | session_resolver 间接引用 | 重连后 MCPTool 自动使用新 session，无需重新注册工具 |
| 重连触发 | MCPTool 连接异常 → reconnect_hook | 惰性重连（用到才重连），避免不必要的资源开销 |
| 超时分层 | 连接超时 / call_tool 超时 / 整体超时 分别配置 | 不同阶段失败的容忍度不同 |

## 4. 实施计划

### Sprint 1：核心功能 ✅

- [x] 配置解析（`MCPServerConfig`）
- [x] MCPTool 适配器
- [x] MCPToolManager 基础版（连接 + 发现 + 注册）
- [x] 工厂集成（`create_tool_registry` 返回 `(registry, mcp_manager)` 元组）
- [x] FastAPI lifespan 集成（优雅关闭）
- [x] 依赖安装（`mcp[cli]>=1.0.0` → 安装 v1.26.0）
- [x] 示例配置 `.mcp.json`（docker-lima stdio + tapd streamable-http，默认 disabled）
- [x] 验证：所有 MCP Server disabled 时服务正常启动，7 个内置工具不受影响
- [x] E2E 测试：docker-lima（stdio）— 19 个工具发现并注册，容器列表查询成功
- [x] E2E 测试：tapd（streamable-http）— 3 个工具发现并注册，待办需求查询成功
- [x] 双传输协议同时启用：7 内置 + 22 MCP = 29 个工具，共存无冲突

### Sprint 2：健壮性 ✅

- [x] 连接超时控制：`_connect_server` 包裹 `asyncio.wait_for(CONNECT_TIMEOUT_S=30s)`，单个 Server 超时不阻塞其他
- [x] call_tool 超时可配置化：从 `MCPServerConfig.call_timeout_s`（由 `timeout_ms` 转换）传入 MCPTool，替代硬编码 60s
- [x] Server 状态追踪：新增 `ConnectionStatus` 枚举 + `ServerState` 数据类，记录连接状态/重连次数/最后错误
- [x] 健康监控：`MCPToolManager.ping(server_name)` 通过 `session.send_ping()` 检测连接存活
- [x] `health_check()` 接口：返回所有 Server 的状态摘要（status/tool_count/retry_count/last_error）
- [x] 异常重连：MCPTool 捕获连接类异常（ConnectionError/OSError/EOFError/BrokenPipeError）→ 触发 `reconnect_hook` → Manager 重建连接 → session_resolver 自动切换到新 session
- [x] session 热替换：MCPTool 通过 `session_resolver` 间接获取 session（而非直接持有引用），重连后无需重新注册工具
- [x] 默认常量集中管理：新增 `MCPDefaults` 类，消除魔法数字
- [x] 重连线程安全：`_reconnect_lock` 防止并发重连同一 Server
- [x] 验证：tapd Server 连接成功 + ping OK + health_check 状态正确

### Sprint 3：可观测性 & 增强 ✅

- [x] OTel span 增强：MCPTool `_execute_with_retry` 内新增 `mcp.call_tool.{server}` span，嵌套在已有 `tool.execute.{name}` span 内，携带 mcp.server/transport/tool/timeout/attempt/success/reconnected 等属性
- [x] 前端 MCP 工具来源标识：`parseMCPToolName()` 解析 `mcp__{server}__{tool}` 格式，ToolCard 渲染紫色⚡{server} 徽章，动态生成 ToolMeta
- [x] MCP 管理 API：`GET /api/mcp/status` 查询健康状态，`POST /api/mcp/reload` 热重载配置（断开旧连接→移除旧工具→重新发现注册）
- [x] `ToolRegistry.unregister(name)` 方法：支持移除已注册工具并清理关联别名，供热重载使用
- [ ] SSE transport 支持：暂不实施（MCP SDK 已标记 SSE 为 deprecated，推荐 Streamable HTTP）

## 5. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tools/mcp/__init__.py` | 新建 | 模块导出 |
| `src/tools/mcp/config.py` | 新建 | 配置解析 + MCPDefaults 默认常量 |
| `src/tools/mcp/mcp_tool.py` | 新建 | BaseTool 适配器（含 OTel span、session_resolver、reconnect_hook） |
| `src/tools/mcp/manager.py` | 新建 | 生命周期管理（连接/发现/健康检查/重连/关闭） |
| `src/tools/__init__.py` | 修改 | 增加 MCP 导出 |
| `src/tools/base_tool.py` | 修改 | ToolRegistry 新增 `unregister()` 方法 |
| `src/factory.py` | 修改 | `create_tool_registry` 返回 `(ToolRegistry, Optional[MCPToolManager])` 元组，新增 `_register_mcp_tools()` |
| `src/api/app.py` | 修改 | lifespan 中增加 MCP shutdown，注册 MCP 路由 |
| `src/api/routers/mcp.py` | 新建 | MCP 状态查询和热重载 API（`/api/mcp/status`、`/api/mcp/reload`） |
| `frontend/src/components/chat/ThinkingPanel.tsx` | 修改 | MCP 工具来源标识（紫色徽章 + 动态 ToolMeta） |
| `requirements.txt` | 修改 | 增加 `mcp[cli]` 依赖 |
| `.mcp.json` | 新建 | 示例配置文件 |

## 6. E2E 测试结果

### 测试环境

- 两个 MCP Server 同时启用（`.mcp.json` 中 `disabled: false`）
- 服务启动日志确认：7 内置 + 22 MCP = **29 个工具**注册成功

### 测试场景

| # | 场景 | 传输协议 | MCP Server | 工具发现数 | 调用工具 | 结果 |
|---|------|----------|------------|-----------|---------|------|
| 1 | 列出当前运行的 Docker 容器 | stdio | docker-lima | 19 | `mcp__docker-lima__list_containers`（1 次） | 成功返回 2 个容器信息 |
| 2 | 查 TAPD 上待办需求 | streamable-http | tapd | 3 | `mcp__tapd__lookup_tapd_tool` → `mcp__tapd__proxy_execute_tool`（共 3 次） | 成功返回 23 条待办需求 |

### 关键观测

1. **stdio 传输（docker-lima）**：子进程启动→初始化→list_tools 全链路正常，19 个工具均以 `mcp__docker-lima__xxx` 命名注册
2. **streamable-http 传输（tapd）**：远程连接→初始化→list_tools 正常，3 个代理工具注册（`lookup_tapd_tool`、`lookup_tool_param_schema`、`proxy_execute_tool`）
3. **LLM 工具选择**：LLM 能正确识别并选择 MCP 工具（而非内置工具），工具参数构造正确
4. **TAPD 代理模式**：TAPD MCP 采用 proxy 模式（先 lookup 再 execute），LLM 首次尝试了不存在的工具名后自行修正，符合预期
5. **shutdown 清理**：anyio cancel scope 跨任务关闭警告已降级为 DEBUG 级别，不影响资源释放

## 7. 遗留问题

1. **同步/异步桥接复杂度**：BaseTool.execute() 是同步接口，MCP SDK 是全异步。需要维护独立事件循环线程，增加了复杂度。长期考虑将 BaseTool.execute 改为 async。
2. **工具数量爆炸**：如果多个 MCP Server 各暴露大量工具，会增加 OpenAI tools schema 的 token 开销。可能需要动态工具筛选（按 Skill/场景只注册部分工具）。
3. **安全边界**：MCP 外部工具的执行不经过内置的安全策略（BinaryPolicy），需要依赖 MCP Server 自身的安全机制。
