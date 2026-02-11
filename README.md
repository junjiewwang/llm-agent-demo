# LLM ReAct Agent Demo

一个功能完备的 **ReAct (Reasoning + Acting) Agent** 系统，支持多工具调用、RAG 知识库、多级记忆、会话持久化和实时思考过程可视化。

## 架构概览

```
┌───────────────────────────────────────────────────────┐
│                      入口层                            │
│           main.py (CLI)  |  web_ui.py (Gradio Web)    │
├───────────────────────────────────────────────────────┤
│                      工厂层                            │
│            src/factory.py — 组件创建与组装              │
├───────────────────────────────────────────────────────┤
│                    Agent 核心层                        │
│   ReActAgent ← ContextBuilder ← LoopDetector ← Metrics│
├──────────────┬─────────────────┬──────────────────────┤
│   LLM 客户端  │     记忆系统     │      工具系统         │
│  OpenAI 兼容  │ 短期+长期+RAG   │    8+ 内置工具        │
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
3. **Inject Zone** — 知识库检索 + 长期记忆（临时注入，不持久化）
4. **History Zone** — 对话历史消息

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

所有工具返回结构化 `ToolResult`，超长输出自动智能截断（head 60% + tail 20%）。

### 安全机制

- **文件系统沙箱**：多根目录白名单、读写权限分级、路径穿越防御、敏感文件排除（.env, .git 等）、文件大小限制
- **CLI 命令沙箱**：子命令白名单（只读/写分级）、危险参数拦截（--force, --rm 等）、命令注入防御（`shell=False` + shell 元字符检测）、Secret 敏感数据脱敏、超时控制

### Web UI

- **多租户隔离**：每个浏览器标签页独立的 Agent 实例和记忆空间
- **多对话管理**：新建 / 切换 / 删除对话
- **会话持久化**：页面刷新自动恢复历史对话（JSON 原子写入）
- **实时思考过程**：通过事件回调 + Queue 流式展示推理过程、工具调用和结果
- **并发可视化**：并发工具调用显示 `⚡ [1/3]` 标记
- **停止功能**：用户可随时中断 Agent 运行
- **知识库管理**：支持文件上传导入和清空
- **Markdown 表格修复**：自动处理 LLM 生成的含空行表格的渲染问题

### 可观测性

- `RunMetrics` 记录每次运行的迭代数、工具调用明细（名称/成功/耗时/错误）、知识库和记忆注入数量
- `AgentEvent` 事件系统支持 6 种事件类型，含并发批次标记

## 项目结构

```
├── main.py                            # CLI 入口
├── web_ui.py                          # Gradio Web UI 入口
├── requirements.txt                   # Python 依赖
├── .env.example                       # 环境变量模板
└── src/
    ├── factory.py                     # 组件工厂（分层组装）
    ├── agent/
    │   ├── base_agent.py              # Agent 抽象基类
    │   ├── react_agent.py             # ReAct Agent 核心实现
    │   ├── events.py                  # 事件系统（思考过程可视化）
    │   ├── loop_detector.py           # 工具调用循环检测
    │   └── metrics.py                 # 运行指标
    ├── config/
    │   └── settings.py                # 配置管理（pydantic-settings）
    ├── context/
    │   └── builder.py                 # Zone 分层上下文构建器
    ├── llm/
    │   ├── base_client.py             # LLM 客户端抽象 + Message 模型
    │   └── openai_client.py           # OpenAI 兼容协议客户端
    ├── memory/
    │   ├── conversation.py            # 短期对话记忆（Token 截断 + 摘要压缩）
    │   ├── token_counter.py           # Token 计数（tiktoken）
    │   └── vector_store.py            # 长期记忆（ChromaDB 向量存储）
    ├── persistence/
    │   └── session_store.py           # 会话 JSON 持久化（原子写入）
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
    │       └── docker_tool.py         # Docker 管理工具
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

**Web UI 模式：**

```bash
python web_ui.py
```

访问 `http://localhost:7860` 即可使用。

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

```
openai>=1.12.0           # OpenAI 兼容 API 客户端
pydantic>=2.6.0          # 数据模型
pydantic-settings>=2.1.0 # 配置管理
python-dotenv>=1.0.0     # .env 文件加载
loguru>=0.7.0            # 结构化日志
tiktoken>=0.6.0          # Token 精确计数
chromadb>=0.4.22         # 向量存储（长期记忆 + 知识库）
pymupdf>=1.24.0          # PDF 文档解析
gradio>=4.19.0           # Web UI 框架
tenacity>=8.2.0          # 重试机制
ddgs>=6.0.0              # DuckDuckGo 搜索
tavily-python>=0.5.0     # Tavily 搜索（可选）
openpyxl>=3.1.0          # Excel 文件解析
```

## 数据存储

运行时数据存储在 `.agent_data/` 目录下（已加入 `.gitignore`）：

```
.agent_data/
├── knowledge/           # 知识库向量存储（ChromaDB）
├── memory/              # 长期记忆向量存储（按租户隔离）
└── sessions/            # 会话持久化 JSON 文件
```
