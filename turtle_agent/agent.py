"""基于当前 ROSA runtime 的 TurtleAgent。

当前 fork 已经改成 ROS2-only，因此这里把 TurtleAgent 重写成一个
普通 Python 类：它只负责准备 turtlesim prompt、turtle 专用工具和默认 Codex ChatModel，
真正的 agent 循环仍然交给 `ROSA`。
"""

from __future__ import annotations

from typing import Optional

from codex import CodexChatModel
from prompts import RobotSystemPrompts
from rosa import ROSA

from . import tools as turtle_tools
from .prompts import TURTLE_SYSTEM_PROMPTS


class TurtleAgent(ROSA):
    """面向 ROS2 turtlesim 的 ROSA agent。

    Args:
        llm: 可选的 LangChain `BaseChatModel`。测试或自定义模型时直接传入；不传时使用
            `CodexChatModel`。
        model: 创建默认 `CodexChatModel` 时使用的模型名。
        thinking: 创建默认 `CodexChatModel` 时使用的 reasoning effort；`None` 或 `"none"`
            表示不显式传 reasoning 配置。
        streaming: 是否允许通过 `astream()` 流式输出。
        verbose: 是否开启 LangGraph debug 输出。
        blacklist: 追加给 ROSA 默认 ROS2 工具的黑名单。
        prompts: 可选的自定义 TurtleAgent prompt；不传时使用内置 turtlesim prompt。
        max_iterations: 映射到 LangGraph `recursion_limit`，限制一次任务最多执行多少步。
    """

    examples = [
        "列出 turtlesim 当前的 ROS2 node、topic 和 service。",
        "把 turtle1 传送到 (3, 3)，然后画一个边长为 2 的正方形。",
        "清空画布，把背景改成浅蓝色，然后用红色画一个圆。",
        "画一座简单的小房子：矩形墙、三角形屋顶、门和两个窗户。",
        "读取 turtle1 当前 pose，并解释 x、y、theta 分别是什么意思。",
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
        # 如果调用者没有传入模型，就使用当前 fork 的默认 Codex LangChain 适配器。
        # 测试时可以传 fake model，实际使用时则复用本机 Codex OAuth 登录态。
        if llm is None:
            llm = CodexChatModel(model=model, thinking=thinking)

        # turtlesim 场景下这些名字通常不是用户真正关心的机器人资源；放进 blacklist 后，
        # 默认 ROS2 列表工具会过滤掉它们，减少 agent 看到的噪声。
        turtle_blacklist = ["master", "docker"]
        if blacklist:
            turtle_blacklist.extend(blacklist)

        super().__init__(
            ros_version=2,
            llm=llm,
            tool_packages=[turtle_tools],
            prompts=prompts or TURTLE_SYSTEM_PROMPTS,
            verbose=verbose,
            blacklist=turtle_blacklist,
            streaming=streaming,
            max_iterations=max_iterations,
        )
