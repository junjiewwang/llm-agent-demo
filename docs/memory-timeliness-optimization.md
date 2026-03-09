# 记忆时效性优化

## 1. 问题背景

### 现象

用户提问"请帮我看一下各种 tapd 单的状态"时，Agent 只有 1 轮思考且**没有调用任何 MCP 工具**，直接从上下文（长期记忆 + 对话历史）中复用了旧的 TAPD 查询结果并输出回答。

### 根因分析

**记忆的"时效性盲区"**：当前系统存在 3 个时效性问题：

| 问题 | 说明 | 影响 |
|------|------|------|
| 记忆无时间标签 | `_extract_key_facts` 提取的事实没有时间戳标注 | LLM 无法判断数据新鲜度 |
| 记忆无"易变性"分类 | TAPD 单状态是高度时变数据（分钟级变化），但被当做普通事实存储 | 时变数据被当做永久事实使用 |
| LLM 无"必须刷新"指引 | System Prompt 未告知 LLM 对状态查询类问题应重新查询 | LLM 倾向于复用已有信息 |

### Governor 衰减不解决此问题

Governor 的半衰期 14 天衰减是针对**信息价值**，不是**信息鲜度**。TAPD 状态记忆因频繁被命中，score 反而持续走高，永远不会被驱逐。

---

## 2. 实施方案

### 方案 C（快速见效）：System Prompt 策略引导 ✅

**目标**：让 LLM 对状态查询类请求强制使用工具刷新

- [x] 在 System Prompt 中增加"时效性原则"规则段
  - 改动文件：`src/factory.py`（`SYSTEM_PROMPT` 常量）
  - 具体内容：新增"时效性原则（严格遵守）"段，要求查看/查询/检查/获取状态类请求必须调用工具获取最新数据，历史记忆仅供参考

### 方案 A-1：记忆提取时自动添加时间戳标签 ✅

**目标**：让记忆自带采集时间，LLM 可判断数据新鲜度

- [x] `_store_to_long_term_memory` 存储时自动添加日期前缀 `[YYYY-MM-DD]`
  - 改动文件：`src/agent/react_agent.py`（`_store_to_long_term_memory` 方法）
  - 同时回退模式（LLM 提取失败时）也添加日期前缀
  - 新增 `collected_at` 时间戳到 metadata

### 方案 A-2：记忆注入时标注采集时间 + 时效性提示 ✅

**目标**：注入上下文的记忆明确标注来源时间和时效性警告

- [x] `set_memory` 注入时增加时效性警告头部
- [x] 每条记忆附带 metadata 中的 `collected_at` 转换为可读日期 `(采集于 YYYY-MM-DD)`
  - 改动文件：`src/context/builder.py`（`set_memory` 方法）
  - 头部内容：`⚠️ 以下为历史记忆，仅供参考。对于状态、列表、实时数据等时变信息，请务必调用工具获取最新数据`

### 方案 A-3：时变类记忆设置短 TTL ✅

**目标**：时变数据不应长期驻留在记忆库中

- [x] `_extract_key_facts` 增加 volatility 判断：LLM 同时输出 `VOLATILE`/`STABLE` 标签 + 事实内容
- [x] 时变类记忆 TTL = 1 天（`metadata["ttl"] = time.time() + 86400`），稳定类使用 VectorStore 默认 TTL
  - 改动文件：`src/agent/react_agent.py`（`_extract_key_facts`、`_store_to_long_term_memory`）
  - `_extract_key_facts` 返回类型从 `Optional[str]` 改为 `Optional[dict]`，含 `facts` 和 `volatile` 字段

---

## 3. 涉及改动文件

| 文件 | 改动说明 |
|------|---------|
| `src/factory.py` | System Prompt 增加"时效性原则"规则段 |
| `src/agent/react_agent.py` | `_extract_key_facts` 增加 volatility 判断（返回 dict）；`_store_to_long_term_memory` 添加日期前缀、collected_at 时间戳、时变记忆短 TTL |
| `src/context/builder.py` | `set_memory` 增加时效性警告头部、每条记忆标注采集日期 |

---

## 4. 遗留问题

- （暂无）
