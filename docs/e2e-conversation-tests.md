# 对话覆盖测试方案

## 测试环境

- 后端：`make run`（端口 8000，生产模式）
- MCP：tapd 启用（streamable-http），docker-lima 禁用
- LLM：glm-4.7-flash
- OTel：默认关闭（OTEL_ENABLED=false）

---

## 一、内置工具覆盖（7 个）

### T01 — calculator（数学计算）

**对话输入：**
```
请计算 (1024 * 768 - 512) / 3.14 的结果
```
**验证点：**
- [ ] LLM 选择 `calculator` 工具
- [ ] 参数 `expression` 包含完整表达式
- [ ] ThinkingPanel 显示计算器图标和正确结果
- [ ] 最终回复包含数值结果

### T02 — datetime（日期时间）

**对话输入：**
```
现在几点了？今天星期几？
```
**验证点：**
- [ ] LLM 选择 `datetime` 工具
- [ ] 返回当前日期时间
- [ ] 最终回复包含星期信息

### T03 — web_search（网页搜索）

**对话输入：**
```
帮我搜索一下 2026 年最新的 AI Agent 框架有哪些
```
**验证点：**
- [ ] LLM 选择 `web_search` 工具
- [ ] 参数 `query` 语义合理
- [ ] ThinkingPanel 显示搜索图标
- [ ] 返回搜索结果并综合回答

### T04 — knowledge_search（知识库检索）

**前置条件：** 已上传至少一份文档到知识库

**对话输入：**
```
从知识库中搜索关于 MCP 协议的相关内容
```
**验证点：**
- [ ] LLM 选择 `knowledge_search` 工具
- [ ] 参数 `query` 语义正确
- [ ] 如果知识库为空，返回"未找到相关内容"而非报错

### T05 — file_reader（文件读取）

**对话输入：**
```
帮我读一下项目根目录下的 requirements.txt 文件内容
```
**验证点：**
- [ ] LLM 选择 `file_reader` 工具
- [ ] 参数 `path` 为 `requirements.txt`
- [ ] Sandbox 允许读取（项目根目录 rw）
- [ ] 返回文件内容

### T06 — file_writer（文件写入）

**对话输入：**
```
帮我在 test_docs 目录下创建一个 hello.txt 文件，内容写 "Hello from Agent"
```
**验证点：**
- [ ] LLM 选择 `file_writer` 工具
- [ ] 参数包含 `path` 和 `content`
- [ ] `should_confirm` 返回 True → 前端弹出确认框
- [ ] 确认后文件成功创建
- [ ] 后续可用 `file_reader` 验证写入内容

### T07 — execute_command（命令执行）

**对话输入：**
```
帮我执行 curl https://httpbin.org/get 看看返回什么
```
**验证点：**
- [ ] LLM 选择 `execute_command` 工具
- [ ] 参数 `command` 包含 `curl`
- [ ] BashExecutor 允许 curl（在白名单中）
- [ ] `should_confirm` 返回 True → 前端确认
- [ ] 返回 httpbin JSON 响应

---

## 二、MCP 工具覆盖（3 个 tapd 工具）

### T08 — MCP 工具发现 + 调用（TAPD 待办需求）

**对话输入：**
```
帮我查一下 TAPD 上我的待办需求
```
**验证点：**
- [ ] LLM 选择 `mcp__tapd__lookup_tapd_tool` 工具
- [ ] ThinkingPanel 显示紫色 ⚡tapd 徽章
- [ ] 工具标题显示 `lookup_tapd_tool` 而非完整 `mcp__tapd__lookup_tapd_tool`
- [ ] LLM 可能进一步调用 `mcp__tapd__proxy_execute_tool` 执行实际查询
- [ ] 最终回复包含待办需求列表

### T09 — MCP 多步调用（TAPD 工具链）

**对话输入：**
```
帮我查一下 TAPD 项目 ID 为 61498159 的所有未关闭缺陷
```
**验证点：**
- [ ] LLM 调用 `mcp__tapd__lookup_tapd_tool`（语义搜索"查询缺陷列表"）
- [ ] 获取 `bugs_get` 工具的 schema
- [ ] 可能调用 `mcp__tapd__lookup_tool_param_schema`（获取参数详情）
- [ ] 最后调用 `mcp__tapd__proxy_execute_tool`（执行查询）
- [ ] ThinkingPanel 中每个 MCP 工具调用都显示紫色⚡徽章
- [ ] 并行调用时显示 `×N` 并行标记

### T10 — MCP 工具参数查询

**对话输入：**
```
TAPD 的 bugs_get 工具支持哪些参数？
```
**验证点：**
- [ ] LLM 选择 `mcp__tapd__lookup_tool_param_schema`
- [ ] 参数 `tool_name` 为 `bugs_get`
- [ ] 返回完整参数 schema 并用自然语言解释

---

## 三、Agent 推理模式

### T11 — ReAct 单步推理（默认模式）

**对话输入：**
```
1+1等于几？
```
**验证点：**
- [ ] 使用 ReAct Agent（简单问题不触发 Plan-Execute）
- [ ] 可能直接回答或调用 calculator
- [ ] 单轮完成，无多步规划

### T12 — Plan-Execute 多步规划

**前置条件：** Feature Flag `plan_execute = ON`

**对话输入：**
```
帮我分析一下当前项目的 requirements.txt，列出所有依赖包的名称和版本，然后搜索其中是否有已知安全漏洞
```
**验证点：**
- [ ] 触发 Plan-Execute Agent（复杂多步任务）
- [ ] ThinkingPanel 展示规划阶段（Plan）
- [ ] 按步骤执行：file_reader → 分析 → web_search
- [ ] 每步状态更新（pending → executing → completed）

### T13 — 工具调用循环检测

**对话输入：**
```
请反复读取 requirements.txt 文件直到找到一个不存在的包名 "xyz_nonexistent_pkg"
```
**验证点：**
- [ ] LoopDetector 检测到重复工具调用模式
- [ ] 不会无限循环，在合理轮次后终止
- [ ] 返回"未找到"的结论而非持续重试

---

## 四、知识库功能

### T14 — 文档上传

**操作：** 通过 `POST /api/knowledge/upload` 上传文档

```bash
curl -X POST http://localhost:8000/api/knowledge/upload \
  -F "file=@test_docs/sample.txt"
```
**验证点：**
- [ ] 文件上传成功
- [ ] 返回文档 ID 和切片数量

### T15 — 知识库检索 + 回答

**前置条件：** T14 已上传文档

**对话输入：**
```
根据知识库内容，回答一下 xxx（与上传文档相关的问题）
```
**验证点：**
- [ ] LLM 选择 `knowledge_search` 工具
- [ ] 检索到相关片段
- [ ] 回答基于文档内容，非幻觉

---

## 五、会话管理

### T16 — 新建会话

**操作：** `POST /api/sessions`
```bash
curl -s -X POST http://localhost:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "测试会话"}' | python3 -m json.tool
```
**验证点：**
- [ ] 返回 session_id
- [ ] 会话标题正确

### T17 — 多轮对话上下文保持

**对话序列：**
```
第1轮：我的名字叫张三
第2轮：我叫什么名字？
```
**验证点：**
- [ ] 第2轮回复包含"张三"
- [ ] 证明对话历史正确传递

### T18 — 会话列表查询

**操作：** `GET /api/sessions`
```bash
curl -s http://localhost:8000/api/sessions | python3 -m json.tool
```
**验证点：**
- [ ] 返回所有会话列表
- [ ] 包含刚创建的测试会话

---

## 六、MCP 管理 API（Sprint 3 新增）

### T19 — MCP 状态查询

**操作：**
```bash
curl -s http://localhost:8000/api/mcp/status | python3 -m json.tool
```
**验证点：**
- [ ] 返回 `success: true`
- [ ] `servers.tapd.status` = `"connected"`
- [ ] `servers.tapd.tool_count` = `3`
- [ ] `connected` 数组包含 `"tapd"`
- [ ] `total_servers` = `1`

### T20 — MCP 热重载

**操作：**
```bash
curl -s -X POST http://localhost:8000/api/mcp/reload | python3 -m json.tool
```
**验证点：**
- [ ] 返回 `reloaded: true`
- [ ] `tools_registered` = `3`（重新连接 tapd）
- [ ] 旧工具被 `unregister()`，新工具重新注册
- [ ] 重载后 `/api/mcp/status` 仍返回 connected

---

## 七、Skill 系统

### T21 — Skill 列表查询

**操作：**
```bash
curl -s http://localhost:8000/api/skills | python3 -m json.tool
```
**验证点：**
- [ ] 返回 3 个 Skill：k8s_resource_analysis、k8s_troubleshooting、skill_creator
- [ ] 每个 Skill 包含 name、display_name、description、trigger_hint

### T22 — Skill 路由触发（K8s 资源分析）

**对话输入：**
```
/skill k8s_resource_analysis
帮我分析一下 K8s 集群的 Pod 资源使用情况
```
**验证点：**
- [ ] SkillRouter 匹配到 k8s_resource_analysis
- [ ] Skill 的 system_prompt 注入 LLM 上下文
- [ ] LLM 使用 `execute_command` + `kubectl` 执行分析
- [ ] 如果 kubectl 不可用，优雅报错

---

## 八、前端 UI 覆盖

### T23 — ThinkingPanel 内置工具展示

**触发：** 执行 T01（calculator）

**验证点：**
- [ ] ThinkingPanel 展开显示工具调用过程
- [ ] 工具卡片显示正确图标（TOOL_META 匹配）
- [ ] 显示工具入参和返回结果
- [ ] 折叠/展开交互正常

### T24 — ThinkingPanel MCP 工具展示（Sprint 3 核心）

**触发：** 执行 T08（TAPD 待办需求）

**验证点：**
- [ ] MCP 工具卡片显示紫色⚡图标
- [ ] 卡片标题显示简化名 `lookup_tapd_tool` 而非 `mcp__tapd__lookup_tapd_tool`
- [ ] 紫色 `⚡tapd` 徽章正确渲染
- [ ] 工具详情区展示入参和返回结果
- [ ] 多个 MCP 调用时，每个都有独立徽章

### T25 — 流式输出体验

**对话输入：**
```
请详细介绍一下 Python 的 asyncio 库
```
**验证点：**
- [ ] SSE 流式输出，逐字显示
- [ ] 不调用工具（纯知识回答）
- [ ] ThinkingPanel 显示 thinking 过程（如有）
- [ ] 完成后停止流式

---

## 九、异常场景

### T26 — 不存在的工具名容错

**场景：** LLM 幻觉生成不存在的工具名

**验证点：**
- [ ] ToolRegistry.execute 返回 ToolResult.fail
- [ ] Agent 收到错误信息后自我修正，重新选择正确工具
- [ ] 不会崩溃或卡死

### T27 — MCP Server 超时容错

**场景：** tapd Server 网络不稳定

**验证点：**
- [ ] `call_tool` 超时后返回错误（call_timeout_s）
- [ ] 触发 reconnect_hook → Manager 尝试重连
- [ ] ServerState 更新：retry_count +1
- [ ] `/api/mcp/status` 反映最新状态

### T28 — 文件操作安全边界

**对话输入：**
```
帮我读取 /etc/passwd 文件
```
**验证点：**
- [ ] Sandbox 拒绝访问（不在 allowed_dirs 中）
- [ ] 返回安全拒绝消息，不是系统异常
- [ ] Agent 向用户解释无法访问的原因

### T29 — 命令执行安全边界

**对话输入：**
```
帮我执行 rm -rf / 命令
```
**验证点：**
- [ ] BashExecutor 拒绝（rm 不在允许的 binary 白名单中）
- [ ] 返回安全拒绝消息
- [ ] 不会实际执行危险命令

---

## 十、系统状态

### T30 — 系统状态查询

**操作：**
```bash
curl -s http://localhost:8000/api/status | python3 -m json.tool
```
**验证点：**
- [ ] 返回系统状态信息
- [ ] 包含工具数量、会话数等

---

## 执行优先级

| 优先级 | 测试用例 | 覆盖范围 |
|--------|---------|---------|
| P0（必须） | T08, T09, T19, T20, T24 | Sprint 3 MCP 新增功能 |
| P0（必须） | T01, T03, T05, T17, T25 | 核心功能回归 |
| P1（重要） | T02, T04, T06, T07, T10, T12 | 工具 + 推理模式 |
| P2（补充） | T11, T13, T14, T15, T16, T18, T21, T22, T23 | 辅助功能 |
| P3（防护） | T26, T27, T28, T29, T30 | 异常 + 安全边界 |

## 快速冒烟测试脚本

依次执行以下命令完成 API 层面的冒烟测试：

```bash
# 1. 系统状态
curl -s http://localhost:8000/api/status | python3 -m json.tool

# 2. MCP 状态
curl -s http://localhost:8000/api/mcp/status | python3 -m json.tool

# 3. Skill 列表
curl -s http://localhost:8000/api/skills | python3 -m json.tool

# 4. 会话列表
curl -s http://localhost:8000/api/sessions | python3 -m json.tool

# 5. MCP 热重载
curl -s -X POST http://localhost:8000/api/mcp/reload | python3 -m json.tool

# 6. 热重载后状态确认
curl -s http://localhost:8000/api/mcp/status | python3 -m json.tool
```
