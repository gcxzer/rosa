from __future__ import annotations

from typing import Any, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from nav_agent import NavAgent


class RecordingChatModel(BaseChatModel):
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)

    @property
    def calls(self):
        return self._calls

    @property
    def bound_tool_names(self):
        return self._bound_tool_names

    @property
    def _llm_type(self) -> str:
        return "nav-recording-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        **kwargs: Any,
    ):
        del tool_choice, kwargs
        self._bound_tool_names = [getattr(item, "name", str(item)) for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        del stop, run_manager, kwargs
        self._calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])


def test_nav_agent_constructs_without_ros_and_binds_navigation_tools() -> None:
    model = RecordingChatModel()
    agent = NavAgent(llm=model, streaming=False)

    assert agent.invoke("去充电站") == "完成"
    assert "nav_check_readiness" in model.bound_tool_names
    assert "nav_start_named_goal" in model.bound_tool_names
    assert "nav_start_semantic_mission" in model.bound_tool_names
    assert "nav_get_goal_status" in model.bound_tool_names
    assert "nav_cancel_goal" in model.bound_tool_names
    assert "ros2_node_list" in model.bound_tool_names

    system_text = "\n".join(str(message.content) for message in model.calls[0] if message.type == "system")
    assert "NavAgent" in system_text
    assert "nav_check_readiness" in system_text
    assert "一次只能有一个活动任务" in system_text
    assert "绝不猜测" in system_text
    assert "Gazebo" in system_text
    assert "rejected、failed、cancelled、timeout" in system_text
