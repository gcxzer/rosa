"""ROSA NavAgent for MuJoCo + Nav2."""

from __future__ import annotations

from typing import Optional

from codex import CodexChatModel
from prompts import RobotSystemPrompts
from rosa import ROSA

from . import tools as nav_tools
from .prompts import NAV_SYSTEM_PROMPTS


class NavAgent(ROSA):
    examples = [
        "检查 MuJoCo、差速控制器、LiDAR、TF、AMCL 和 Nav2 是否就绪。",
        "列出可以导航的办公室地点，并解析“充电站”。",
        "告诉我 Stretch 当前在 map 坐标系中的位姿。",
        "导航到接待区，然后查询这个任务的状态。",
        "依次前往 reception、inspection 和 charging。",
        "取消当前导航任务，并报告最终状态。",
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
    ) -> None:
        if llm is None:
            llm = CodexChatModel(model=model, thinking=thinking)
        nav_blacklist = ["master", "docker", "gazebo"]
        if blacklist:
            nav_blacklist.extend(blacklist)
        super().__init__(
            ros_version=2,
            llm=llm,
            tool_packages=[nav_tools],
            prompts=prompts or NAV_SYSTEM_PROMPTS,
            verbose=verbose,
            blacklist=nav_blacklist,
            streaming=streaming,
            max_iterations=max_iterations,
        )
