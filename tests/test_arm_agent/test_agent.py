from typing import Any, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from arm_agent import ArmAgent


class RecordingChatModel(BaseChatModel):
    """测试用 ChatModel：只记录输入消息和工具名，不访问真实 Codex 或 ROS2。"""

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
        # create_agent 会把默认 ROS2 工具和 arm_agent 工具一起传进来；这里只记录名字。
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


def test_arm_agent_imports_and_binds_arm_tools_without_ros_runtime():
    model = RecordingChatModel()
    agent = ArmAgent(llm=model, streaming=False)

    assert agent.invoke("检查机械臂") == "完成"
    assert "arm_check_readiness" in model.bound_tool_names
    assert "arm_get_end_effector_link" in model.bound_tool_names
    assert "arm_move_to_pose_goal" in model.bound_tool_names
    assert "arm_move_to_named_target" in model.bound_tool_names
    assert "arm_open_gripper" in model.bound_tool_names
    assert "arm_close_gripper" in model.bound_tool_names
    assert "arm_set_gripper_width" in model.bound_tool_names
    assert "arm_execute_plan" not in model.bound_tool_names
    assert "ros2_node_list" in model.bound_tool_names

    system_text = "\n".join(
        str(message.content) for message in model.calls[0] if message.type == "system"
    )
    assert "ArmAgent" in system_text
    assert "MoveIt2" in system_text
    assert "MuJoCo" in system_text
    assert "夹爪" in system_text
    assert "Gazebo" in system_text
