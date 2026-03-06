# 对话历史上下文优化方案分析

## 1. 问题诊断

### 现象

| 指标 | 新用户（无历史） | 老用户（有历史） | 差距 |
|------|----------------|----------------|------|
| input_tokens | 18,527 | 51,632 | **2.8x** |
| 总耗时 | 55s | 400s | **7.3x** |
| LLM 推理质量 | 并行 3 路查询（需求+缺陷+任务） | 仅查需求 | 质量下降 |
| history_tokens | 2,834 | 13,606 | **4.8x** |

### 根因

当前 History Zone 是**全量线性堆叠**模式：

```
[System] + [Environment] + [Skill] + [KB] + [Memory]
                           ↓
          [History: msg1, msg2, ..., msg40]  ← 全量带给 LLM
```

即使有 `max_messages=40` 条数限制和 `compress()` 摘要机制，但：
1. 40 条消息中每条可能含**大量工具调用返回**（TAPD 返回 30 条需求的完整 JSON）
2. 压缩触发水位线是 `history_budget * 0.8`，在未触发前所有历史全量输入
3. 即使触发压缩，也只压缩前半部分，后半部分原样保留
4. **工具返回是 token 大户** — 一次 MCP 调用返回可能 3000+ tokens

### 核心问题

> **对话历史中大量信息与当前提问无关，但全部作为上下文输入给了 LLM。**

这与 Skill 的按需加载形成鲜明对比 — Skill 通过 `SkillRouter.match()` 只注入相关的 Skill prompt，而 History 却不加区分地全量注入。

---

## 2. 现有项目的上下文机制（优势与不足）

### 已有的优化机制

| 机制 | 作用 | 局限 |
|------|------|------|
| Zone 分层预算 | Skill/KB/Memory 各有预算上限 | History Zone 独占剩余空间，无精细化管理 |
| max_messages=40 | 消息条数硬上限 | 不按 token 控制，40 条含大量工具返回仍然很大 |
| compress() 摘要 | 压缩前半部分为结构化摘要 | 触发滞后（80% 水位线），后半部分不压缩 |
| Scratchpad 隔离 | Plan-Execute 步骤级隔离 | 仅限 Plan-Execute 模式，ReAct 无此机制 |
| 长期记忆 | 关键事实萃取到 VectorStore | 对话间知识传递，但不减少单次对话的历史量 |
| ToolResult 截断 | 工具返回最大 2000 字符 | 已在做，但 40 条 × 2000 仍然很大 |

### 关键不足

1. **History Zone 无"按需检索"机制** — 不像 KB 和 Memory 通过向量检索只取相关片段
2. **工具调用消息不分级** — 完整的 assistant(tool_calls) + tool(result) 对全量保留
3. **压缩策略过于保守** — 只压缩前半，不做渐进式多级压缩
4. **无跨会话复用** — 每个会话独立维护历史，相同问题不同会话会重复查询

---

## 3. 业界方案全景

### 3.1 MemGPT / Letta — 分层虚拟内存架构

**核心思想**：借鉴操作系统的虚拟内存，将 Agent 记忆分为三层：

```
┌─────────────────────────────────────────┐
│ Main Context (Working Memory)           │  ← 固定大小的"内存"
│  = System + 最近 N 条 + 当前任务上下文    │     LLM 直接可见
├─────────────────────────────────────────┤
│ Recall Storage (Conversation Archive)   │  ← 全量对话历史
│  向量索引 + 时间戳 + 关键词              │     按需检索加载
├─────────────────────────────────────────┤
│ Archival Storage (Long-term Knowledge)  │  ← 结构化知识
│  事实、偏好、长期记忆                    │     永久持久化
└─────────────────────────────────────────┘
```

**关键机制**：
- Agent 有 `conversation_search(query)` 和 `archival_search(query)` 两个内置工具
- LLM **自主决定**何时搜索历史（而非全量注入）
- 工作记忆固定大小，超出时自动将最旧消息"换出"到 Recall Storage

**与我们项目的映射**：
- Main Context ≈ 当前 History Zone
- Recall Storage ≈ **缺失**（这是我们的核心短板）
- Archival Storage ≈ 当前 VectorStore 长期记忆

### 3.2 LangGraph / LangChain — 检索增强对话历史

**方案 A：Conversation Summary Buffer Memory**
```
最近 K 条消息保留原样 + 更早的消息压缩为摘要
= [Summary of msgs 1..N-K] + [msg N-K+1, ..., msg N]
```

**方案 B：Vector Store-Backed Memory**
```
所有消息存入向量库 → 每次对话检索 top-K 相关消息注入
= [Retrieved msg A] + [Retrieved msg C] + [Recent msg N-1, N]
```

**方案 C：Entity Memory**
```
从对话中提取实体及其属性 → 维护实体知识图谱 → 注入相关实体上下文
= "用户张三的偏好: xxx, 上次任务: yyy"
```

### 3.3 Anthropic Claude — 对话摘要 + 精细化保留

Claude 的 Projects 功能采用的策略：
- **最近窗口**：保留最近 N 条完整消息
- **远期摘要**：更早的对话由模型生成结构化摘要
- **工具结果精简**：工具返回只保留**结论性信息**，不保留原始数据

### 3.4 Microsoft AutoGen — 消息转换管道

```
对话历史 → [Transform Pipeline] → 精简历史
                │
                ├── MessageTokenLimiter    — Token 总量限制
                ├── MessageHistoryLimiter   — 条数限制
                ├── TextMessageCompressor  — 单条消息内容压缩
                └── Custom Transforms      — 自定义过滤
```

### 3.5 Google Gemini — 缓存 + 精细化上下文

- **Context Caching**：重复的上下文前缀只计算一次，后续请求复用缓存
- **Grounding**：通过 Google Search 实时检索替代历史积累

---

## 4. 推荐方案：分层对话记忆（Tiered Conversation Memory）

### 4.1 架构设计

```
用户输入："请帮我看一下我当前的 tapd 单状态"
    │
    ├── ① 从 Working Memory 取最近 K 条（如 6 条）  ── 完整保留
    │
    ├── ② 从 Conversation Archive 检索相关历史片段   ── 向量检索
    │       query = 用户输入
    │       返回 top-3 相关的历史交互摘要
    │
    ├── ③ Session Summary 注入                      ── 当前会话概要
    │       "本会话已讨论：MCP 配置、Docker 容器查询..."
    │
    └── ④ 已有的 KB + 长期记忆检索                   ── 不变
```

### 4.2 与现有架构的融合

```
┌──────────────────────────────────────────────────┐
│ System Zone       — system prompt                │  不变
├──────────────────────────────────────────────────┤
│ Environment Zone  — 运行时环境信息                │  不变
├──────────────────────────────────────────────────┤
│ Skill Zone        — 领域专家 prompt（按需注入）    │  不变
├──────────────────────────────────────────────────┤
│ Knowledge Zone    — KB 检索结果                   │  不变
├──────────────────────────────────────────────────┤
│ Memory Zone       — 长期记忆 + 会话摘要           │  ← 扩展
├──────────────────────────────────────────────────┤
│ History Zone 改造:                                │
│  ├── Session Summary（当前会话概要, ~200 tokens）  │  ← 新增
│  ├── Retrieved History（检索的相关片段, ~800 tok） │  ← 新增
│  └── Recent Window（最近 K 条完整消息）            │  ← 缩小
└──────────────────────────────────────────────────┘
```

### 4.3 核心组件设计

#### 组件 1：ConversationArchive（对话归档库）

```python
class ConversationArchive:
    """对话历史的向量化归档存储。

    每当消息被"换出"工作记忆时，生成摘要并存入向量库。
    支持按语义检索历史交互片段。
    """

    def archive(self, messages: List[Message]) -> str:
        """将一组消息（通常是一个完整的 Q&A 交互）归档。

        1. 用 LLM 生成交互摘要（~100 tokens）
        2. 存入 ChromaDB，metadata 含 session_id、timestamp、topic
        3. 返回 archive_id
        """

    def search(self, query: str, top_k: int = 3) -> List[ArchivedInteraction]:
        """按语义检索相关的历史交互摘要。"""

    def get_session_summary(self, session_id: str) -> str:
        """获取指定会话的整体摘要（由最近一次归档时累积更新）。"""
```

#### 组件 2：HistoryManager（历史管理器）

```python
class HistoryManager:
    """分层历史管理：Recent Window + Retrieved Archive + Session Summary。

    替代当前 ConversationMemory 的全量线性历史。
    """

    def __init__(self, recent_window_size: int = 6):
        self.recent_window_size = recent_window_size
        self.archive = ConversationArchive()

    def build_history_context(
        self, user_query: str, full_messages: List[Message]
    ) -> List[Message]:
        """构建分层历史上下文。

        Returns:
            [Session Summary MSG] + [Retrieved History MSGs] + [Recent K MSGs]
        """
        # 1. Session Summary（~200 tokens）
        summary_msg = self._build_session_summary()

        # 2. Retrieved History（语义检索 top-3, ~800 tokens）
        retrieved = self.archive.search(user_query, top_k=3)
        retrieved_msgs = self._format_retrieved(retrieved)

        # 3. Recent Window（最近 K 条完整消息）
        recent = full_messages[-self.recent_window_size:]

        return [summary_msg] + retrieved_msgs + recent
```

#### 组件 3：工具结果精简器

```python
class ToolResultCompactor:
    """在消息离开 Recent Window 进入 Archive 前，精简工具返回内容。

    策略：
    - 成功的工具调用：只保留结论（"查询到 30 条需求，3 条缺陷"）
    - 失败的工具调用：保留错误信息
    - 大数据返回：提取 schema + 行数 + 前 3 条样例
    """
```

### 4.4 消息生命周期

```
消息诞生 ──→ Recent Window (完整) ──→ 被挤出时 ──→ Archive (摘要化)
                   │                                    │
                   │ LLM 直接可见                        │ 按需检索
                   │ 保留完整 tool_calls/result           │ 只保留交互摘要
                   │ 最近 6 条                           │ 向量索引
                   │                                    │
                   └── 当前对话窗口 ──────────────────────┘
```

### 4.5 预期效果

| 指标 | 当前（全量） | 优化后（分层） | 改善 |
|------|------------|--------------|------|
| History tokens | 13,606 | ~2,500 | **-82%** |
| Total input_tokens | 51,632 | ~22,000 | **-57%** |
| LLM 推理耗时 | 400s | ~80s | **-80%** |
| 推理质量 | 被历史噪音干扰 | 聚焦当前问题 | 提升 |
| 每轮费用 | 高 | 低 | 节省 |

---

## 5. 实施计划

### Sprint 1：工具结果精简（低成本高收益）

**目标**：减少 History Zone 中工具返回的 token 占比

- [ ] `ConversationMemory` 新增 `compact_tool_results()` 方法
  - 当消息超过 `recent_window_size` 时，自动精简旧的 tool result 消息
  - 策略：成功 → `"[工具 {name} 执行成功，返回 {N} 条结果]"`；失败 → 保留错误
- [ ] `ContextBuilder.build()` 中 History Phase 对非 Recent Window 的 tool result 自动截断
- [ ] 配置项：`AGENT_RECENT_WINDOW_SIZE=6`

**预期收益**：history_tokens 下降 50-60%，零架构改动

### Sprint 2：对话归档 + 语义检索（核心能力）

**目标**：实现 Recent Window + Archive 分层架构

- [ ] 新建 `src/memory/conversation_archive.py` — ChromaDB collection `conversation_archive`
- [ ] 归档触发：消息被挤出 Recent Window 时，每个 Q&A 交互对生成摘要后存入
- [ ] 检索注入：`ContextBuilder` 新增 Archive Zone（在 History Zone 前）
- [ ] 预算调整：Archive Zone 分配 5% budget，History Zone 缩小为 Recent Window

**预期收益**：history_tokens 再下降 60-70%，支持跨轮次相关信息召回

### Sprint 3：Session Summary + 增量更新（体验优化）

**目标**：为长会话提供全局概要

- [ ] 会话级摘要：每 N 轮交互（或每次归档时）增量更新当前会话的 summary
- [ ] Summary 注入：作为 SYSTEM 消息 `[当前会话概要] ...` 注入 History Zone 头部
- [ ] 渐进式压缩：摘要 → 归档 → 驱逐，三级递进

**预期收益**：即使 100+ 轮对话，LLM 仍有全局视野

---

## 6. 方案对比矩阵

| 方案 | 复杂度 | Token 节省 | 信息保真度 | 实施建议 |
|------|--------|-----------|-----------|---------|
| A. 增大压缩频率 | 低 | 30% | 中（摘要丢信息） | 临时缓解 |
| B. 工具结果精简 | 低 | 50-60% | 高（只精简数据） | **Sprint 1 推荐** |
| C. Recent Window + Archive | 中 | 70-80% | 高（语义检索） | **Sprint 2 核心** |
| D. MemGPT 自主检索 | 高 | 80%+ | 最高 | 长期演进方向 |
| E. 实体记忆 / 知识图谱 | 很高 | 取决于实现 | 最高 | 研究型 |

---

## 7. 与现有代码的集成点

| 现有组件 | 改动范围 | 说明 |
|----------|---------|------|
| `ConversationMemory` | 扩展 | 新增 `compact_tool_results()`、`archive_old_messages()` |
| `ContextBuilder` | 扩展 | 新增 Archive Zone，调整 History Zone 预算 |
| `VectorStore` | 复用 | Archive 复用相同的 ChromaDB 基础设施 |
| `ReActAgent` | 小改 | 迭代前调用 `history_manager.build_history_context()` |
| `settings.py` | 扩展 | 新增 `recent_window_size`、`archive_zone_max_ratio` |
| `factory.py` | 扩展 | 初始化 `ConversationArchive` |

---

## 8. 总结

**核心观点**：对话历史应该从"全量线性堆叠"升级为"分层按需检索"，与项目已有的 Skill 按需加载、KB 语义检索保持一致的设计哲学。

**推荐路径**：
1. **Sprint 1（1-2 天）**：工具结果精简 — 低成本高收益的速赢
2. **Sprint 2（3-5 天）**：对话归档 + 语义检索 — 架构级解决
3. **Sprint 3（2-3 天）**：Session Summary — 体验打磨

**长期方向**：向 MemGPT 模式演进 — Agent 自主决定何时检索历史，实现真正的"虚拟无限记忆"。
