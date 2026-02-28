# LLM ReAct Agent Demo

一个功能完备的 **ReAct (Reasoning + Acting) Agent** 系统，支持多工具调用、Plan-Execute 规划模式、RAG 知识库、多级记忆、Zone 分层上下文管理、Skill 动态注入、会话持久化和实时思考过程可视化。

## 架构概览

```
┌───────────────────────────────────────────────────────┐
│                      入口层                            │
│     main.py (CLI)  |  server.py (FastAPI + React)     │
├───────────────────────────────────────────────────────┤
│                    API / 服务层                        │
│    FastAPI Routers (SSE) ← AgentService ← Factory     │
├───────────────────────────────────────────────────────┤
│                    Agent 核心层                        │
│   ReActAgent / PlanExecuteAgent                       │
│     ← ContextBuilder ← LoopDetector ← ToolExecutor   │
├──────────────┬─────────────────┬──────────────────────┤
│   LLM 客户端  │     记忆系统     │      工具系统         │
│  OpenAI 兼容  │ 短期+长期+RAG   │   10+ 内置工具       │
├──────────────┼─────────────────┼──────────────────────┤
│   Skill 系统  │   上下文管理     │     安全沙箱          │
│  动态注入/路由 │ Zone分层预算     │  文件/CLI 沙箱       │
├──────────────┴─────────────────┴──────────────────────┤
│                     基础设施层                          │
│   Config (pydantic-settings) | Logger | Retry          │
│   SessionStore (JSON) | VectorStore (ChromaDB)         │
└───────────────────────────────────────────────────────┘
```

## 核心特性

### ReAct 推理循环

- 基于 OpenAI Function Calling 的工具调用机制
- 最大迭代次数可配置（默认 10 次），超限自动总结
- **并发工具执行**：多个 tool_calls 使用 `ThreadPoolExecutor` 并行处理（最多 5 线程），结果按原始顺序写入 Memory
- **循环检测**：通过工具调用 fingerprint 检测连续重复调用，自动引导 LLM 换策略
- **L3 任务偏离检测**：检测 Agent 是否偏离原始任务，自动纠偏

### Plan-Execute 规划模式

- **先规划再执行**：对复杂任务先生成多步骤计划，再逐步执行
- **动态重规划**：执行过程中根据结果动态调整计划
- **步骤状态追踪**：每个步骤有 pending/running/completed/failed 状态
- **前端可视化**：实时展示计划进度、步骤状态和工具调用详情

### 多级记忆系统

| 层级 | 实现 | 说明 |
|------|------|------|
| 短期记忆 | `ConversationMemory` | 维护当前对话上下文，Token 超限时自动摘要压缩 |
| 长期记忆 | `VectorStore` (ChromaDB) | 对话结束后由 LLM 提取关键事实存入向量库，语义检索 |
| 知识库 | `KnowledgeBase` (ChromaDB) | 外部文档导入，支持 txt/md/pdf/xlsx，段落感知分块 |

### Zone 分层上下文构建

解决知识库/长期记忆直接写入对话历史导致的污染、不可截断和缓存前缀破坏问题：

1. **System Zone** — 系统提示词（稳定前缀，缓存友好）
2. **Environment Zone** — 运行时环境信息（当前时间等）
3. **Skill Zone** — 动态注入的 Skill 提示词（有预算上限，支持截断）
4. **Knowledge Zone** — 知识库检索结果（有预算上限，支持截断）
5. **Memory Zone** — 长期记忆检索（有预算上限，支持截断）
6. **History Zone** — 对话历史消息（支持摘要压缩）

每个 Zone 有独立的 Token 预算，超限时自动截断。前端实时展示各 Zone 用量。

### Skill 系统

- **动态注入**：根据用户消息自动匹配和注入相关 Skill
- **预算感知**：Skill 提示词注入受 Zone 预算约束
- **Skill 路由**：基于关键词匹配和语义理解的路由机制
- **自定义 Skill**：支持 Markdown 格式的 Skill 定义（SOP 流程 + 脚本工具）

### 工具系统

| 工具 | 说明 |
|------|------|
| `calculator` | 安全数学计算器（AST 白名单运算符，防代码注入） |
| `get_current_time` | 获取当前日期时间 |
| `web_search` | 网页搜索（DuckDuckGo 免费 / Tavily 付费，策略模式自动选择） |
| `knowledge_search` | 知识库语义检索 |
| `file_reader` | 文件读取 / 目录浏览 / 文件查找 / 内容搜索（5 种 action） |
| `file_writer` | 文件创建 / 覆写 / 追加 / 精确替换（3 种 action） |
| `kubectl` | Kubernetes 集群管理（默认关闭，需配置启用） |
| `docker` | Docker 容器管理（默认关闭，需配置启用） |
| `curl` | HTTP 请求工具（URL 白名单 + 超时控制） |

所有工具返回结构化 `ToolResult`，超长输出自动智能截断（head 60% + tail 20%）。

### 安全机制

- **文件系统沙箱**：多根目录白名单、读写权限分级、路径穿越防御、敏感文件排除（.env, .git 等）、文件大小限制
- **CLI 命令沙箱**：子命令白名单（只读/写分级）、危险参数拦截（--force, --rm 等）、命令注入防御（`shell=False` + shell 元字符检测）、Secret 敏感数据脱敏、超时控制

### Web UI（React + Tailwind CSS）

- **现代化界面**：React 18 + Zustand 状态管理 + Tailwind CSS + Vite 构建
- **暗色/亮色模式**：支持主题切换，localStorage 持久化 + 系统偏好检测
- **多租户隔离**：每个浏览器标签页独立的 Agent 实例和记忆空间
- **多对话管理**：新建 / 切换 / 删除对话，侧边栏显示相对时间
- **会话持久化**：页面刷新自动恢复历史对话（JSON 原子写入）
- **实时思考过程**：通过 SSE 事件流式展示推理过程、工具调用和结果
- **Plan 模式可视化**：实时展示计划步骤进度、工具调用展开详情
- **上下文用量仪表盘**：SVG 圆环 + Zone 详情弹窗，实时监控各 Zone Token 消耗
- **空状态引导**：品牌 Logo + 快捷提示卡片，点击即可发送预设 Prompt
- **消息复制**：hover 消息气泡显示复制按钮，一键复制内容
- **Token 统计**：标签式设计展示输入/输出/总计 Token 和耗时
- **并发可视化**：并发工具调用显示 `⚡ [1/3]` 标记
- **停止功能**：用户可随时中断 Agent 运行
- **Mermaid 图表**：支持 Mermaid 语法渲染流程图、架构图等
- **Markdown 渲染**：完整 Markdown 支持，含代码高亮和表格修复

### 可观测性

- `RunMetrics` 记录每次运行的迭代数、工具调用明细（名称/成功/耗时/错误）、知识库和记忆注入数量
- `AgentEvent` 事件系统支持 6 种事件类型，含并发批次标记

## 项目结构

```
├── main.py                            # CLI 入口
├── server.py                          # FastAPI 服务入口
├── Makefile                           # 构建与运行命令
├── requirements.txt                   # Python 依赖
├── .env.example                       # 环境变量模板
├── frontend/                          # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── chat/
│   │   │   │   ├── ChatView.tsx           # 主聊天视图（空状态引导 + 消息列表）
│   │   │   │   ├── InputBox.tsx           # 输入框（集成上下文指示器）
│   │   │   │   ├── MessageBubble.tsx      # 消息气泡（Token统计 + 复制按钮）
│   │   │   │   ├── ThinkingPanel.tsx      # 思考过程面板（Plan可视化）
│   │   │   │   ├── ContextUsageIndicator.tsx  # 上下文用量仪表盘
│   │   │   │   ├── MarkdownRenderer.tsx   # Markdown 渲染器
│   │   │   │   └── mermaid/              # Mermaid 图表渲染
│   │   │   ├── layout/
│   │   │   │   ├── Header.tsx            # 顶栏（模型名 + 暗色切换）
│   │   │   │   ├── Sidebar.tsx           # 侧边栏（对话列表 + 时间戳）
│   │   │   │   ├── StatusPanel.tsx       # 系统状态面板
│   │   │   │   ├── KnowledgePanel.tsx    # 知识库管理面板
│   │   │   │   └── SkillsPanel.tsx       # Skill 管理面板
│   │   │   └── auth/
│   │   │       └── LoginModal.tsx        # 登录弹窗
│   │   ├── stores/
│   │   │   ├── chatStore.ts             # 聊天状态（上下文追踪 + Plan进度）
│   │   │   ├── uiStore.ts              # UI状态（暗色模式 + 面板控制）
│   │   │   └── ...                     # 其他 store
│   │   └── types/
│   │       ├── conversation.ts          # 对话/Zone类型定义
│   │       └── events.ts               # SSE事件类型定义
│   └── index.html
├── skills/                            # Skill 定义目录
│   ├── k8s-resource-analysis/
│   ├── k8s-troubleshooting/
│   └── skill-creator/
└── src/
    ├── factory.py                     # 组件工厂（分层组装）
    ├── agent/
    │   ├── base_agent.py              # Agent 抽象基类
    │   ├── react_agent.py             # ReAct Agent 核心实现
    │   ├── plan_execute_agent.py      # Plan-Execute Agent 实现
    │   ├── plan.py                    # Plan 数据模型
    │   ├── tool_executor.py           # 工具执行器（并发支持）
    │   ├── events.py                  # 事件系统（Plan模式扩展）
    │   ├── loop_detector.py           # 循环检测 + L3任务偏离检测
    │   └── metrics.py                 # 运行指标
    ├── api/
    │   ├── app.py                     # FastAPI 应用
    │   ├── schemas.py                 # Pydantic 请求/响应模型
    │   ├── dependencies.py            # 依赖注入
    │   └── routers/
    │       ├── chat.py                # 聊天 SSE 接口
    │       ├── session.py             # 会话管理接口
    │       ├── knowledge.py           # 知识库接口
    │       ├── skills.py              # Skill 接口
    │       ├── auth.py                # 认证接口
    │       └── status.py              # 状态查询接口
    ├── config/
    │   └── settings.py                # 配置管理（Zone预算/Skill/Plan等）
    ├── context/
    │   └── builder.py                 # Zone 分层上下文构建器（预算管理 + 截断）
    ├── skills/
    │   ├── base.py                    # Skill 基类
    │   ├── loader.py                  # Skill 加载器
    │   ├── registry.py                # Skill 注册表
    │   └── router.py                  # Skill 路由（关键词匹配）
    ├── llm/
    │   ├── base_client.py             # LLM 客户端抽象 + Message 模型
    │   └── openai_client.py           # OpenAI 兼容协议客户端
    ├── memory/
    │   ├── conversation.py            # 短期对话记忆（Token 截断 + 摘要压缩）
    │   ├── token_counter.py           # Token 计数（tiktoken）
    │   └── vector_store.py            # 长期记忆（ChromaDB 向量存储）
    ├── persistence/
    │   ├── session_store.py           # 会话 JSON 持久化（原子写入）
    │   └── user_store.py              # 用户数据持久化
    ├── services/
    │   ├── agent_service.py           # Agent 服务层（上下文状态查询）
    │   └── auth_service.py            # 认证服务
    ├── rag/
    │   ├── knowledge_base.py          # 知识库管理器
    │   ├── document_loader.py         # 文档加载器（txt/md/pdf）
    │   └── chunker.py                 # 文本分块器（段落感知 + 滑动窗口）
    ├── tools/
    │   ├── base_tool.py               # 工具抽象基类 + ToolRegistry
    │   ├── result.py                  # ToolResult 结构化返回 + 智能截断
    │   ├── calculator.py              # 安全数学计算器
    │   ├── datetime_tool.py           # 日期时间工具
    │   ├── web_search.py              # 网页搜索工具
    │   ├── search_backends.py         # 搜索后端策略（DuckDuckGo / Tavily）
    │   ├── knowledge_search.py        # 知识库检索工具
    │   ├── filesystem/
    │   │   ├── sandbox.py             # 文件系统安全沙箱
    │   │   ├── file_reader.py         # 文件读取 / 搜索工具
    │   │   └── file_writer.py         # 文件写入工具
    │   └── devops/
    │       ├── command_sandbox.py     # CLI 命令安全沙箱
    │       ├── kubectl_tool.py        # Kubernetes 管理工具
    │       ├── docker_tool.py         # Docker 管理工具
    │       └── curl_tool.py           # HTTP 请求工具
    ├── environment/
    │   ├── adapter_base.py            # 环境适配器基类
    │   └── tool_env_adapter.py        # 工具环境适配器
    ├── observability/
    │   └── instruments.py             # 可观测性工具
    └── utils/
        ├── logger.py                  # 日志配置（loguru）
        └── retry.py                   # LLM API 重试策略（tenacity）
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，至少配置 LLM API 相关项：

```env
# LLM 配置（必填）
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
```

支持所有 OpenAI 兼容 API（DeepSeek、通义千问等），只需修改 `LLM_BASE_URL` 和 `LLM_MODEL`。

### 启动

**CLI 模式：**

```bash
python main.py
```

**Web UI 模式（推荐）：**

```bash
# 构建前端 + 启动后端
make run
```

或手动分步：

```bash
# 构建前端
cd frontend && npm install && npm run build && cd ..

# 启动后端（自动托管前端静态文件）
python server.py --reload
```

访问 `http://localhost:8000` 即可使用。

### CLI 交互命令

| 命令 | 说明 |
|------|------|
| `/tools` | 查看已注册的工具列表 |
| `/memory` | 查看当前记忆状态 |
| `/clear` | 清空对话历史 |
| `/import <path>` | 导入知识库文件或目录 |
| `/kb clear` | 清空知识库 |
| `/exit` | 退出 |

## 配置项

### LLM 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `LLM_API_KEY` | _(必填)_ | LLM API 密钥 |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | API 端点 |
| `LLM_MODEL` | `gpt-4o` | 模型名称 |

### Agent 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `AGENT_MAX_ITERATIONS` | `10` | 最大 ReAct 迭代次数 |
| `AGENT_TEMPERATURE` | `0.7` | 生成温度 |
| `AGENT_MAX_TOKENS` | `4096` | 最大生成 token 数 |

### 搜索配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SEARCH_BACKEND` | `auto` | 搜索后端：`auto` / `duckduckgo` / `tavily` |
| `SEARCH_TAVILY_API_KEY` | _(可选)_ | Tavily API Key，`auto` 模式下有此 Key 自动选择 Tavily |

### 文件系统配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `FILESYSTEM_SANDBOX_DIR` | 当前工作目录 | 文件系统沙箱根目录 |
| `FILESYSTEM_ALLOWED_DIRS` | _(空)_ | 额外允许读取的目录（逗号分隔） |
| `FILESYSTEM_WRITABLE_DIRS` | _(空)_ | 额外允许写入的目录（逗号分隔） |
| `FILESYSTEM_EXCLUDE` | `.env,.git,...` | 排除文件模式（逗号分隔） |
| `FILESYSTEM_MAX_FILE_SIZE` | `1048576` | 最大文件大小（字节，默认 1MB） |
| `FILESYSTEM_MAX_DEPTH` | `5` | 目录遍历最大深度 |
| `FILESYSTEM_MAX_RESULTS` | `50` | 搜索最大结果数 |

### DevOps 工具配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `DEVOPS_KUBECTL_ENABLED` | `false` | 启用 kubectl 工具 |
| `DEVOPS_KUBECTL_READ_ONLY` | `true` | kubectl 只读模式（仅 get/describe/logs 等） |
| `DEVOPS_KUBECTL_ALLOWED_NAMESPACES` | _(空)_ | 限制可访问的 namespace（逗号分隔，空则不限制） |
| `DEVOPS_KUBECTL_TIMEOUT` | `30` | kubectl 命令超时（秒） |
| `DEVOPS_DOCKER_ENABLED` | `false` | 启用 docker 工具 |
| `DEVOPS_DOCKER_READ_ONLY` | `true` | docker 只读模式（仅 ps/images/logs 等） |
| `DEVOPS_DOCKER_TIMEOUT` | `30` | docker 命令超时（秒） |

## 依赖

### 后端（Python）

```
openai>=1.12.0           # OpenAI 兼容 API 客户端
fastapi>=0.109.0         # Web 框架
uvicorn>=0.27.0          # ASGI 服务器
pydantic>=2.6.0          # 数据模型
pydantic-settings>=2.1.0 # 配置管理
python-dotenv>=1.0.0     # .env 文件加载
loguru>=0.7.0            # 结构化日志
tiktoken>=0.6.0          # Token 精确计数
chromadb>=0.4.22         # 向量存储（长期记忆 + 知识库）
pymupdf>=1.24.0          # PDF 文档解析
tenacity>=8.2.0          # 重试机制
ddgs>=6.0.0              # DuckDuckGo 搜索
tavily-python>=0.5.0     # Tavily 搜索（可选）
openpyxl>=3.1.0          # Excel 文件解析
```

### 前端

```
React 18 + TypeScript
Zustand（状态管理）
Tailwind CSS（样式）
Vite（构建工具）
react-virtuoso（虚拟滚动）
react-markdown + remark-gfm（Markdown 渲染）
mermaid（图表渲染）
```

## 数据存储

运行时数据存储在 `.agent_data/` 目录下（已加入 `.gitignore`）：

```
.agent_data/
├── knowledge/           # 知识库向量存储（ChromaDB）
├── memory/              # 长期记忆向量存储（按租户隔离）
└── sessions/            # 会话持久化 JSON 文件
```
