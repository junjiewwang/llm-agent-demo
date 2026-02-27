# Skill 写作方法论

本文档提炼了高质量 Skill 的写作模式和最佳实践，帮助你编写出更有效的 SKILL.md。

---

## 目录

1. [核心哲学：Explain the Why](#核心哲学explain-the-why)
2. [渐进式披露](#渐进式披露)
3. [写作模式](#写作模式)
4. [Description 触发优化](#description-触发优化)
5. [领域组织模式](#领域组织模式)
6. [常见反模式](#常见反模式)
7. [迭代改进思路](#迭代改进思路)

---

## 核心哲学：Explain the Why

这是最重要的原则。当今的 LLM 拥有强大的推理能力和心智理论（Theory of Mind）。当你给它一个好的框架，它能超越机械的指令执行，真正理解任务并做出明智的决策。

**反面做法**：堆砌 ALWAYS/NEVER 规则
```markdown
## 输出规则
ALWAYS 使用 markdown 表格
NEVER 超过 3 层嵌套
MUST 包含总结段落
```

**正面做法**：解释原因，让模型理解
```markdown
## 输出格式
使用 markdown 表格展示结构化数据，因为用户通常需要快速比较多个指标，
表格比列表更易于横向对比。

保持嵌套层级在 3 层以内 — 过深的嵌套会让用户在长输出中迷失方向，
如果信息确实复杂，考虑用分节标题代替深层嵌套。

以一段简洁的总结收尾，让用户在不阅读全文的情况下也能快速获得关键结论。
```

当你发现自己在写全大写的 ALWAYS 或 NEVER 时，这是一个信号 — 退一步想想，能否通过解释原因让模型自然地遵循。

---

## 渐进式披露

### 三级加载模型

| 层级 | 内容 | 何时加载 | Token 预算 |
|------|------|----------|-----------|
| Level 1 | name + description | 始终在上下文 | ~100 词 |
| Level 2 | SKILL.md 正文 | Skill 触发时 | < 500 行 |
| Level 3 | references/ + scripts/ | Agent 按需 fs_read | 不限 |

### 关键原则

- **SKILL.md 正文控制在 500 行以内**。如果接近上限，增加一层层级：将详细内容拆到 `references/`，正文中留下清晰的"何时读取哪个文件"的指引
- **参考文件从 SKILL.md 中明确引用**，附带读取时机的说明
- **大型参考文件（>300 行）包含目录**，方便 Agent 快速定位

### 引用参考文件的模式

```markdown
## 常见错误处理

遇到以下错误类型时，请用 fs_read 读取对应的参考文件获取详细方案：

- Pod 异常相关 → `references/pod-errors.md`
- 网络问题相关 → `references/network-issues.md`
- 存储问题相关 → `references/storage-issues.md`
```

---

## 写作模式

### 祈使语气

用直接的指令语气，而非建议语气：

```markdown
# 好
请先运行 `kubectl get pods -n <namespace>` 获取 Pod 列表，
然后对异常 Pod 执行 `kubectl describe pod <pod-name>` 查看事件。

# 不好
你可以运行 kubectl get pods 来查看 Pod 列表，
如果需要的话也可以用 describe 命令查看详情。
```

### 定义输出格式

使用具体的模板：

```markdown
## 诊断报告格式

请按以下结构输出诊断结果：

### 现象
<一句话描述观察到的问题>

### 根因分析
<按可能性从高到低列出原因>

### 修复建议
<具体的修复命令和步骤>

### 验证方法
<修复后如何确认问题已解决>
```

### 示例模式

用具体的输入→输出示例，帮助模型理解期望：

```markdown
## 示例

**示例 1：OOMKilled**
用户输入: "我的 Pod 一直在重启，状态显示 OOMKilled"
期望行为:
1. 先查看 Pod 资源限制：`kubectl describe pod <name> | grep -A5 Limits`
2. 查看实际内存使用：`kubectl top pod <name>`
3. 给出调整 resources.limits.memory 的建议

**示例 2：ImagePullBackOff**
用户输入: "Pod 状态 ImagePullBackOff"
期望行为:
1. 检查镜像名称是否正确
2. 检查 imagePullSecrets 配置
3. 测试镜像仓库连通性
```

### 速查表模式

对高频场景提供快速映射：

```markdown
## 常见问题速查表

| 现象 | 可能原因 | 快速诊断命令 |
|------|---------|-------------|
| CrashLoopBackOff | 应用启动失败 | `kubectl logs <pod> --previous` |
| ImagePullBackOff | 镜像拉取失败 | `kubectl describe pod <pod>` |
| Pending | 资源不足/调度失败 | `kubectl describe pod <pod>` 看 Events |
| OOMKilled | 内存超限 | `kubectl top pod <pod>` |
```

---

## Description 触发优化

description 是决定 Skill 是否被激活的核心字段。优化 description 可以显著提升触发准确率。

### 原则

1. **同时说明"做什么"和"什么时候用"**：不要只写功能描述，要覆盖触发场景
2. **稍微"主动"**：当前系统倾向于"不够积极"地触发 Skill。让 description 覆盖更多边缘场景
3. **包含关键词**：列出用户可能使用的术语、错误信息、场景描述

### 对比示例

**不好**：
```yaml
description: Docker 故障排查
```

**好**：
```yaml
description: >-
  Docker 容器故障排查专家。当用户提到容器启动失败、镜像构建报错、
  docker-compose 问题、容器网络不通、volume 挂载失败、
  Dockerfile 优化、或任何 Docker 相关的异常和疑问时使用此技能，
  即使用户没有明确说"排查"二字也应该触发
```

---

## 领域组织模式

当一个 Skill 支持多个领域或框架时，避免在 SKILL.md 中堆砌所有内容。采用"选择逻辑在正文，详细方案在 references"的模式：

```
cloud-deploy/
├── SKILL.md              # 通用部署工作流 + 选择逻辑
└── references/
    ├── aws.md            # AWS 部署详细步骤
    ├── gcp.md            # GCP 部署详细步骤
    └── azure.md          # Azure 部署详细步骤
```

SKILL.md 正文中这样引导：

```markdown
## 平台选择

根据用户的目标平台，用 fs_read 读取对应的参考文件：

- AWS（EC2/ECS/EKS/Lambda）→ `references/aws.md`
- GCP（GCE/GKE/Cloud Run）→ `references/gcp.md`
- Azure（AKS/App Service）→ `references/azure.md`

如果用户没有指定平台，先询问他们的部署目标。
```

Agent 运行时只会读取用户需要的那一个参考文件，避免浪费 token。

---

## 常见反模式

### 1. 过度约束

```markdown
# 反模式：规则过多，限制了模型的灵活性
MUST 使用 kubectl get pods -o json
NEVER 使用 kubectl get pods -o wide
ALWAYS 先检查 namespace
MUST NOT 直接删除 Pod
```

**改进**：解释为什么某些做法更好，让模型根据实际情况判断。

### 2. 过于宽泛

```markdown
# 反模式：指令太模糊
你是一个专家，请帮助用户解决问题。
```

**改进**：给出具体的方法论步骤、工具使用策略和输出格式。

### 3. 过度细分

```markdown
# 反模式：为每个小场景创建独立 Skill
skills/
├── k8s-pod-crash/
├── k8s-pod-pending/
├── k8s-pod-oom/
├── k8s-network-issue/
└── k8s-storage-issue/
```

**改进**：合并为一个 `k8s-troubleshooting` Skill，用 references 区分子场景。

### 4. 忽略边界情况

```markdown
# 反模式：只考虑理想路径
1. 运行 kubectl get pods
2. 找到异常 Pod
3. 修复
```

**改进**：考虑"如果没有权限怎么办"、"如果命令超时怎么办"、"如果没有找到异常 Pod 怎么办"。

---

## 迭代改进思路

Skill 创建后很可能需要多轮改进。以下是从实际使用反馈中改进的思路：

### 1. 从反馈中泛化

用户在少量测试用例上给的反馈，需要泛化为通用的指令改进。不要针对单个测试用例做过拟合的修补，而是理解问题的本质原因。

### 2. 保持精简

删除没有贡献的内容。如果某段指令在测试中没有产生正面效果，考虑移除。过多的指令会稀释真正重要的内容。

### 3. 寻找重复模式

如果 Agent 在多次使用 Skill 时都独立写出了类似的辅助逻辑，这强烈暗示应该将该逻辑提取为 `scripts/` 中的脚本或 `references/` 中的模板，避免每次从零开始。

### 4. 重新表述而非加重

如果某个行为一直不符合预期，与其加更多 MUST/NEVER 约束，不如尝试换一种表述方式 — 用不同的比喻、不同的组织结构、不同的解释角度。这种方式的成本很低，却可能带来显著改善。
