import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from codex import CodexChatModel
from codex.auth import runtime_codex_credentials
from rosa import ROSA


@tool
def sample_tool(value: int) -> int:
    """返回传入的整数。"""
    return value


class FakeResponses:
    """测试用 Responses API client，记录 payload，并按队列返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses if isinstance(responses, list) else [responses])
        self.payloads: list[dict] = []
        self.stream_events = None

    def create(self, **payload):
        self.payloads.append(payload)
        return self.responses.pop(0)

    def stream(self, **payload):
        self.payloads.append(payload)
        response = self.responses.pop(0)
        return FakeStream(response, events=self.stream_events)


class FakeClient:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)


class FakeStream:
    def __init__(self, response, *, events=None):
        self.response = response
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def __iter__(self):
        return iter(
            self.events
            or [
                SimpleNamespace(type="response.output_text.delta", delta="Hel"),
                SimpleNamespace(type="response.output_text.delta", delta="lo"),
            ]
        )

    def get_final_response(self):
        return self.response


def message_response(text: str, *, usage=None):
    return SimpleNamespace(
        id="resp_text",
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=usage,
    )


def tool_call_response(name: str = "sample_tool", arguments: str = '{"value":3}'):
    return SimpleNamespace(
        id="resp_tool",
        status="completed",
        output=[
            SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name=name,
                arguments=arguments,
                status="completed",
            )
        ],
        usage=None,
    )


def empty_response():
    return SimpleNamespace(id="resp_empty", status="completed", output=[], usage=None)


def test_codex_chat_model_uses_responses_tools_without_prompt_json_protocol():
    client = FakeClient(message_response("你好"))
    model = CodexChatModel(model="gpt-5.5", client=client).bind_tools([sample_tool])

    result = model.invoke([HumanMessage(content="打个招呼")])

    payload = client.responses.payloads[0]
    assert result.content == "你好"
    assert result.tool_calls == []
    assert payload["model"] == "gpt-5.5"
    assert payload["store"] is False
    assert payload["tools"][0]["name"] == "sample_tool"
    assert payload["tools"][0]["type"] == "function"
    assert payload["parallel_tool_calls"] is False


def test_codex_chat_model_converts_responses_function_call_to_langchain_tool_call():
    client = FakeClient(tool_call_response())
    model = CodexChatModel(model="gpt-5.5", client=client).bind_tools([sample_tool])

    result = model.invoke([HumanMessage(content="调用工具")])

    assert result.content == ""
    assert result.tool_calls[0]["name"] == "sample_tool"
    assert result.tool_calls[0]["args"] == {"value": 3}
    assert result.tool_calls[0]["id"] == "call_1"


def test_codex_chat_model_keeps_plain_response_without_tools():
    client = FakeClient(message_response("普通回答"))

    result = CodexChatModel(model="gpt-5.5", client=client).invoke("直接回答")

    assert result.content == "普通回答"
    assert "tools" not in client.responses.payloads[0]


def test_codex_chat_model_replays_tool_results_as_function_call_output():
    client = FakeClient(message_response("工具结果已使用"))
    model = CodexChatModel(model="gpt-5.5", client=client).bind_tools([sample_tool])

    result = model.invoke(
        [
            HumanMessage(content="调用工具"),
            AIMessage(
                content="",
                tool_calls=[{"name": "sample_tool", "args": {"value": 3}, "id": "call_1"}],
            ),
            ToolMessage(content="3", name="sample_tool", tool_call_id="call_1"),
        ]
    )

    assert result.content == "工具结果已使用"
    assert {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "3",
    } in client.responses.payloads[0]["input"]


def test_codex_chat_model_stream_returns_deltas():
    client = FakeClient(message_response("Hello"))
    chunks = list(CodexChatModel(model="gpt-5.5", client=client).stream("流式回答"))

    visible_chunks = [chunk.content for chunk in chunks if chunk.content]
    assert visible_chunks == ["Hel", "lo"]
    assert "".join(visible_chunks) == "Hello"


def test_codex_chat_model_stream_buffers_final_tool_call_chunk():
    client = FakeClient(empty_response())
    client.responses.stream_events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="sample_tool",
                arguments='{"value":7}',
                status="completed",
            ),
        )
    ]
    model = CodexChatModel(model="gpt-5.5", client=client).bind_tools([sample_tool])

    chunks = list(model.stream("调用工具"))

    tool_chunks = [chunk for chunk in chunks if chunk.tool_call_chunks]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_call_chunks[0]["name"] == "sample_tool"
    assert tool_chunks[0].tool_call_chunks[0]["args"] == '{"value":7}'


def test_codex_chat_model_passes_model_and_thinking_to_responses_payload():
    client = FakeClient(message_response("配置已传递"))

    CodexChatModel(model="test-codex-model", thinking="high", client=client).invoke("检查配置")

    payload = client.responses.payloads[0]
    assert payload["model"] == "test-codex-model"
    assert payload["reasoning"]["effort"] == "high"
    assert payload["reasoning"]["summary"] == "auto"


def test_codex_chat_model_omits_thinking_when_none():
    client = FakeClient(message_response("配置已传递"))

    CodexChatModel(thinking="none", client=client).invoke("检查配置")

    assert "reasoning" not in client.responses.payloads[0]


def test_codex_chat_model_reads_codex_auth_store(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        """
        {
          "tokens": {
            "access_token": "access-token",
            "account_id": "account-1",
            "base_url": "https://example.test/codex"
          }
        }
        """,
        encoding="utf-8",
    )

    credentials = runtime_codex_credentials(auth_path=auth_path)

    assert credentials.access_token == "access-token"
    assert credentials.account_id == "account-1"
    assert credentials.base_url == "https://example.test/codex"


def test_codex_chat_model_direct_answer_through_rosa_runtime():
    client = FakeClient(message_response("ROSA 运行时回答"))
    agent = ROSA(ros_version=2, llm=CodexChatModel(client=client))

    assert agent.invoke("直接回答") == "ROSA 运行时回答"


def test_codex_chat_model_tool_call_through_rosa_runtime():
    client = FakeClient(
        [
            tool_call_response(name="sample_tool", arguments='{"value":7}'),
            message_response("工具调用完成"),
        ]
    )
    agent = ROSA(ros_version=2, llm=CodexChatModel(client=client), tools=[sample_tool])

    assert agent.invoke("调用 sample_tool") == "工具调用完成"


def test_codex_chat_model_streamed_answer_through_rosa_runtime():
    client = FakeClient(message_response("Hello"))
    client.responses.stream_events = [
        SimpleNamespace(type="response.output_text.delta", delta="流"),
        SimpleNamespace(type="response.output_text.delta", delta="式"),
    ]
    agent = ROSA(ros_version=2, llm=CodexChatModel(client=client), streaming=True)

    async def collect_events():
        return [event async for event in agent.astream("流式回答")]

    events = asyncio.run(collect_events())

    assert events == [
        {"type": "token", "content": "流"},
        {"type": "token", "content": "式"},
        {"type": "final", "content": "流式"},
    ]
