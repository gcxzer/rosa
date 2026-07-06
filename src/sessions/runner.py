"""绑定本地 session 的一轮 agent 对话执行逻辑。

`main.py` 只应该负责解析命令行、创建 agent 和维护交互循环；真正消费
`ROSA.astream()`、打印流式事件、把 user/assistant 消息写入 transcript 的逻辑放在这里。
这样以后如果本地入口、测试入口或其他脚本都要复用同一套 session 行为，不需要复制这段样板。
"""

from __future__ import annotations

from typing import Any

from .store import ROSASessionStore


async def run_session_prompt(
    *,
    agent: Any,
    session_store: ROSASessionStore,
    session_id: str,
    prompt: str,
) -> None:
    """执行一轮对话，并把 user/assistant 消息写入本地 transcript。"""
    session_store.append_message(
        session_id,
        role="user",
        content=prompt,
    )
    printed_token = False
    final_content = ""
    error_content = ""

    # 所有 ROSA 派生 agent 都使用 `astream()` 的统一事件格式；这里集中处理终端展示。
    async for event in agent.astream(prompt):
        event_type = event.get("type")
        if event_type == "token":
            # token 事件表示模型正在流式输出正文片段，需要连续打印在同一行。
            printed_token = True
            print(event.get("content", ""), end="", flush=True)
        elif event_type == "tool_start":
            # tool_start/tool_end 用于观察 agent 调用了哪个工具，以及工具输入输出。
            print(f"\n[tool:start] {event.get('name')} {event.get('input')}")
        elif event_type == "tool_end":
            print(f"\n[tool:end] {event.get('name')} {event.get('output')}")
        elif event_type == "final":
            # 如果前面已经逐 token 打印过正文，final 只补一个换行，避免重复输出。
            final_content = str(event.get("content", "") or "")
            if printed_token:
                print()
            else:
                print(final_content)
        elif event_type == "error":
            error_content = str(event.get("content", "") or "")
            print(f"\n[error] {error_content}")

    # transcript 只保存最终 assistant 文本或错误文本；token/tool 事件只是运行时观察信息。
    session_store.append_message(
        session_id,
        role="assistant",
        content=final_content or error_content,
        metadata={"error": bool(error_content)} if error_content else {},
    )
