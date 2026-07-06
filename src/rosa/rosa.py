#  Copyright (c) 2024. Jet Propulsion Laboratory. All rights reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from __future__ import annotations

from typing import Any, AsyncIterable, Dict, Literal, Optional
from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)

from prompts.system import RobotSystemPrompts, render_system_prompt
from tools.registry import ROSATools


class ROSA:
    """ROSA（Robot Operating System Agent）封装了自然语言到 ROS2 工具调用的核心逻辑。

    Args:
        ros_version (Literal[2]): agent 将要交互的 ROS 版本。当前分支仅支持 ROS2。
        llm (BaseChatModel): 用于生成响应的 LangChain 聊天模型。当前 fork 推荐使用
            `codex.CodexChatModel`，也可以传入其他支持 LangChain tool-calling 的
            BaseChatModel 子类。
        tools (Optional[list]): 额外提供给 agent 使用的 LangChain 工具函数列表。
        tool_packages (Optional[list]): 包含 LangChain 工具函数的 Python 包列表。
        prompts (Optional[RobotSystemPrompts]): 提供给 agent 的自定义机器人 prompt。
        verbose (bool): 是否开启 LangGraph agent debug 输出。默认值为 False。
        blacklist (Optional[list]): 需要从 agent 可见工具结果中排除的 ROS 名称列表。
        streaming (bool): 是否允许通过 `astream()` 流式输出 agent 结果。默认值为 True。
        max_iterations (int): 映射到 LangGraph `recursion_limit`，用于限制一次 agent run 的
            最大图执行步数。
    """

    def __init__(
        self,
        ros_version: Literal[2],
        llm: BaseChatModel,
        tools: Optional[list] = None,
        tool_packages: Optional[list] = None,
        prompts: Optional[RobotSystemPrompts] = None,
        verbose: bool = False,
        blacklist: Optional[list] = None,
        streaming: bool = True,
        max_iterations: int = 100,
    ):
        self.__streaming = streaming
        self.__max_iterations = max_iterations
        self.__session_id = str(uuid4())
        self.__messages: list[dict[str, str]] = []

        # 这里直接完成工具注册。`ROSATools` 负责加载默认 ROS2 工具；
        # 调用者传入的工具和工具包在同一处追加，读代码时不用来回跳转。
        rosa_tools = ROSATools(ros_version, blacklist=blacklist if blacklist else [])
        if tools:
            rosa_tools.add_tools(tools)
        if tool_packages:
            rosa_tools.add_packages(tool_packages)

        system_prompt = render_system_prompt(prompts)
        self.__agent = create_agent(
            model=llm,
            tools=rosa_tools.get_tools(),
            system_prompt=system_prompt,
            debug=verbose,
        )

    @property
    def session_id(self) -> str:
        """返回当前 ROSA session id。"""
        return self.__session_id

    def use_session(
        self,
        session_id: str,
        messages: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """切换到指定 session，并恢复 transcript 历史消息。

        现在 ROSA 不再依赖 LangGraph checkpointer 保存多轮上下文，而是把 user/assistant
        文本消息作为明确的输入历史传给每一轮 agent。这样本地 JSONL transcript 和运行时
        上下文只有一套来源，跨进程恢复也更直接。
        """
        normalized = str(session_id or "").strip()
        self.__session_id = normalized or str(uuid4())
        self.__messages = [
            {
                "role": str(message.get("role") or ""),
                "content": str(message.get("content") or ""),
            }
            for message in messages or []
            if str(message.get("role") or "") in {"user", "assistant"}
            and str(message.get("content") or "")
        ]

    def clear_chat(self):
        """切换到新的 ROSA session，让后续调用从空对话开始。"""
        self.__session_id = str(uuid4())
        self.__messages = []

    def invoke(self, query: str) -> str:
        """使用用户查询调用 agent，并返回最终响应字符串。"""
        try:
            result = self.__agent.invoke(
                {"messages": self._input_messages(query)},
                config=self._runtime_config(),
            )
        except KeyboardInterrupt:
            # 重新抛出 KeyboardInterrupt，让上层调用者可以正确处理中断。
            raise
        except Exception as e:
            return f"发生错误：{str(e)}"

        # agent state 里最后一条没有 tool_calls 的 AIMessage，就是本轮最终文本回答。
        for message in reversed(result.get("messages", [])):
            if isinstance(message, AIMessage) and not message.tool_calls:
                response = self._message_text(message)
                self._record_turn(query, response)
                return response
        return ""

    async def astream(self, query: str) -> AsyncIterable[Dict[str, Any]]:
        """以异步流的形式返回 agent 对用户查询的规范化事件。

        这里使用 LangChain/LangGraph 当前的 `stream_mode=["messages", "updates"]`：
        `messages` 负责模型 token，`updates` 负责完整工具调用和工具结果。ROSA 对外只暴露
        稳定的事件字典，避免上层调用者依赖 LangChain 内部节点名或事件名。
        """
        if not self.__streaming:
            raise ValueError(
                "当前未启用 streaming。请改用 'invoke' 方法，或在初始化 ROSA 时设置 streaming=True。"
            )

        final_output = ""
        try:
            async for chunk in self.__agent.astream(
                {"messages": self._input_messages(query)},
                config=self._runtime_config(),
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                if chunk.get("type") == "messages":
                    token, _metadata = chunk["data"]
                    if isinstance(token, AIMessageChunk) and not token.tool_call_chunks:
                        content = self._message_text(token)
                        if content:
                            yield {"type": "token", "content": content}
                    continue

                if chunk.get("type") != "updates":
                    continue

                for update in chunk["data"].values():
                    if not isinstance(update, dict) or not update.get("messages"):
                        continue

                    message = update["messages"][-1]
                    if isinstance(message, AIMessage) and message.tool_calls:
                        for tool_call in message.tool_calls:
                            yield {
                                "type": "tool_start",
                                "name": tool_call.get("name"),
                                "input": tool_call.get("args", {}),
                            }
                    elif isinstance(message, ToolMessage):
                        yield {
                            "type": "tool_end",
                            "name": getattr(message, "name", None),
                            "output": self._message_text(message),
                        }
                    elif isinstance(message, AIMessage):
                        content = self._message_text(message)
                        if content:
                            final_output = content

            self._record_turn(query, final_output)
            yield {"type": "final", "content": final_output}
        except KeyboardInterrupt:
            # 将用户中断转换成 streaming error 事件，方便上层 UI 统一展示。
            yield {"type": "error", "content": "操作已被用户中断"}
        except Exception as e:
            yield {"type": "error", "content": f"发生错误：{e}"}

    def _runtime_config(self) -> dict[str, Any]:
        """生成 LangGraph 每次 invoke/astream 需要的运行配置。"""
        config: dict[str, Any] = {}
        if self.__max_iterations:
            # `recursion_limit` 是 LangGraph 控制图执行步数的通用开关。这里至少给 2，
            # 避免用户传 1 时连模型节点和工具节点都不够跑。
            config["recursion_limit"] = max(2, self.__max_iterations)
        return config

    def _input_messages(self, query: str) -> list[dict[str, str]]:
        """生成本轮输入消息。"""
        return [*self.__messages, {"role": "user", "content": query}]

    def _record_turn(self, query: str, response: str) -> None:
        """把本轮 user/assistant 文本写入当前进程内的上下文。"""
        self.__messages.append({"role": "user", "content": query})
        if response:
            self.__messages.append({"role": "assistant", "content": response})

    def _message_text(self, message: BaseMessage | AIMessageChunk) -> str:
        """把 LangChain message/chunk 的多种 content 形状规范成字符串。"""
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    # LangChain v1 的 content block 常见形状是 {"type": "text", "text": "..."}。
                    # 如果后续模型返回 reasoning/tool 等 block，这里只提取可展示文本。
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        text_attr = getattr(message, "text", None)
        if callable(text_attr):
            return str(text_attr())
        if isinstance(text_attr, str):
            return text_attr
        return str(content) if content is not None else ""
