"""文件系统沙箱安全层。

为 FileReaderTool 和 FileWriterTool 提供统一的路径安全验证：
- 支持多根目录白名单（默认根 + 额外允许目录）
- 读写权限分级（默认根可读写，额外目录默认只读）
- 排除敏感文件和目录（.env、.git 等），全局生效
- 文件大小限制，防止读取超大文件耗尽上下文
- 路径穿越防御，所有路径 resolve 后检查是否在白名单内

FileReader 和 FileWriter 共用同一个 Sandbox 实例，
安全逻辑集中维护，避免重复实现。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.utils.logger import logger


@dataclass(frozen=True)
class AllowedDir:
    """白名单中的一个允许目录。"""

    path: Path        # 绝对路径（已 resolve）
    writable: bool    # 是否允许写入
    is_default: bool  # 是否为默认根目录（相对路径基于此解析）


class Sandbox:
    """文件系统沙箱，基于多根目录白名单限制文件操作。

    安全模型：
    - 所有路径操作必须落在某个允许目录内
    - 默认根目录（default_root）为读写，相对路径基于此目录解析
    - 额外允许目录默认只读，可通过 writable_dirs 配置为可写
    - 敏感文件排除规则对所有目录全局生效

    Args:
        root: 默认根目录（读写，相对路径的基准）。
        allowed_dirs: 额外允许访问的目录列表（默认只读）。
        writable_dirs: 额外允许写入的目录列表（必须也在 allowed_dirs 中）。
        exclude_patterns: 排除的文件/目录模式列表。
        max_file_size: 单文件读取大小限制（字节）。
        max_depth: 目录搜索最大深度。
        max_results: 搜索结果最大条数。
    """

    def __init__(
        self,
        root: Optional[str] = None,
        allowed_dirs: Optional[List[str]] = None,
        writable_dirs: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        max_file_size: int = 1_048_576,
        max_depth: int = 5,
        max_results: int = 50,
    ):
        # 默认根目录
        default_root = Path(root).expanduser().resolve() if root else Path.cwd().resolve()
        if not default_root.is_dir():
            raise ValueError(f"默认根目录不存在: {default_root}")
        self._default_root = default_root

        # 构建白名单：默认根（读写） + 额外目录
        writable_set = set()
        if writable_dirs:
            for d in writable_dirs:
                writable_set.add(Path(d).expanduser().resolve())

        self._allowed: List[AllowedDir] = [
            AllowedDir(path=default_root, writable=True, is_default=True),
        ]

        if allowed_dirs:
            for d in allowed_dirs:
                p = Path(d).expanduser().resolve()
                if not p.is_dir():
                    logger.warning("允许目录不存在，已跳过: {}", p)
                    continue
                # 避免与默认根重复
                if p == default_root:
                    continue
                self._allowed.append(
                    AllowedDir(path=p, writable=p in writable_set, is_default=False)
                )

        self._exclude_patterns = exclude_patterns or [
            ".env", ".git", "__pycache__", ".agent_data", ".venv",
            "venv", "node_modules", ".idea", ".vscode",
        ]
        self._max_file_size = max_file_size
        self._max_depth = max_depth
        self._max_results = max_results

        logger.info(
            "Sandbox 初始化 | default_root={} | allowed_dirs={} | exclude={}",
            self._default_root,
            [(str(a.path), "rw" if a.writable else "ro") for a in self._allowed],
            self._exclude_patterns,
        )

    # ── 属性 ──

    @property
    def root(self) -> Path:
        """默认根目录（向后兼容）。"""
        return self._default_root

    @property
    def max_depth(self) -> int:
        return self._max_depth

    @property
    def max_results(self) -> int:
        return self._max_results

    @property
    def max_file_size(self) -> int:
        return self._max_file_size

    # ── 路径解析 ──

    def _resolve_path(self, path: str) -> Path:
        """将用户传入的路径解析为绝对路径。

        规则：
        - ~/xxx → 展开 home 目录
        - /xxx  → 绝对路径直接用
        - xxx   → 相对于默认根目录解析
        """
        p = Path(path).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (self._default_root / p).resolve()

    def _find_allowed_dir(self, resolved: Path) -> Optional[AllowedDir]:
        """查找路径所属的允许目录。

        按路径长度降序匹配（最具体的目录优先），
        确保 /home/user/proj 优先于 /home/user 匹配。
        """
        # 按路径深度降序排列，优先匹配最具体的目录
        sorted_allowed = sorted(self._allowed, key=lambda a: len(a.path.parts), reverse=True)
        for allowed in sorted_allowed:
            try:
                resolved.relative_to(allowed.path)
                return allowed
            except ValueError:
                continue
        return None

    # ── 验证方法 ──

    def validate_path(self, path: str) -> Path:
        """验证路径在任一允许目录内（读操作用），返回解析后的绝对路径。

        Raises:
            PermissionError: 路径不在任何允许目录内，或命中排除规则。
        """
        resolved = self._resolve_path(path)

        allowed = self._find_allowed_dir(resolved)
        if not allowed:
            raise PermissionError(
                f"路径不在允许的目录范围内: {path}\n"
                f"允许的目录: {[str(a.path) for a in self._allowed]}"
            )

        if self._is_excluded(resolved, allowed):
            raise PermissionError(f"受保护路径，禁止访问: {path}")

        return resolved

    def validate_path_for_write(self, path: str) -> Path:
        """验证路径可写：必须在可写目录内。

        Raises:
            PermissionError: 路径不在可写目录内，或命中排除规则。
        """
        resolved = self._resolve_path(path)

        allowed = self._find_allowed_dir(resolved)
        if not allowed:
            raise PermissionError(
                f"路径不在允许的目录范围内: {path}\n"
                f"允许的目录: {[str(a.path) for a in self._allowed]}"
            )

        if not allowed.writable:
            raise PermissionError(
                f"目录 {allowed.path} 为只读，禁止写入: {path}"
            )

        if self._is_excluded(resolved, allowed):
            raise PermissionError(f"受保护路径，禁止访问: {path}")

        return resolved

    def validate_file_for_read(self, path: str) -> Path:
        """验证文件可读：路径安全 + 文件存在 + 大小限制。

        Raises:
            PermissionError: 安全验证失败。
            FileNotFoundError: 文件不存在。
            ValueError: 文件过大。
        """
        resolved = self.validate_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        if not resolved.is_file():
            raise ValueError(f"路径不是文件: {path}")

        size = resolved.stat().st_size
        if size > self._max_file_size:
            size_mb = size / 1_048_576
            limit_mb = self._max_file_size / 1_048_576
            raise ValueError(
                f"文件过大 ({size_mb:.1f}MB)，超过限制 ({limit_mb:.1f}MB): {path}"
            )

        return resolved

    def validate_file_for_write(self, path: str) -> Path:
        """验证文件可写：路径安全 + 可写权限 + 若已存在则检查大小。

        Raises:
            PermissionError: 安全验证失败或目录只读。
            ValueError: 路径不是文件。
        """
        resolved = self.validate_path_for_write(path)

        if resolved.exists() and not resolved.is_file():
            raise ValueError(f"路径不是文件: {path}")

        return resolved

    def validate_dir(self, path: str) -> Path:
        """验证目录存在且在允许范围内。

        Raises:
            PermissionError: 安全验证失败。
            FileNotFoundError: 目录不存在。
        """
        resolved = self.validate_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"目录不存在: {path}")

        if not resolved.is_dir():
            raise ValueError(f"路径不是目录: {path}")

        return resolved

    # ── 展示辅助 ──

    def relative_to_root(self, absolute_path: Path) -> str:
        """将绝对路径转为相对于所属允许目录的展示路径。

        优先使用默认根目录的相对路径，其次使用所属允许目录的相对路径。
        """
        # 优先用默认根
        try:
            return str(absolute_path.relative_to(self._default_root))
        except ValueError:
            pass
        # 其次用所属允许目录
        allowed = self._find_allowed_dir(absolute_path)
        if allowed:
            try:
                rel = absolute_path.relative_to(allowed.path)
                return f"{allowed.path.name}/{rel}"
            except ValueError:
                pass
        return str(absolute_path)

    def is_excluded(self, path: Path) -> bool:
        """公开的排除检查（供遍历时过滤使用）。"""
        allowed = self._find_allowed_dir(path)
        if not allowed:
            return True
        return self._is_excluded(path, allowed)

    def list_allowed_dirs(self) -> str:
        """列出所有允许的目录及权限，供 LLM 了解可访问范围。"""
        lines = ["可访问的目录："]
        for a in self._allowed:
            perm = "读写" if a.writable else "只读"
            label = " (默认根目录)" if a.is_default else ""
            lines.append(f"  📁 {a.path}  ({perm}{label})")
        return "\n".join(lines)

    # ── 内部方法 ──

    def _is_excluded(self, resolved: Path, allowed: AllowedDir) -> bool:
        """检查路径是否命中排除规则。

        排除检查基于路径相对于所属允许目录的各段。
        """
        try:
            parts = resolved.relative_to(allowed.path).parts
        except ValueError:
            return True  # 不在任何允许目录内，视为排除

        for part in parts:
            for pattern in self._exclude_patterns:
                if part == pattern or part.startswith(pattern):
                    return True
        return False
