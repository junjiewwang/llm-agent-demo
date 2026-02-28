"""Skill 路由器 — 根据用户意图匹配最合适的 Skill。

匹配策略（按优先级）：
1. 关键词匹配：用户输入包含 Skill 的 trigger_patterns 中的关键词
2. 语义匹配（可选扩展）：基于 embedding 相似度匹配

当前 V1 实现关键词匹配，语义匹配作为后续扩展预留接口。
"""

from typing import List, Optional

from src.config import settings
from src.skills.base import Skill, SkillMatchResult
from src.skills.registry import SkillRegistry
from src.utils.logger import logger


class SkillRouter:
    """Skill 路由器，根据用户意图选择合适的 Skill。

    用法：
        router = SkillRouter(registry)
        matches = router.match("帮我排查 pod CrashLoopBackOff 的问题")
        # → [SkillMatch(k8s_troubleshooting, score=0.8, type=keyword)]
    """

    def __init__(
        self,
        registry: SkillRegistry,
        max_active_skills: int = 2,
    ) -> None:
        """
        Args:
            registry: Skill 注册中心。
            max_active_skills: 单次请求最多激活的 Skill 数量，防止 token 膨胀。
        """
        self._registry = registry
        self._max_active_skills = max_active_skills

    @property
    def registry(self) -> SkillRegistry:
        """暴露内部 SkillRegistry，供 API 层查询和管理。"""
        return self._registry

    def match(self, user_input: str) -> List[SkillMatchResult]:
        """根据用户输入匹配合适的 Skill。

        匹配流程：
        1. 对所有已启用的 Skill 进行关键词匹配
        2. 按 (score desc, priority asc) 排序
        3. 截取 top-N（受 max_active_skills 限制）

        Args:
            user_input: 用户输入文本。

        Returns:
            匹配到的 SkillMatchResult 列表（可能为空）。
        """
        if not user_input or not user_input.strip():
            return []

        candidates: List[SkillMatchResult] = []
        input_lower = user_input.lower()

        for skill in self._registry.list_active():
            result = self._keyword_match(skill, input_lower)
            if result is not None:
                candidates.append(result)

        if not candidates:
            return []

        # 过滤低分候选（避免宽泛关键词误触发）
        min_score = settings.agent.skill_min_match_score
        candidates = [m for m in candidates if m.score >= min_score]
        if not candidates:
            logger.debug("所有 Skill 匹配分数低于阈值 {}", min_score)
            return []

        # 排序：分数高优先，同分时 priority 小优先
        candidates.sort(key=lambda m: (-m.score, m.skill.priority))

        # 截取 top-N
        selected = candidates[:self._max_active_skills]

        if selected:
            names = [f"{m.skill.name}({m.score:.2f})" for m in selected]
            logger.info("Skill 匹配结果: {}", ", ".join(names))

        return selected

    @staticmethod
    def _keyword_match(skill: Skill, input_lower: str) -> Optional[SkillMatchResult]:
        """关键词匹配：计算 trigger_patterns 的命中率作为分数。

        匹配规则：
        - 命中 trigger_patterns 中的关键词越多，分数越高
        - 分数 = 命中数 / 总 pattern 数
        - 至少命中 1 个才算匹配成功

        Args:
            skill: 待匹配的 Skill。
            input_lower: 用户输入（已转小写）。

        Returns:
            匹配成功返回 SkillMatchResult，否则返回 None。
        """
        if not skill.trigger_patterns:
            return None

        hit_count = sum(
            1 for pattern in skill.trigger_patterns
            if pattern.lower() in input_lower
        )

        if hit_count == 0:
            return None

        score = hit_count / len(skill.trigger_patterns)
        return SkillMatchResult(skill=skill, score=score, match_type="keyword")
