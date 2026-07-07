"""ROSA 本地统一入口。

当前入口支持 TurtleAgent 和 ArmAgent。新增机器人 agent 时，继续在这个统一入口里扩展
`--agent` choices 和创建逻辑，不再新增多个 main 文件。

运行示例：

    uv run python main.py --agent turtle "画一个边长为 2 的正方形"
    uv run python main.py --agent arm "检查机械臂运行栈是否就绪"
    uv run python main.py --agent turtle --session-id 20260706_120000_abcd1234
"""

from __future__ import annotations

import argparse
import asyncio

from arm_agent import ArmAgent
from sessions import ROSASessionStore, SessionNotFoundError, run_session_prompt
from turtle_agent import TurtleAgent


async def main() -> None:
    """解析命令行参数，创建指定 agent，并消费 `ROSA.astream()` 的事件流。"""
    parser = argparse.ArgumentParser(description="ROSA 本地统一测试入口")
    parser.add_argument("prompt", nargs="*", help="要发送给 agent 的文本；提供时会先作为多轮对话的第一条消息。")
    parser.add_argument("--agent", default="turtle", choices=["turtle", "arm"], help="要启动的 agent。turtle 用于 turtlesim，arm 用于 MoveIt2 + MuJoCo 机械臂。")
    parser.add_argument("--model", default="gpt-5.5", help="传给 Codex Responses API 的模型名。")
    parser.add_argument("--thinking", default="none", choices=["none", "low", "medium", "high", "xhigh"], help="Codex reasoning effort。none 表示不覆盖默认 thinking。")
    parser.add_argument("--session-id", default="", help="继续已有 session；不传时会创建新 session。")
    args = parser.parse_args()

    # 命令行里不加引号时，prompt 可能被 shell 拆成多个片段；这里统一拼回一句话。
    prompt = " ".join(args.prompt).strip()

    # 如果用户只运行 `uv run python main.py --agent turtle`，仍然先发一条最小问候消息。
    if not prompt:
        prompt = "你好，你是谁？"
    thinking = None if args.thinking == "none" else args.thinking
    # 本地调试入口固定把 session 放到项目下的 .rosa/sessions，避免 CLI 参数越来越像管理工具。
    session_store = ROSASessionStore(".rosa/sessions")

    if args.agent == "turtle":
        agent = TurtleAgent(model=args.model, thinking=thinking, streaming=True)
        agent_title = "TurtleAgent chat"
    else:
        agent = ArmAgent(model=args.model, thinking=thinking, streaming=True)
        agent_title = "ArmAgent chat"

    if args.session_id:
        try:
            session = session_store.require_session(args.session_id)
        except SessionNotFoundError:
            print(f"找不到 session：{args.session_id}")
            return
        agent.use_session(
            session.metadata.session_id,
            session_store.transcript_messages_for_langchain(session.metadata.session_id),
        )
        print(f"继续 session：{session.metadata.session_id} - {session.metadata.title}")
    else:
        title_source = prompt or agent_title
        session = session_store.create_session(
            title=title_source[:80],
            agent=args.agent,
            provider="codex",
            model=args.model,
        )
        agent.use_session(session.metadata.session_id, [])
        print(f"新建 session：{session.metadata.session_id} - {session.metadata.title}")

    print("进入多轮对话。输入 exit/quit 退出，输入 clear/new 新建 session。")
    # 如果命令行已经带了 prompt，先把它当作第一条用户消息发送；后续继续停在交互循环里。
    queued_prompt = prompt
    while True:
        if queued_prompt:
            current_prompt = queued_prompt
            queued_prompt = ""
            print(f"\n> {current_prompt}")
        else:
            try:
                current_prompt = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                # 例如用户按 Ctrl-D/Ctrl-C，或者脚本在非交互环境里没有 stdin；这里干净退出。
                print()
                break

        if not current_prompt:
            continue
        if current_prompt in {"exit", "quit"}:
            break
        if current_prompt in {"clear", "new"}:
            # 不覆盖旧 transcript；新建 session 后切到一段空的消息历史。
            session = session_store.create_session(
                title=agent_title,
                agent=args.agent,
                provider="codex",
                model=args.model,
            )
            agent.use_session(session.metadata.session_id, [])
            print(f"已新建 session：{session.metadata.session_id}")
            continue

        await run_session_prompt(
            agent=agent,
            session_store=session_store,
            session_id=session.metadata.session_id,
            prompt=current_prompt,
        )


if __name__ == "__main__":
    asyncio.run(main())
