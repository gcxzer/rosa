"""Codex Responses API 和 LangChain message/tool 格式之间的转换。

这个模块只做结构转换：LangChain messages -> Responses payload，Responses response
-> LangChain message/chunk。这里不再塞额外“让模型怎么用工具”的提示词；工具行为由
ROSA system prompt 和 Responses API 的 function tools 共同约束。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, Sequence

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool


CODEX_RESPONSES_OPTIONS = {
    "include",
    "parallel_tool_calls",
    "prompt_cache_key",
    "service_tier",
    "text",
    "top_p",
    "truncation",
}


def codex_tool_spec(tool: dict[str, Any] | type | Any | BaseTool) -> dict[str, Any]:
    """把 LangChain tool schema 转成 Responses API 的 function tool schema。"""
    converted = convert_to_openai_tool(tool)
    function = converted.get("function") if isinstance(converted, dict) else None
    if not isinstance(function, dict):
        return converted if isinstance(converted, dict) else {}
    return {
        "name": str(function.get("name") or ""),
        "description": str(function.get("description") or ""),
        "parameters": function.get("parameters")
        if isinstance(function.get("parameters"), dict)
        else {"type": "object"},
        **({"strict": bool(function["strict"])} if "strict" in function else {}),
    }


def responses_payload(
    messages: list[BaseMessage],
    *,
    model: str,
    options: dict[str, Any],
    tools: Sequence[dict[str, Any]],
    tool_choice: Any,
    thinking: str | None,
) -> dict[str, Any]:
    """构造 `client.responses.create/stream` 可直接使用的 payload。"""
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = str(getattr(message, "type", "") or "").strip()
        raw_content = getattr(message, "content", "")
        if isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            text_parts: list[str] = []
            for item in raw_content:
                # LangChain content 可以是纯字符串，也可以是带 type/text/content 的块；
                # Responses 的 instructions 和工具输出只需要可见文本，所以这里压成字符串。
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text", item.get("content", ""))
                    text_parts.append(text if isinstance(text, str) else json.dumps(item, ensure_ascii=False))
                else:
                    text_parts.append(str(item))
            content = "\n".join(part for part in text_parts if part)
        else:
            content = str(raw_content) if raw_content is not None else ""

        if role in {"system", "developer"}:
            # LangChain 的 system/developer message 在 Responses API 中对应
            # `instructions`，不应该混入 user/assistant 对话 input。
            if content:
                instructions.append(content)
            continue

        if role == "human":
            if isinstance(raw_content, list):
                parts: list[dict[str, Any]] = []
                for item in raw_content:
                    # Responses API 的多模态输入块和 LangChain 的 content block 字段名
                    # 不完全一致，这里只做必要的文本/图片字段转换。
                    if isinstance(item, str):
                        parts.append({"type": "input_text", "text": item})
                    elif not isinstance(item, dict):
                        text = str(item) if item is not None else ""
                        if text:
                            parts.append({"type": "input_text", "text": text})
                    else:
                        part_type = str(item.get("type") or "").strip()
                        if part_type in {"text", "input_text"}:
                            text = item.get("text", item.get("content", ""))
                            if text is not None:
                                parts.append({"type": "input_text", "text": str(text)})
                        elif part_type in {"image_url", "input_image"}:
                            image_url = item.get("image_url") or item.get("url")
                            if isinstance(image_url, dict):
                                image_url = image_url.get("url")
                            if image_url:
                                parts.append({"type": "input_image", "image_url": str(image_url)})
                        else:
                            text = item.get("text", item.get("content", ""))
                            if isinstance(text, str) and text:
                                parts.append({"type": "input_text", "text": text})
                input_items.append({"role": "user", "content": parts or content})
            else:
                input_items.append({"role": "user", "content": content})
            continue

        if role == "ai":
            if content:
                input_items.append({"role": "assistant", "content": content})

            for index, tool_call in enumerate(getattr(message, "tool_calls", None) or []):
                # 如果上一轮模型请求过工具，下一轮请求必须把该 function_call 回放给
                # Responses API；这样紧随其后的 function_call_output 才能对上 call_id。
                if not isinstance(tool_call, dict):
                    continue
                name = str(tool_call.get("name") or "").strip()
                if not name:
                    continue
                args = tool_call.get("args", {})
                arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}_{index}"),
                        "name": name,
                        "arguments": arguments or "{}",
                    }
                )
            continue

        if role == "tool":
            call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if call_id:
                input_items.append({"type": "function_call_output", "call_id": call_id, "output": content})
            continue

        if content:
            # 兜底处理未知 LangChain message 类型：把可见文本当作 user input，避免
            # 因 provider 新增 message type 直接丢失用户可见内容。
            input_items.append({"role": "user", "content": content})

    if not input_items:
        input_items.append({"role": "user", "content": ""})

    payload: dict[str, Any] = {
        "model": model,
        "input": input_items,
        # ROSA 不需要把机器人上下文写入 OpenAI 侧存储，保持本地调试更可控。
        "store": False,
    }

    instruction_parts = [
        "\n\n".join(part for part in instructions if part).strip(),
        str(options.get("developer_instructions") or ""),
        str(options.get("base_instructions") or ""),
    ]
    resolved_instructions = "\n\n".join(
        part.strip() for part in instruction_parts if isinstance(part, str) and part.strip()
    )
    if resolved_instructions:
        payload["instructions"] = resolved_instructions

    response_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip()
        if tool_type and tool_type != "function":
            response_tools.append(dict(tool))
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        response_tools.append(
            {
                "type": "function",
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("parameters")
                if isinstance(tool.get("parameters"), dict)
                else {"type": "object"},
                **({"strict": bool(tool["strict"])} if "strict" in tool else {}),
            }
        )

    if response_tools:
        payload["tools"] = response_tools

        # LangChain 的 tool_choice 和 Responses API 的字段形状不同，这里只做明确映射，
        # 不再用 prompt 文字提醒模型。
        if isinstance(tool_choice, dict):
            payload["tool_choice"] = tool_choice
        else:
            choice = str(tool_choice or "auto").strip()
            if choice in {"", "auto"}:
                payload["tool_choice"] = "auto"
            elif choice == "none":
                payload["tool_choice"] = "none"
            elif choice == "any":
                payload["tool_choice"] = "required"
            else:
                payload["tool_choice"] = {"type": "function", "name": choice}

        # ROSA 系统 prompt 已要求工具顺序执行；这里在请求层同步关闭并行工具调用。
        payload["parallel_tool_calls"] = False

    reasoning = dict(options["reasoning"]) if isinstance(options.get("reasoning"), dict) else {}
    effort = thinking or options.get("effort") or options.get("reasoning_effort")
    if effort and effort != "none":
        reasoning["effort"] = effort
        reasoning.setdefault("summary", options.get("summary") or "auto")
    if reasoning:
        payload["reasoning"] = reasoning

    for key in CODEX_RESPONSES_OPTIONS:
        value = options.get(key)
        if value is not None and key not in payload:
            payload[key] = value
    return payload


def message_from_responses_response(response: Any) -> AIMessage:
    """把 Codex Responses API 的完成响应解析成 LangChain `AIMessage`。"""
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    output = get_attr(response, "output", None)
    output_items = output if isinstance(output, list) else []

    for item in output_items:
        item_type = str(get_attr(item, "type", "") or "")
        if item_type == "message":
            raw_parts = get_attr(item, "content", None)
            if not isinstance(raw_parts, list):
                continue
            text_parts: list[str] = []
            for part in raw_parts:
                # Responses message content 可能混有非文本块；最终 assistant 文本只取
                # output_text/text，避免把结构化块直接 stringify 给用户。
                part_type = str(get_attr(part, "type", "") or "")
                if part_type not in {"output_text", "text"}:
                    continue
                text = get_attr(part, "text", "")
                if isinstance(text, str):
                    text_parts.append(text)
            text = "".join(text_parts).strip()
            if text:
                content_parts.append(text)
            continue

        if item_type in {"function_call", "custom_tool_call"}:
            name = str(get_attr(item, "name", "") or "").strip()
            if not name:
                continue
            raw_args = get_attr(item, "input", "{}") if item_type == "custom_tool_call" else get_attr(item, "arguments", "{}")
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    parsed = {"input": raw_args}
                args = parsed if isinstance(parsed, dict) else {"input": parsed}
            else:
                args = {"input": raw_args} if raw_args is not None else {}
            tool_calls.append(
                {
                    "name": name,
                    "args": args,
                    "id": str(
                        get_attr(item, "call_id", "")
                        or get_attr(item, "id", "")
                        or f"call_{uuid.uuid4().hex[:12]}_{len(tool_calls)}"
                    ),
                }
            )

    content = "\n".join(part for part in content_parts if part).strip()
    if not content:
        content = str(get_attr(response, "output_text", "") or "").strip()

    metadata: dict[str, Any] = {
        "response_id": get_attr(response, "id", None),
        "status": str(get_attr(response, "status", "") or ""),
    }
    usage = get_attr(response, "usage", None)
    if usage is not None:
        input_tokens = _first_int(usage, "input_tokens", "prompt_tokens", "inputTokens", "promptTokens")
        output_tokens = _first_int(usage, "output_tokens", "completion_tokens", "outputTokens", "completionTokens")
        total_tokens = _first_int(usage, "total_tokens", "totalTokens") or input_tokens + output_tokens
        metadata["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    return AIMessage(
        content="" if tool_calls else content,
        tool_calls=tool_calls,
        response_metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
    )


def stream_chunk_from_responses_event(event: Any) -> list[ChatGenerationChunk]:
    """把 Responses 流式事件转换成 LangChain token chunk。"""
    event_type = str(get_attr(event, "type", "") or "")
    if event_type in {"response.output_text.delta", "response.text.delta"}:
        delta = str(get_attr(event, "delta", "") or "")
        if delta:
            return [ChatGenerationChunk(message=AIMessageChunk(content=delta))]
    return []


def final_generation_chunk_from_response(response: Any, *, suppress_content: bool) -> ChatGenerationChunk:
    """把最终 response 补成 LangChain streaming 的最后一个 chunk。"""
    message = message_from_responses_response(response)
    tool_call_chunks: list[dict[str, Any]] = []
    for index, tool_call in enumerate(message.tool_calls):
        # LangChain streaming 的工具调用使用字符串形式 args；完整 dict 已经保存在
        # non-streaming message.tool_calls 中，这里只负责补齐 chunk 协议字段。
        tool_call_chunks.append(
            {
                "name": str(tool_call.get("name") or ""),
                "args": json.dumps(
                    tool_call.get("args") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}_{index}"),
                "index": index,
            }
        )
    return ChatGenerationChunk(
        message=AIMessageChunk(
            content="" if tool_call_chunks or suppress_content else str(message.content or ""),
            tool_call_chunks=tool_call_chunks,
            chunk_position="last",
            response_metadata=dict(message.response_metadata or {}),
        ),
        generation_info=dict(message.response_metadata or {}),
    )


def backfill_stream_output(
    response: Any | None,
    *,
    collected_output_items: list[Any],
    collected_text_deltas: list[str],
) -> Any | None:
    """部分 Codex stream 终态缺 output 时，用已收到的事件补齐最终 response。"""
    if response is None:
        if not collected_output_items and not collected_text_deltas:
            return None
        response = SimpleNamespace(id="", status="completed", output=[], output_text="", usage=None)

    output = get_attr(response, "output", None)
    if isinstance(output, list) and output:
        return response
    if collected_output_items:
        set_attr(response, "output", list(collected_output_items))
        return response
    if collected_text_deltas:
        text = "".join(collected_text_deltas)
        set_attr(
            response,
            "output",
            [
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[SimpleNamespace(type="output_text", text=text)],
                )
            ],
        )
        if not get_attr(response, "output_text", ""):
            set_attr(response, "output_text", text)
    return response


def get_attr(item: Any, name: str, default: Any = None) -> Any:
    """同时支持 dict 和 SDK object 的属性读取。"""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def set_attr(item: Any, name: str, value: Any) -> None:
    """同时支持 dict 和 SDK object 的属性写入。"""
    if isinstance(item, dict):
        item[name] = value
        return
    try:
        setattr(item, name, value)
    except Exception:
        pass


def _first_int(value: Any, *keys: str) -> int:
    for key in keys:
        raw = get_attr(value, key, None)
        if isinstance(raw, bool):
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return 0
