from typing import Any, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from turtle_agent import TurtleAgent


class RecordingChatModel(BaseChatModel):
    """测试用 ChatModel：记录 LangChain 传入的 messages 和绑定的工具名。"""

    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    @property
    def bound_tool_names(self) -> list[str]:
        return self._bound_tool_names

    @property
    def _llm_type(self) -> str:
        return "recording-chat-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "RecordingChatModel":
        del tool_choice, kwargs
        # create_agent 会在调用模型前绑定工具；这里记录名字即可，不执行任何工具。
        self._bound_tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self._calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])


def test_turtle_agent_binds_turtle_tools_and_prompt():
    model = RecordingChatModel()
    agent = TurtleAgent(llm=model, streaming=False)

    assert agent.invoke("检查 turtlesim") == "完成"
    assert "draw_rectangle" in model.bound_tool_names
    assert "turtle_get_pose" in model.bound_tool_names

    system_text = "\n".join(
        str(message.content) for message in model.calls[0] if message.type == "system"
    )
    assert "turtlesim" in system_text
    assert "TurtleAgent" in system_text
