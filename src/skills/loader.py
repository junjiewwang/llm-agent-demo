"""Skill 加载器 — 从 SKILL.md 文件加载声明式 Skill 定义。

支持 SKILL.md 格式（行业标准，兼容 Claude Code / Cursor / GitHub Copilot）：

    ---
    name: k8s_troubleshooting
    display_name: K8s 故障排查专家
    description: Kubernetes 集群故障排查
    priority: 10
    required_tools:
      - kubectl
    trigger_patterns:
      - 排查
      - 故障
    ---

    # K8s 故障排查专家

    你现在是 Kubernetes 故障排查专家...

YAML Front Matter（--- 包围的部分）存放元数据，Markdown 正文存放注入 LLM 的指令。

目录结构规范（一个目录 = 一个 Skill）：

    skills/
    ├── k8s-troubleshooting/
    │   ├── SKILL.md          # 必备：Skill 定义
    │   └── references/       # 可选：附属参考资料
    │       └── common-errors.md
    └── k8s-resource-analysis/
        └── SKILL.md
"""

from pathlib import Path
from typing import List, Optional, Set, Tuple

import yaml

from src.skills.base import Skill
from src.utils.logger import logger

# Skill 定义文件名
SKILL_FILENAME = "SKILL.md"

# Front Matter 必填字段
_REQUIRED_FIELDS = {"name", "display_name", "description"}

# 可自动扫描的附属资源子目录名
_REFERENCES_DIR = "references"
_SCRIPTS_DIR = "scripts"


def _parse_skill_md(content: str) -> Tuple[dict, str]:
    """解析 SKILL.md 文件内容，分离 YAML Front Matter 和 Markdown 正文。

    Args:
        content: SKILL.md 文件的完整文本。

    Returns:
        (front_matter_dict, markdown_body) 二元组。

    Raises:
        ValueError: Front Matter 格式不正确或 YAML 解析失败。
    """
    content = content.strip()

    # Front Matter 必须以 --- 开头
    if not content.startswith("---"):
        raise ValueError("SKILL.md 必须以 YAML Front Matter (---) 开头")

    # 找到第二个 ---
    second_sep = content.find("---", 3)
    if second_sep == -1:
        raise ValueError("SKILL.md 缺少 Front Matter 结束标记 (---)")

    front_matter_raw = content[3:second_sep].strip()
    markdown_body = content[second_sep + 3:].strip()

    # 解析 YAML Front Matter
    try:
        front_matter = yaml.safe_load(front_matter_raw)
    except yaml.YAMLError as e:
        raise ValueError(f"Front Matter YAML 解析失败: {e}") from e

    if not isinstance(front_matter, dict):
        raise ValueError("Front Matter 必须是 YAML 字典格式")

    return front_matter, markdown_body


def _scan_resource_dir(base_dir: Path, subdir_name: str) -> Tuple[str, ...]:
    """扫描 Skill 目录下的附属资源子目录，返回相对路径元组。

    只收集文件（忽略隐藏文件和 __pycache__），按文件名排序确保稳定性。

    Args:
        base_dir: Skill 所在目录。
        subdir_name: 子目录名（如 'references' 或 'scripts'）。

    Returns:
        相对于 base_dir 的文件路径元组（如 ('references/common-errors.md',)）。
    """
    subdir = base_dir / subdir_name
    if not subdir.is_dir():
        return ()

    paths = sorted(
        f for f in subdir.rglob("*")
        if f.is_file() and not f.name.startswith(".")
        and "__pycache__" not in f.parts
    )
    return tuple(str(p.relative_to(base_dir)) for p in paths)


def load_from_file(path: Path) -> Skill:
    """从单个 SKILL.md 文件加载 Skill。

    Args:
        path: SKILL.md 文件路径。

    Returns:
        解析后的 Skill 实例。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 格式错误或缺少必填字段。
    """
    if not path.exists():
        raise FileNotFoundError(f"Skill 文件不存在: {path}")

    content = path.read_text(encoding="utf-8")
    front_matter, markdown_body = _parse_skill_md(content)

    # 校验必填字段
    missing = _REQUIRED_FIELDS - set(front_matter.keys())
    if missing:
        raise ValueError(f"SKILL.md 缺少必填字段 ({path}): {missing}")

    # Markdown 正文作为 system_prompt（Skill 的核心指令）
    if not markdown_body:
        raise ValueError(f"SKILL.md 缺少 Markdown 正文（指令内容）: {path}")

    # 构建 Skill，只传入 dataclass 接受的字段
    skill_fields: dict = {
        "name": str(front_matter["name"]).strip(),
        "display_name": str(front_matter["display_name"]).strip(),
        "description": str(front_matter["description"]).strip(),
        "system_prompt": markdown_body,
    }

    # 可选字段
    if "trigger_patterns" in front_matter and isinstance(front_matter["trigger_patterns"], list):
        skill_fields["trigger_patterns"] = [str(p).strip() for p in front_matter["trigger_patterns"]]

    if "required_tools" in front_matter and isinstance(front_matter["required_tools"], list):
        skill_fields["required_tools"] = [str(t).strip() for t in front_matter["required_tools"]]

    if "priority" in front_matter:
        skill_fields["priority"] = int(front_matter["priority"])

    if "max_coexist" in front_matter:
        skill_fields["max_coexist"] = int(front_matter["max_coexist"])

    # 附属资源扫描（Level 3 渐进式披露）
    base_dir = path.parent
    skill_fields["base_dir"] = str(base_dir)
    skill_fields["references"] = _scan_resource_dir(base_dir, _REFERENCES_DIR)
    skill_fields["scripts"] = _scan_resource_dir(base_dir, _SCRIPTS_DIR)

    skill = Skill(**skill_fields)

    if skill.has_resources:
        logger.info(
            "Skill '{}' 加载了附属资源: {} references, {} scripts",
            skill.name, len(skill.references), len(skill.scripts),
        )

    return skill


def load_from_directory(
    directory: Path,
    disabled_skills: Optional[Set[str]] = None,
) -> List[Skill]:
    """递归扫描目录，加载所有包含 SKILL.md 的子目录。

    目录结构：每个子目录代表一个 Skill，子目录内必须包含 SKILL.md 文件。
    扫描策略：递归查找所有名为 SKILL.md 的文件。

    Args:
        directory: 扫描的根目录。
        disabled_skills: 需要禁用的 Skill 名称集合（加载后按 name 过滤）。

    Returns:
        成功加载的 Skill 列表（跳过无效文件，不阻断启动）。
    """
    if not directory.exists():
        logger.warning("Skills 目录不存在，跳过: {}", directory)
        return []

    if not directory.is_dir():
        logger.warning("Skills 路径不是目录，跳过: {}", directory)
        return []

    disabled = disabled_skills or set()
    skills: List[Skill] = []
    seen_names: Set[str] = set()

    # 递归扫描所有 SKILL.md 文件
    skill_files = sorted(
        f for f in directory.rglob(SKILL_FILENAME)
        if f.is_file()
    )

    for path in skill_files:
        try:
            skill = load_from_file(path)
        except (ValueError, FileNotFoundError) as e:
            logger.warning("跳过无效 Skill: {} | 原因: {}", path.parent.name, e)
            continue

        # 禁用检查
        if skill.name in disabled:
            logger.info("Skill '{}' 已被配置禁用，跳过", skill.name)
            continue

        # 重名检查
        if skill.name in seen_names:
            logger.warning("Skill 名称重复，跳过后加载的: {} (来自 {})", skill.name, path)
            continue

        seen_names.add(skill.name)
        skills.append(skill)
        logger.debug("已加载 Skill: {} ({})", skill.name, path.parent.name)

    return skills
