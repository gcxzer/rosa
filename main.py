"""ROSA 本地统一入口。

现在仓库里只有 TurtleAgent，因此 `--agent` 当前只支持 turtle。后续如果继续增加别的
机器人 agent，可以在这个入口里继续扩展 choices 和创建逻辑，而不是再新增多个 main 文件。

运行示例：

    uv run python main.py --agent turtle "画一个边长为 2 的正方形"
"""

from __future__ import annotations

import argparse
import asyncio

from turtle_agent import TurtleAgent


async def main() -> None:
    """解析命令行参数，创建指定 agent，并消费 `ROSA.astream()` 的事件流。"""
    parser = argparse.ArgumentParser(description="ROSA 本地统一测试入口")
    parser.add_argument(
        "prompt",
        nargs="*",
        help="要发送给 agent 的文本；不填时使用当前 agent 的最小检查 prompt。",
    )
    parser.add_argument(
        "--agent",
        default="turtle",
        choices=["turtle"],
        help="要启动的 agent。当前只有 turtle。",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.5",
        help="传给 Codex Responses API 的模型名。",
    )
    parser.add_argument(
        "--thinking",
        default="none",
        choices=["none", "low", "medium", "high", "xhigh"],
        help="Codex reasoning effort。none 表示不覆盖默认 thinking。",
    )
    args = parser.parse_args()

    # 命令行里不加引号时，prompt 可能被 shell 拆成多个片段；这里统一拼回一句话。
    prompt = " ".join(args.prompt).strip()
    thinking = None if args.thinking == "none" else args.thinking

    if args.agent == "turtle":
        agent = TurtleAgent(model=args.model, thinking=thinking, streaming=True)
        if not prompt:
            prompt = "列出 turtlesim 当前的 ROS2 node、topic 和 service"

    printed_token = False
    # 所有 agent 都使用 ROSA.astream() 的统一事件格式；入口只负责展示，不关心具体 agent 内部实现。
    async for event in agent.astream(prompt):
        event_type = event.get("type")
        if event_type == "token":
            # token 事件表示模型正在流式输出正文片段，需要连续打印在同一行。
            printed_token = True
            print(event.get("content", ""), end="", flush=True)
        elif event_type == "tool_start":
            # tool_start/tool_end 用于观察 agent 调用了哪个工具，以及输入输出。
            print(f"\n[tool:start] {event.get('name')} {event.get('input')}")
        elif event_type == "tool_end":
            print(f"\n[tool:end] {event.get('name')} {event.get('output')}")
        elif event_type == "final":
            # 如果前面已经逐 token 打印过正文，final 只补一个换行，避免重复输出。
            if printed_token:
                print()
            else:
                print(event.get("content", ""))
        elif event_type == "error":
            print(f"\n[error] {event.get('content', '')}")


if __name__ == "__main__":
    asyncio.run(main())
