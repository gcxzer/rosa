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

from typing import Any, Iterator, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from openai import OpenAI
from pydantic import Field

from .auth import DEFAULT_CODEX_BASE_URL, codex_default_headers, runtime_codex_credentials
from .responses import (
    backfill_stream_output,
    codex_tool_spec,
    final_generation_chunk_from_response,
    get_attr,
    message_from_responses_response,
    responses_payload,
    stream_chunk_from_responses_event,
)


class CodexChatModel(BaseChatModel):
    """把 Codex Responses API 包装成 LangChain `BaseChatModel`。

    这里参考 Paper Notes 的实现：不再通过 prompt 要求 Codex 手写 JSON 工具调用，
    而是把 LangChain tools 转成 Responses API 的 function tools。Codex 后端返回
    `function_call` 后，本适配器再转换为 LangChain 标准 `AIMessage.tool_calls`，
    由 `create_agent` 负责真正执行 ROSA 工具。
    """

    model: str = "gpt-5.5"
    thinking: Optional[str] = None
    auth_path: Optional[str] = None
    base_url: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: Optional[Any] = None
    client: Any | None = Field(default=None, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "openai-codex-responses"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "thinking": self.thinking,
            "base_url": self.base_url,
        }

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "CodexChatModel":
        """保存 Responses API 可直接接收的 function tool schema。"""
        options = {**self.options, **kwargs} if kwargs else dict(self.options)
        return self.model_copy(
            update={
                "options": options,
                "bound_tools": [codex_tool_spec(tool) for tool in tools],
                "tool_choice": tool_choice,
            }
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """执行一次非流式 Responses 请求，并返回 LangChain `ChatResult`。"""
        del stop, run_manager
        options = {**self.options, **kwargs}
        payload = self._payload(messages, options)
        client = self._codex_openai_client(options)
        create = getattr(getattr(client, "responses", None), "create", None)
        if not callable(create):
            raise RuntimeError("Codex Responses client does not provide responses.create.")
        response = create(**payload)
        message = message_from_responses_response(response)
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=message,
                    generation_info=dict(message.response_metadata or {}),
                )
            ],
            llm_output={
                "usage": message.response_metadata.get("usage")
                if isinstance(message.response_metadata, dict)
                else None
            },
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """执行一次流式 Responses 请求，并产出 LangChain message chunks。"""
        del stop, run_manager
        options = {**self.options, **kwargs}
        client = self._codex_openai_client(options)
        payload = self._payload(messages, options)
        stream_factory = getattr(getattr(client, "responses", None), "stream", None)
        if not callable(stream_factory):
            raise RuntimeError("Codex Responses client does not provide responses.stream.")

        streamed_content = False
        final_response: Any | None = None
        terminal_response: Any | None = None
        collected_output_items: list[Any] = []
        collected_text_deltas: list[str] = []
        with stream_factory(**payload) as stream:
            for event in stream:
                event_type = str(get_attr(event, "type", "") or "")
                if event_type in {"response.output_item.done", "response.output_item.completed"}:
                    item = get_attr(event, "item", None)
                    if item is not None:
                        collected_output_items.append(item)
                        collected_text_deltas.clear()
                elif event_type in {"response.output_text.delta", "response.text.delta"}:
                    delta = str(get_attr(event, "delta", "") or "")
                    if delta and not collected_output_items and not get_attr(terminal_response, "output", None):
                        collected_text_deltas.append(delta)
                elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
                    terminal_response = get_attr(event, "response", None) or terminal_response
                    if get_attr(terminal_response, "output", None):
                        collected_text_deltas.clear()

                for chunk in stream_chunk_from_responses_event(event):
                    if str(chunk.message.content or ""):
                        streamed_content = True
                    yield chunk

            get_final_response = getattr(stream, "get_final_response", None)
            if callable(get_final_response):
                final_response = get_final_response()

        final_response = backfill_stream_output(
            final_response or terminal_response,
            collected_output_items=collected_output_items,
            collected_text_deltas=collected_text_deltas,
        )
        if final_response is None:
            raise RuntimeError("Codex Responses stream completed without a final response.")
        yield final_generation_chunk_from_response(final_response, suppress_content=streamed_content)

    def _payload(self, messages: list[BaseMessage], options: dict[str, Any]) -> dict[str, Any]:
        """构造 Responses API payload，并集中校验 ROSA 暴露的模型选项。"""
        thinking = self.thinking
        if thinking == "none":
            thinking = None
        elif thinking not in {None, "low", "medium", "high", "xhigh"}:
            raise ValueError(
                "无效的 thinking mode。可用值包括 none、low、medium、high、xhigh。"
            )
        return responses_payload(
            messages,
            model=self.model,
            options=options,
            tools=self.bound_tools,
            tool_choice=self.tool_choice,
            thinking=thinking,
        )

    def _codex_openai_client(self, options: dict[str, Any]) -> Any:
        """创建访问 ChatGPT Codex 后端的 OpenAI client。"""
        if self.client is not None:
            return self.client
        auth_path = self.auth_path or options.get("auth_path")
        credentials = runtime_codex_credentials(auth_path=auth_path)
        if not credentials.access_token:
            raise RuntimeError("Codex OAuth 未连接。请先在 Codex 中登录，生成 ~/.codex/auth.json。")
        return OpenAI(
            api_key=credentials.access_token,
            base_url=str(self.base_url or options.get("base_url") or credentials.base_url or DEFAULT_CODEX_BASE_URL).rstrip("/"),
            default_headers=codex_default_headers(credentials),
        )
