# Skill 数据结构规范

本文档定义 Skill 系统中各数据结构的 schema，供创建和维护 Skill 时参考。

---

## SKILL.md Front Matter

YAML Front Matter 位于 `---` 分隔符之间，定义 Skill 的元数据。

```yaml
---
# 必填字段
name: docker_troubleshooting          # 唯一标识符，snake_case
display_name: Docker 故障排查专家      # 中文展示名
description: >-                        # 触发描述（关键！决定 Skill 是否被激活）
  Docker 容器故障排查专家。当用户提到容器异常、
  镜像构建失败、网络不通等问题时使用

# 可选字段
priority: 10                           # 优先级，值越小越优先（默认 100）
max_coexist: 2                         # 最大共存数（默认 2）
required_tools:                        # 依赖的工具列表
  - docker
  - fs_read
trigger_patterns:                      # 关键词列表，用于快速匹配
  - docker
  - 容器
  - 镜像
  - 故障排查
---
```

### 字段说明

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | ✅ | - | 唯一标识符，snake_case 格式 |
| `display_name` | string | ✅ | - | 用户可见的展示名称 |
| `description` | string | ✅ | - | 触发描述，包含"做什么"和"什么时候用" |
| `priority` | int | ❌ | 100 | 优先级（10=高频核心，50=通用，100=低频辅助） |
| `max_coexist` | int | ❌ | 2 | 最大共存 Skill 数量 |
| `required_tools` | list[str] | ❌ | [] | 依赖的工具名列表（支持别名，如 fs_read → file_reader） |
| `trigger_patterns` | list[str] | ❌ | [] | 关键词/短语列表，用于快速意图匹配 |

### description 写作要点

description 是 Skill 被触发的主要机制。Agent 根据 description 决定是否激活该 Skill。

**好的 description**：
```yaml
description: >-
  Kubernetes 故障排查专家。当用户提到 Pod 异常、CrashLoopBackOff、
  容器 OOMKilled、节点 NotReady、Service 访问不通、Ingress 配置问题，
  或任何 K8s 集群故障时，即使用户没有明确说"排查"二字也应使用此技能
```

**不好的 description**：
```yaml
description: Kubernetes 故障排查  # 太短，触发不充分
```

---

## Skill 目录结构

```
skill-name/
├── SKILL.md              # 必备：Skill 定义（Level 2）
├── references/           # 可选：参考资料（Level 3，按需加载）
│   ├── common-errors.md  # 常见错误速查表
│   └── best-practices.md # 最佳实践
└── scripts/              # 可选：辅助脚本（Level 3，按需执行）
    └── validate.py       # 校验脚本
```

### 目录命名规范

- Skill 目录名：kebab-case（如 `k8s-troubleshooting`）
- SKILL.md 文件名：固定为 `SKILL.md`
- references/ 下文件：描述性命名（如 `common-errors.md`、`aws.md`）
- scripts/ 下文件：功能性命名（如 `validate.py`、`generate_report.py`）

### 自动扫描

系统 loader 会自动扫描 `references/` 和 `scripts/` 子目录。扫描到的文件路径会通过 ContextBuilder 注入上下文，Agent 可通过 `fs_read` 按需读取。

---

## evals.json（未来扩展）

用于定义 Skill 的评估用例，当前版本暂不支持自动化评估，但建议在创建 Skill 时手动设计测试用例。

```json
{
  "skill_name": "docker_troubleshooting",
  "evals": [
    {
      "id": 1,
      "prompt": "我的容器一直在 CrashLoopBackOff，怎么排查？",
      "expected_output": "分步骤的排查指南，包含 kubectl 命令",
      "expectations": [
        "输出包含 kubectl describe pod 命令",
        "输出包含 kubectl logs 命令",
        "提供了至少 3 种可能的原因"
      ]
    },
    {
      "id": 2,
      "prompt": "docker build 一直失败，报 COPY failed",
      "expected_output": "针对 COPY 失败的排查步骤",
      "expectations": [
        "检查了 .dockerignore 文件",
        "检查了构建上下文路径",
        "提供了具体的修复命令"
      ]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `skill_name` | string | 对应 Skill 的 name |
| `evals[].id` | int | 唯一标识 |
| `evals[].prompt` | string | 测试提示词（模拟用户输入） |
| `evals[].expected_output` | string | 期望输出的描述 |
| `evals[].expectations` | list[str] | 可验证的断言列表 |
