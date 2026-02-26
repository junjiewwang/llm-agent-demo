"""用户数据持久化存储（基于文件）。

数据结构：data/users.json
{
  "users": {
    "username": {
      "id": "uuid",
      "password_hash": "hash_str",
      "created_at": timestamp
    }
  }
}
"""
import json
import threading
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any

from src.utils.logger import logger


class UserStore:
    """用户数据存储，提供基本的增删改查。"""

    def __init__(self, data_dir: str = ".agent_data"):
        self._data_dir = Path(data_dir)
        self._users_path = self._data_dir / "users.json"
        self._lock = threading.Lock()
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """确保数据目录存在。"""
        if not self._data_dir.exists():
            self._data_dir.mkdir(parents=True, exist_ok=True)

    def _load_users(self) -> Dict[str, Any]:
        """加载所有用户数据。"""
        if not self._users_path.exists():
            return {}
        try:
            with open(self._users_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("users", {})
        except Exception as e:
            logger.error("加载用户数据失败: {}", e)
            return {}

    def _save_users(self, users: Dict[str, Any]):
        """保存所有用户数据。"""
        try:
            with open(self._users_path, "w", encoding="utf-8") as f:
                json.dump({"users": users}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存用户数据失败: {}", e)

    def create_user(self, username: str, password_hash: str) -> Optional[Dict[str, Any]]:
        """创建新用户。"""
        with self._lock:
            users = self._load_users()
            if username in users:
                return None  # 用户已存在

            user_id = uuid.uuid4().hex
            user_data = {
                "id": user_id,
                "username": username,
                "password_hash": password_hash,
                "created_at": time.time(),
            }
            users[username] = user_data
            self._save_users(users)
            logger.info("新用户注册成功: {}", username)
            return user_data

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名查找用户。"""
        # 读取操作不加锁，依赖文件系统的原子性（虽然并发写入可能有问题，但这里读写分离不严格）
        # 为了严谨，还是加上锁，或者容忍脏读
        with self._lock:
            users = self._load_users()
            return users.get(username)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 查找用户（效率较低，需遍历）。"""
        with self._lock:
            users = self._load_users()
            for user in users.values():
                if user["id"] == user_id:
                    return user
            return None
