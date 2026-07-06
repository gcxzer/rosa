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

import inspect
from functools import wraps
from typing import Literal, List, Optional

from langchain_core.tools import BaseTool


def inject_blacklist(default_blacklist: List[str]):
    """
    向需要 blacklist 参数的 @tool 函数自动注入黑名单。这样做是必要的，因为我们不能
    依赖 LLM 每次都主动使用 blacklist；它可能会“忘记”传入该参数。

    该装饰器会用一个新函数包裹 @tool 函数；如果调用时没有传入 blacklist，就自动注入默认黑名单。
    同时它会保留函数签名和参数列表，确保 LangChain 能够正确执行工具函数。如果不这样处理，
    LangChain 会抛出错误。这里曾尝试使用 partial 函数，但无法满足 LangChain 的签名要求。
    """

    def decorator(func):
        # LangChain 的 @tool 会把普通函数包装成 BaseTool。运行时注入工具参数时，
        # 我们优先操作底层函数签名；真正调用 BaseTool 时则使用 invoke()，避免触发
        # LangChain 已弃用的旧式工具调用路径。
        tool_func = func.func if isinstance(func, BaseTool) else func

        @wraps(tool_func)
        def wrapper(*args, **kwargs):
            if args and isinstance(args[0], dict):
                if "blacklist" in args[0]:
                    args[0]["blacklist"] = default_blacklist + args[0]["blacklist"]
                else:
                    args[0]["blacklist"] = default_blacklist
            else:
                if "blacklist" in kwargs:
                    kwargs["blacklist"] = default_blacklist + kwargs["blacklist"]
                else:
                    params = inspect.signature(tool_func).parameters
                    if "blacklist" in params:
                        kwargs["blacklist"] = default_blacklist

            if isinstance(func, BaseTool):
                if args and isinstance(args[0], dict) and len(args) == 1 and not kwargs:
                    return func.invoke(args[0])
                if args:
                    return tool_func(*args, **kwargs)
                return func.invoke(kwargs)

            if args and isinstance(args[0], dict) and len(args) == 1 and not kwargs:
                return tool_func(**args[0])
            return tool_func(*args, **kwargs)

        # 重建函数签名，确保其中包含 blacklist。
        sig = inspect.signature(tool_func)
        new_params = [
            (
                param.replace(default=default_blacklist)
                if param.name == "blacklist"
                else param
            )
            for param in sig.parameters.values()
        ]
        wrapper.__signature__ = sig.replace(parameters=new_params)
        return wrapper

    return decorator


class ROSATools:
    def __init__(self, ros_version: Literal[2] = 2, blacklist: Optional[List[str]] = None):
        self.__tools: list = []
        self.__ros_version = ros_version
        self.__blacklist = blacklist

        # 添加默认工具。
        from . import calculation, log, system

        self.__iterative_add(calculation)
        self.__iterative_add(log)
        self.__iterative_add(system)

        if self.__ros_version != 2:
            raise ValueError("ROSA 已改为仅支持 ROS2，请使用 ros_version=2。")

        # 当前分支只保留 ROS2 工具，因此这里始终加载 ROS2 工具包。
        from . import ros2

        self.__iterative_add(ros2, blacklist=blacklist)

    def get_tools(self) -> List[BaseTool]:
        return self.__tools

    def __add_tool(self, tool):
        if hasattr(tool, "name") and hasattr(tool, "func"):
            if self.__blacklist and "blacklist" in tool.func.__code__.co_varnames:
                # 将黑名单注入工具函数，避免依赖 LLM 自己记得传入该参数。
                tool.func = inject_blacklist(self.__blacklist)(tool.func)
            self.__tools.append(tool)

    def __iterative_add(self, package, blacklist: Optional[List[str]] = None):
        """
        遍历一个 Python 包，并把其中的每个 @tool 添加到工具列表。

        :param package: 需要遍历的 Python 包。
        :param blacklist: 某些工具用于过滤结果的黑名单参数。
        """
        for tool_name in dir(package):
            if not tool_name.startswith("_"):
                t = getattr(package, tool_name)
                self.__add_tool(t)

    def add_packages(self, tool_packages: List, blacklist: Optional[List[str]] = None):
        """
        遍历每个工具包，并把其中的工具加入当前 Tools 对象。

        :param tool_packages: 需要添加到当前 Tools 对象中的工具包列表。
        """
        for pkg in tool_packages:
            self.__iterative_add(pkg, blacklist=blacklist)

    def add_tools(self, tools: list):
        """
        将一组单独的工具添加到当前 Tools 对象中。

        :param tools: 需要添加的工具列表。
        """
        for tool in tools:
            self.__add_tool(tool)
