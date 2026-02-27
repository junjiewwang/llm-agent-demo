"""Skill 基础定义 — 声明式的领域专家知识单元。

Skill 是比 Tool 更高级的能力抽象：
- Tool = 单个原子操作（一次 function call）
- Skill = 场景化的专家知识 + 工具使用指南 + 推理策略

Skill 不包含执行逻辑，而是通过声明式的 prompt 片段注入 LLM 上下文，
引导 Agent 在特定场景下更智能地思考和使用工具。

设计原则：
1. 声明式：Skill 只描述"做什么"和"怎么做"，不包含执行代码
2. 可组合：多个 Skill 可以叠加注入
3. 无侵入：Skill 通过 ContextBuilder 注入，不改变 Agent 核心循环
4. 可扩展：新增 Skill 只需创建一个配置类，零代码侵入
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Skill:
    """声明式的领域专家知识单元。

    Attributes:
        name: 唯一标识名称（英文，如 'k8s_troubleshooting'）。
        display_name: 展示名称（如 '故障排查专家'）。
        description: 简短描述，用于意图匹配时的语义比较。
        trigger_patterns: 关键词/短语列表，用于快速匹配用户意图。
        system_prompt: 注入 LLM 的领域专家 prompt（Skill 的核心内容）。
        required_tools: 该 Skill 依赖的工具名列表（用于校验和提示）。
        priority: 优先级（值越小越优先），同时匹配多个 Skill 时取优先级最高的。
        max_coexist: 最大共存数量，限制同时激活的 Skill 数避免 token 浪费。
        base_dir: Skill 所在目录的绝对路径（由 loader 自动填充）。
        references: 附属参考资料文件路径列表（相对于 base_dir）。
        scripts: 附属脚本文件路径列表（相对于 base_dir）。
    """

    name: str
    display_name: str
    description: str
    trigger_patterns: List[str] = field(default_factory=list)
    system_prompt: str = ""
    required_tools: List[str] = field(default_factory=list)
    priority: int = 100
    max_coexist: int = 2
    base_dir: Optional[str] = None
    references: Tuple[str, ...] = ()
    scripts: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Skill name 不能为空")
        if not self.system_prompt:
            raise ValueError(f"Skill '{self.name}' 的 system_prompt 不能为空")

    @property
    def has_resources(self) -> bool:
        """是否包含附属资源（references 或 scripts）。"""
        return bool(self.references or self.scripts)

    @property
    def prompt_token_hint(self) -> int:
        """粗略估算 system_prompt 的 token 数（按 1 中文字 ≈ 2 token）。"""
        return len(self.system_prompt) * 2 // 3


@dataclass
class SkillMatchResult:
    """Skill 匹配结果。

    Attributes:
        skill: 匹配到的 Skill。
        score: 匹配分数（0.0 ~ 1.0），越高越相关。
        match_type: 匹配方式（'keyword' | 'semantic'）。
    """

    skill: Skill
    score: float
    match_type: str

    def __repr__(self) -> str:
        return f"SkillMatch({self.skill.name}, score={self.score:.2f}, type={self.match_type})"
