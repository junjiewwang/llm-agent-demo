"""FastAPI 服务启动入口。

用法：
    python server.py              # 默认 0.0.0.0:8000
    python server.py --port 9000  # 自定义端口
    python server.py --reload     # 开发模式（热重载）
"""

import argparse

import uvicorn

from src.api.app import create_app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM ReAct Agent Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式（热重载）")
    args = parser.parse_args()

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
