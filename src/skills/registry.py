"""Skill 注册中心 — 管理所有可用 Skill 的注册、查询与启停。

职责单一：只负责 Skill 的注册、查询、校验和启停状态管理，不涉及匹配逻辑。
匹配逻辑由 SkillRouter 负责。
"""

from typing import Dict, List, Optional, Set

from src.skills.base import Skill
from src.utils.logger import logger


class SkillRegistry:
    """Skill 注册中心。

    管理所有可用 Skill，提供注册、查询、依赖校验、启停管理等功能。
    启停状态通过 _disabled 集合维护（Skill 是 frozen dataclass，不可修改）。

    用法：
        registry = SkillRegistry()
        registry.register(k8s_skill)
        registry.register(docker_skill)

        skill = registry.get("k8s_troubleshooting")
        all_skills = registry.list_all()
        active_skills = registry.list_active()

        registry.set_enabled("docker_skill", False)  # 禁用
    """

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._disabled: Set[str] = set()

    def register(self, skill: Skill) -> "SkillRegistry":
        """注册 Skill，支持链式调用。

        Raises:
            ValueError: Skill name 重复时抛出。
        """
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' 已注册，不允许重复注册")
        self._skills[skill.name] = skill
        logger.info("Skill 已注册: {} ({})", skill.name, skill.display_name)
        return self

    def get(self, name: str) -> Optional[Skill]:
        """根据名称获取 Skill，不存在返回 None。"""
        return self._skills.get(name)

    def list_all(self) -> List[Skill]:
        """返回所有已注册的 Skill（按 priority 排序）。"""
        return sorted(self._skills.values(), key=lambda s: s.priority)

    def list_active(self) -> List[Skill]:
        """返回所有已启用的 Skill（按 priority 排序）。

        用于 SkillRouter 匹配时只考虑启用的 Skill。
        """
        return sorted(
            (s for s in self._skills.values() if s.name not in self._disabled),
            key=lambda s: s.priority,
        )

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """设置 Skill 启停状态。

        Args:
            name: Skill 名称。
            enabled: True=启用，False=禁用。

        Returns:
            True 表示操作成功（Skill 存在），False 表示 Skill 不存在。
        """
        if name not in self._skills:
            return False
        if enabled:
            self._disabled.discard(name)
            logger.info("Skill 已启用: {}", name)
        else:
            self._disabled.add(name)
            logger.info("Skill 已禁用: {}", name)
        return True

    def is_enabled(self, name: str) -> bool:
        """查询 Skill 是否启用。未注册的 Skill 返回 False。"""
        if name not in self._skills:
            return False
        return name not in self._disabled

    def validate_tools(self, available_tools: List[str]) -> List[str]:
        """校验所有 Skill 的 required_tools 是否已注册。

        Args:
            available_tools: 当前 ToolRegistry 中已注册的工具名列表。

        Returns:
            缺失工具的警告信息列表（空列表表示全部通过）。
        """
        warnings: List[str] = []
        tool_set = set(available_tools)
        for skill in self._skills.values():
            missing = [t for t in skill.required_tools if t not in tool_set]
            if missing:
                msg = f"Skill '{skill.name}' 依赖的工具未注册: {missing}"
                warnings.append(msg)
                logger.warning(msg)
        return warnings

    def check_tools_satisfied(self, skill: Skill, available_tools: List[str]) -> bool:
        """检查指定 Skill 的依赖工具是否全部满足。"""
        tool_set = set(available_tools)
        return all(t in tool_set for t in skill.required_tools)

    @property
    def skill_names(self) -> List[str]:
        """返回所有已注册 Skill 名称。"""
        return list(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
