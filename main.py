"""LLM Agent 入口 - CLI 对话模式。

使用 ReAct Agent，支持自主规划、工具调用、短期/长期记忆、RAG 知识库和多轮对话。

命令：
    /clear       - 清空对话历史
    /tools       - 查看已注册工具
    /memory      - 查看记忆和知识库状态
    /import <路径> - 导入文件或目录到知识库
    /kb clear    - 清空知识库
    /exit        - 退出程序
"""

import os

from src.factory import create_agent
from src.rag import DocumentLoader
from src.utils.logger import logger


def handle_import(args: str, knowledge_base) -> None:
    """处理 /import 命令。"""
    path = args.strip()
    if not path:
        print("  用法: /import <文件或目录路径>")
        print(f"  支持格式: {', '.join(DocumentLoader.supported_extensions())}")
        return

    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"  ❌ 路径不存在: {path}")
        return

    try:
        if os.path.isdir(path):
            chunks = knowledge_base.import_directory(path)
            print(f"  ✅ 目录导入完成，共 {chunks} 个文本块")
        else:
            chunks = knowledge_base.import_file(path)
            print(f"  ✅ 文件导入完成，共 {chunks} 个文本块")
        print(f"  📚 知识库总量: {knowledge_base.count()} 个文本块")
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")


def main():
    logger.info("正在初始化 LLM Agent...")

    try:
        components = create_agent()
    except ValueError as e:
        logger.error("初始化失败: {}", e)
        print(f"\n❌ {e}")
        print("请复制 .env.example 为 .env 并填入你的 API Key：")
        print("  cp .env.example .env")
        return

    llm_client = components.llm_client
    memory = components.memory
    vector_store = components.vector_store
    knowledge_base = components.knowledge_base
    tool_registry = components.tool_registry
    agent = components.agent

    print("\n🤖 LLM ReAct Agent 已启动")
    print(f"   模型: {llm_client.model}")
    print(f"   已注册工具: {', '.join(tool_registry.tool_names)}")
    print(f"   长期记忆: {'✅ 已启用' if vector_store else '❌ 未启用'}")
    print(f"   知识库(RAG): {'✅ 已启用' if knowledge_base else '❌ 未启用'}")
    print("   命令: /clear /tools /memory /import /kb clear /exit\n")

    while True:
        try:
            user_input = input("👤 You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 再见！")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("\n👋 再见！")
            break

        if user_input == "/clear":
            memory.clear()
            print("🗑️  对话历史已清空\n")
            continue

        if user_input == "/tools":
            print("\n📦 已注册工具:")
            for name in tool_registry.tool_names:
                tool = tool_registry.get(name)
                print(f"   - {name}: {tool.description}")
            print()
            continue

        if user_input == "/memory":
            print(f"\n🧠 状态:")
            print(f"   短期记忆: {memory.token_count} tokens")
            if vector_store:
                print(f"   长期记忆: {vector_store.count()} 条")
            else:
                print(f"   长期记忆: 未启用")
            if knowledge_base:
                print(f"   知识库: {knowledge_base.count()} 个文本块")
            else:
                print(f"   知识库: 未启用")
            print()
            continue

        if user_input.startswith("/import"):
            if knowledge_base:
                handle_import(user_input[len("/import"):], knowledge_base)
            else:
                print("  ❌ 知识库未启用")
            print()
            continue

        if user_input == "/kb clear":
            if knowledge_base:
                knowledge_base.clear()
                print("🗑️  知识库已清空\n")
            else:
                print("  ❌ 知识库未启用\n")
            continue

        try:
            response = agent.run(user_input)
            print(f"\n🤖 Assistant: {response}\n")
        except Exception as e:
            logger.error("Agent 执行失败: {}", e)
            print(f"\n❌ 执行失败: {e}\n")


if __name__ == "__main__":
    main()
