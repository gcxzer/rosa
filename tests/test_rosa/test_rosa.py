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

import asyncio
from typing import Any, Iterator, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool, tool
from pydantic import PrivateAttr

from prompts.system import RobotSystemPrompts
from rosa import ROSA


class SequenceChatModel(BaseChatModel):
    """测试用的最小 LangChain ChatModel，可记录 agent 传入的 messages。

    这个 fake model 只服务 ROSA runtime 测试：同步调用时按顺序吐出 `_responses`，
    streaming 调用时按顺序吐出 `_stream_responses`。它实现 `bind_tools()`，这样
    LangChain v1 `create_agent` 可以像使用真实 tool-calling 模型一样使用它。
    """

    _responses: list[AIMessage] = PrivateAttr(default_factory=list)
    _stream_responses: list[list[AIMessageChunk]] = PrivateAttr(default_factory=list)
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tools: list[Any] = PrivateAttr(default_factory=list)

    def __init__(
        self,
        responses: Optional[list[AIMessage]] = None,
        stream_responses: Optional[list[list[AIMessageChunk]]] = None,
    ):
        super().__init__()
        self._responses = list(responses or [])
        self._stream_responses = list(stream_responses or [])

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    @property
    def _llm_type(self) -> str:
        return "sequence-chat-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SequenceChatModel":
        self._bound_tools = list(tools)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._calls.append(messages)
        message = self._responses.pop(0) if self._responses else AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self._calls.append(messages)
        chunks = self._stream_responses.pop(0) if self._stream_responses else []
        for chunk in chunks:
            yield ChatGenerationChunk(message=chunk)


def collect_stream(agent: ROSA, query: str) -> list[dict[str, Any]]:
    async def _collect() -> list[dict[str, Any]]:
        return [event async for event in agent.astream(query)]

    return asyncio.run(_collect())


def test_invoke_uses_messages_state_and_returns_final_text():
    model = SequenceChatModel(responses=[AIMessage(content="收到")])
    agent = ROSA(ros_version=2, llm=model)

    result = agent.invoke("列出 topic")

    assert result == "收到"
    assert any(isinstance(message, HumanMessage) and message.content == "列出 topic" for message in model.calls[0])
    assert any(message.type == "system" for message in model.calls[0])


def test_repeated_rosa_construction_does_not_duplicate_custom_prompts():
    model_with_custom_prompt = SequenceChatModel(responses=[AIMessage(content="完成")])
    custom_prompt = RobotSystemPrompts(mission_and_objectives="只检查实验台 A。")
    ROSA(ros_version=2, llm=model_with_custom_prompt, prompts=custom_prompt).invoke("开始")

    plain_model = SequenceChatModel(responses=[AIMessage(content="完成")])
    ROSA(ros_version=2, llm=plain_model).invoke("开始")
    plain_system_text = "\n".join(
        str(message.content) for message in plain_model.calls[0] if message.type == "system"
    )

    assert "只检查实验台 A" not in plain_system_text


def test_repeated_invokes_reuse_in_process_message_history():
    model = SequenceChatModel(
        responses=[AIMessage(content="第一轮回答"), AIMessage(content="第二轮回答")]
    )
    agent = ROSA(ros_version=2, llm=model)

    assert agent.invoke("第一轮问题") == "第一轮回答"
    assert agent.invoke("第二轮问题") == "第二轮回答"

    second_call_text = "\n".join(str(message.content) for message in model.calls[1])
    assert "第一轮问题" in second_call_text
    assert "第一轮回答" in second_call_text
    assert "第二轮问题" in second_call_text


def test_clear_chat_resets_message_history():
    model = SequenceChatModel(
        responses=[AIMessage(content="第一轮回答"), AIMessage(content="第二轮回答")]
    )
    agent = ROSA(ros_version=2, llm=model)

    agent.invoke("第一轮问题")
    agent.clear_chat()
    assert agent.invoke("第二轮问题") == "第二轮回答"

    second_call_text = "\n".join(str(message.content) for message in model.calls[1])
    assert "第一轮问题" not in second_call_text
    assert "第一轮回答" not in second_call_text
    assert "第二轮问题" in second_call_text


def test_use_session_restores_transcript_history_without_duplicate_injection():
    model = SequenceChatModel(
        responses=[AIMessage(content="第二轮回答"), AIMessage(content="第三轮回答")]
    )
    agent = ROSA(ros_version=2, llm=model)
    agent.use_session(
        "saved-session",
        [
            {"role": "user", "content": "第一轮问题"},
            {"role": "assistant", "content": "第一轮回答"},
        ],
    )

    assert agent.session_id == "saved-session"
    assert agent.invoke("第二轮问题") == "第二轮回答"
    assert agent.invoke("第三轮问题") == "第三轮回答"

    first_call_text = "\n".join(str(message.content) for message in model.calls[0])
    assert "第一轮问题" in first_call_text
    assert "第一轮回答" in first_call_text
    assert "第二轮问题" in first_call_text

    second_call_text = "\n".join(str(message.content) for message in model.calls[1])
    assert second_call_text.count("第一轮问题") == 1
    assert second_call_text.count("第一轮回答") == 1
    assert "第三轮问题" in second_call_text


def test_blacklist_injection_runs_inside_create_agent_tool_execution():
    @tool
    def report_blacklist(blacklist: list[str] | None = None) -> str:
        """返回工具实际收到的 blacklist。"""
        return ",".join(blacklist or [])

    model = SequenceChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report_blacklist",
                        "args": {},
                        "id": "call_report_blacklist",
                    }
                ],
            ),
            AIMessage(content="工具完成"),
        ]
    )
    agent = ROSA(
        ros_version=2,
        llm=model,
        tools=[report_blacklist],
        blacklist=["隐藏项"],
    )

    assert agent.invoke("检查黑名单") == "工具完成"
    tool_messages = [
        message for message in model.calls[1] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].content == "隐藏项"


def test_astream_maps_tokens_and_single_final_event():
    model = SequenceChatModel(
        stream_responses=[
            [AIMessageChunk(content="你"), AIMessageChunk(content="好")]
        ]
    )
    agent = ROSA(ros_version=2, llm=model, streaming=True)

    events = collect_stream(agent, "流式回答")

    assert events == [
        {"type": "token", "content": "你"},
        {"type": "token", "content": "好"},
        {"type": "final", "content": "你好"},
    ]


def test_astream_maps_tool_lifecycle_events():
    @tool
    def echo_value(value: str) -> str:
        """回显传入的 value。"""
        return f"回显：{value}"

    model = SequenceChatModel(
        stream_responses=[
            [
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "echo_value",
                            "args": '{"value":"ping"}',
                            "id": "call_echo_value",
                            "index": 0,
                        }
                    ],
                )
            ],
            [AIMessageChunk(content="完成")],
        ]
    )
    agent = ROSA(ros_version=2, llm=model, tools=[echo_value], streaming=True)

    events = collect_stream(agent, "调用工具")

    assert events == [
        {"type": "tool_start", "name": "echo_value", "input": {"value": "ping"}},
        {"type": "tool_end", "name": "echo_value", "output": "回显：ping"},
        {"type": "token", "content": "完成"},
        {"type": "final", "content": "完成"},
    ]
