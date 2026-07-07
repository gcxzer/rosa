"""基于当前 ROSA runtime 的 ArmAgent。
"""

from __future__ import annotations

from typing import Optional

from codex import CodexChatModel
from prompts import RobotSystemPrompts
from rosa import ROSA

from . import tools as arm_tools
from .prompts import ARM_SYSTEM_PROMPTS


class ArmAgent(ROSA):
    """面向 ROS2 MoveIt2 + MuJoCo 机械臂栈的 ROSA agent。"""

    examples = [
        "检查当前 ROS2 graph，确认 MoveIt2、controller manager、joint states 和 MuJoCo 控制链路是否在线。",
        "读取当前 joint states 和末端位姿。",
        "移动到 named target home，并返回最终 joint state。",
        "把末端移动到 panda_link0 坐标系下 x=0.4, y=0.0, z=0.4，姿态保持单位四元数。",
        "立刻停止机械臂运动，并报告控制器状态。",
    ]

    def __init__(
        self,
        llm=None,
        model: str = "gpt-5.5",
        thinking: Optional[str] = None,
        streaming: bool = True,
        verbose: bool = False,
        blacklist: Optional[list[str]] = None,
        prompts: Optional[RobotSystemPrompts] = None,
        max_iterations: int = 100,
    ):
        # 默认复用 Codex LangChain 适配器；单元测试可以传入 fake model。
        if llm is None:
            llm = CodexChatModel(model=model, thinking=thinking)

        # 机械臂场景下这些名字通常是平台噪声或旧系统残留；默认过滤掉，调用者仍可追加。
        arm_blacklist = ["master", "docker", "gazebo"]
        if blacklist:
            arm_blacklist.extend(blacklist)

        super().__init__(
            ros_version=2,
            llm=llm,
            tool_packages=[arm_tools],
            prompts=prompts or ARM_SYSTEM_PROMPTS,
            verbose=verbose,
            blacklist=arm_blacklist,
            streaming=streaming,
            max_iterations=max_iterations,
        )
