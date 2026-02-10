"""日期时间工具，提供当前时间查询。"""

from datetime import datetime
from typing import Any, Dict

from src.tools.base_tool import BaseTool


class DateTimeTool(BaseTool):
    """获取当前日期和时间信息。"""

    @property
    def name(self) -> str:
        return "get_current_time"

    @property
    def description(self) -> str:
        return (
            "获取当前的日期和时间信息，包括年月日、星期、时分秒。"
            "适用场景：用户询问'今天几号'、'现在几点'、'今天星期几'等时间相关问题时使用。"
            "注意：返回的是服务器本地时区的时间。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "时区名称，默认为系统本地时区。例如: 'Asia/Shanghai', 'UTC'",
                }
            },
            "required": [],
        }

    def execute(self, timezone: str = "", **kwargs) -> str:
        now = datetime.now()

        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[now.weekday()]

        return (
            f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"星期: {weekday}\n"
            f"时区: 系统本地时区"
        )
