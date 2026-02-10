"""会话持久化存储模块。

将租户会话数据（对话元信息、聊天记录、ConversationMemory 消息）
序列化为 JSON 文件，支持服务重启后恢复。

存储路径：.agent_data/sessions/{tenant_id}.json
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.llm.base_client import Message, Role
from src.utils.logger import logger

# 持久化根目录
_DEFAULT_BASE_DIR = ".agent_data/sessions"


class SessionStore:
    """基于 JSON 文件的会话持久化存储。

    每个租户对应一个 JSON 文件，包含所有对话的元信息和消息记录。
    写操作通过锁保证线程安全。
    """

    def __init__(self, base_dir: str = _DEFAULT_BASE_DIR):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _tenant_path(self, tenant_id: str) -> Path:
        """获取租户的持久化文件路径。"""
        # 使用前 32 字符作为文件名，避免过长
        safe_id = tenant_id[:32] if tenant_id else "unknown"
        return self._base_dir / f"{safe_id}.json"

    # ── 序列化辅助 ──

    @staticmethod
    def _serialize_messages(messages: List[Message]) -> List[dict]:
        """将 Message 列表序列化为可 JSON 化的字典列表。"""
        return [msg.model_dump(mode="json") for msg in messages]

    @staticmethod
    def _deserialize_messages(data: List[dict]) -> List[Message]:
        """从字典列表反序列化为 Message 列表。"""
        messages = []
        for item in data:
            # Role 枚举需要从字符串恢复
            if "role" in item and isinstance(item["role"], str):
                item["role"] = Role(item["role"])
            messages.append(Message.model_validate(item))
        return messages

    # ── 保存 ──

    def save_tenant(
        self,
        tenant_id: str,
        active_conv_id: Optional[str],
        conversations: Dict[str, dict],
    ) -> None:
        """保存整个租户会话到磁盘。

        Args:
            tenant_id: 租户 ID。
            active_conv_id: 当前活跃对话 ID。
            conversations: 各对话的序列化数据，结构为：
                {conv_id: {
                    "id": str,
                    "title": str,
                    "created_at": float,
                    "chat_history": List[dict],  # UI 聊天记录
                    "memory_messages": List[dict],  # ConversationMemory 序列化
                    "system_prompt_count": int,
                }}
        """
        payload = {
            "tenant_id": tenant_id,
            "active_conv_id": active_conv_id,
            "conversations": conversations,
        }

        path = self._tenant_path(tenant_id)
        with self._lock:
            try:
                tmp_path = path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                # 原子替换：先写临时文件再 rename，防止写入中途崩溃导致数据损坏
                tmp_path.replace(path)
                logger.debug(
                    "会话已保存 | tenant={} | convs={}",
                    tenant_id[:8], len(conversations),
                )
            except Exception as e:
                logger.error("会话保存失败 | tenant={} | err={}", tenant_id[:8], e)

    # ── 加载 ──

    def load_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """从磁盘加载租户会话数据。

        Returns:
            成功时返回 {"tenant_id", "active_conv_id", "conversations"} 字典，
            文件不存在或解析失败时返回 None。
        """
        path = self._tenant_path(tenant_id)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                "会话已加载 | tenant={} | convs={}",
                tenant_id[:8], len(data.get("conversations", {})),
            )
            return data
        except Exception as e:
            logger.error("会话加载失败 | tenant={} | err={}", tenant_id[:8], e)
            return None

    # ── 删除 ──

    def delete_tenant(self, tenant_id: str) -> None:
        """删除租户的持久化数据。"""
        path = self._tenant_path(tenant_id)
        with self._lock:
            try:
                if path.exists():
                    path.unlink()
                    logger.info("会话文件已删除 | tenant={}", tenant_id[:8])
            except Exception as e:
                logger.error("会话文件删除失败 | tenant={} | err={}", tenant_id[:8], e)
