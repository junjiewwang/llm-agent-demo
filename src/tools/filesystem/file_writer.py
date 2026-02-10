"""文件写入工具。

提供 3 种操作，通过 action 参数区分：
- write_file: 创建或覆写文件
- append_file: 追加内容到文件末尾
- replace_in_file: 精确替换文件中的指定文本段

所有操作都受 Sandbox 沙箱约束，确保路径安全。
写操作前自动创建不存在的父目录。
支持多根目录白名单，仅允许在可写目录中执行写操作。
"""

from pathlib import Path
from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.tools.filesystem.sandbox import Sandbox
from src.utils.logger import logger


class FileWriterTool(BaseTool):
    """文件写入工具。

    通过 Sandbox 限制所有操作在安全目录内，
    支持文件创建/覆写、追加、精确替换。
    """

    def __init__(self, sandbox: Sandbox):
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "file_writer"

    @property
    def description(self) -> str:
        return (
            "修改本地文件系统中的文件。支持 3 种操作（通过 action 参数指定）：\n"
            "1. write_file: 创建新文件或覆写已有文件（需提供 content）\n"
            "2. append_file: 追加内容到文件末尾（需提供 content）\n"
            "3. replace_in_file: 精确替换文件中的指定文本（需提供 old_str 和 new_str）\n"
            "适用场景：需要创建、修改、编辑文件内容时使用。\n"
            "不适用：读取或搜索文件请使用 file_reader 工具。\n"
            "注意：只能写入可写目录（用 file_reader 的 list_allowed_dirs 查看权限）。\n"
            "建议修改前先用 file_reader 查看文件内容，确保 old_str 精确匹配。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write_file", "append_file", "replace_in_file"],
                    "description": "操作类型",
                },
                "path": {
                    "type": "string",
                    "description": "目标文件路径。支持相对路径（基于默认工作目录）、绝对路径、~ 路径",
                },
                "content": {
                    "type": "string",
                    "description": "write_file / append_file: 要写入或追加的内容",
                },
                "old_str": {
                    "type": "string",
                    "description": "replace_in_file: 要替换的原始文本（必须精确匹配文件中的内容）",
                },
                "new_str": {
                    "type": "string",
                    "description": "replace_in_file: 替换后的文本（传空字符串表示删除）",
                },
            },
            "required": ["action", "path"],
        }

    def execute(self, action: str, path: str, **kwargs) -> str:
        """根据 action 分发到具体操作。"""
        dispatch = {
            "write_file": self._write_file,
            "append_file": self._append_file,
            "replace_in_file": self._replace_in_file,
        }

        handler = dispatch.get(action)
        if not handler:
            return f"未知操作: {action}。支持的操作: {list(dispatch.keys())}"

        try:
            return handler(path, **kwargs)
        except (PermissionError, FileNotFoundError, ValueError) as e:
            return f"操作失败: {e}"
        except Exception as e:
            logger.error("file_writer.{} 异常: {}", action, e)
            return f"操作异常: {e}"

    # ── 具体操作 ──

    def _write_file(self, path: str, content: str = "", **kwargs) -> str:
        """创建或覆写文件。"""
        file_path = self._sandbox.validate_file_for_write(path)

        # 自动创建父目录
        file_path.parent.mkdir(parents=True, exist_ok=True)

        is_new = not file_path.exists()
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        rel = self._sandbox.relative_to_root(file_path)
        line_count = content.count("\n") + (1 if content else 0)
        action_word = "创建" if is_new else "覆写"
        logger.info("file_writer.write_file | {} {} ({} 行)", action_word, rel, line_count)
        return f"✅ 已{action_word}文件: {rel} ({line_count} 行)"

    def _append_file(self, path: str, content: str = "", **kwargs) -> str:
        """追加内容到文件末尾。"""
        file_path = self._sandbox.validate_file_for_write(path)

        if not content:
            return "append_file 需要提供 content 参数"

        # 文件不存在则创建
        file_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not file_path.exists()

        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content)

        rel = self._sandbox.relative_to_root(file_path)
        appended_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        logger.info("file_writer.append_file | {} (+{} 行)", rel, appended_lines)

        if is_new:
            return f"✅ 已创建文件并写入: {rel} ({appended_lines} 行)"
        return f"✅ 已追加到文件末尾: {rel} (+{appended_lines} 行)"

    def _replace_in_file(self, path: str, old_str: str = "", new_str: str = "", **kwargs) -> str:
        """精确替换文件中的指定文本。"""
        # 先验证可读（文件存在 + 大小限制），再验证可写
        file_path = self._sandbox.validate_file_for_read(path)
        self._sandbox.validate_path_for_write(path)

        if not old_str:
            return "replace_in_file 需要提供 old_str 参数（要替换的原始文本）"

        with open(file_path, "r", encoding="utf-8") as f:
            original = f.read()

        # 检查匹配次数
        count = original.count(old_str)
        if count == 0:
            return (
                f"替换失败: 在 {self._sandbox.relative_to_root(file_path)} 中未找到匹配文本。\n"
                "请检查 old_str 是否与文件内容精确匹配（包括空格和换行）。\n"
                "建议先用 file_reader 的 read_file 操作查看文件内容。"
            )

        if count > 1:
            return (
                f"替换失败: old_str 在文件中匹配了 {count} 处，无法确定替换哪一处。\n"
                "请提供更多上下文使 old_str 唯一匹配。"
            )

        # 执行替换
        new_content = original.replace(old_str, new_str, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        rel = self._sandbox.relative_to_root(file_path)
        old_lines = old_str.count("\n") + 1
        new_lines = new_str.count("\n") + 1 if new_str else 0
        logger.info("file_writer.replace_in_file | {} | {} 行 → {} 行", rel, old_lines, new_lines)

        if not new_str:
            return f"✅ 已删除文本: {rel} (移除了 {old_lines} 行)"
        return f"✅ 已替换文本: {rel} ({old_lines} 行 → {new_lines} 行)"
