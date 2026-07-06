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

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, AsyncIterable, Dict, Literal, Optional, Union

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_community.callbacks import get_openai_callback
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import AzureChatOpenAI, ChatOpenAI

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama

from .prompts import RobotSystemPrompts, system_prompts
from .tools import ROSATools

logger = logging.getLogger(__name__)

# 这些模型提供商用于静态分析；运行时仍接受 BaseChatModel。
if TYPE_CHECKING:
    ChatModel = Union[ChatOpenAI, AzureChatOpenAI, ChatAnthropic, ChatOllama]
else:
    ChatModel = BaseChatModel


class ROSA:
    """ROSA（Robot Operating System Agent）封装了通过自然语言与 ROS 系统交互的核心逻辑。

    Args:
        ros_version (Literal[2]): agent 将要交互的 ROS 版本。当前分支仅支持 ROS2。
        llm (ChatModel): 用于生成响应的语言模型。已经测试过的模型提供商包括：
            ChatOpenAI、AzureChatOpenAI、ChatAnthropic 和 ChatOllama。其他支持工具调用的
            BaseChatModel 子类在运行时可能可用，但并未被官方测试覆盖。
            注意：token 用量统计目前只支持 ChatOpenAI 和 AzureChatOpenAI。
        tools (Optional[list]): 额外提供给 agent 使用的 LangChain 工具函数列表。
        tool_packages (Optional[list]): 包含 LangChain 工具函数的 Python 包列表。
        prompts (Optional[RobotSystemPrompts]): 提供给 agent 的自定义 prompt。
        verbose (bool): 是否打印详细输出。默认值为 False。
        blacklist (Optional[list]): 需要从 agent 可见工具结果中排除的 ROS 名称列表。
        accumulate_chat_history (bool): 是否累积聊天历史。默认值为 True。
        show_token_usage (bool): 是否显示 token 用量。启用 streaming 时不可用。默认值为 False。
        streaming (bool): 是否流式输出 agent 的结果。默认值为 True。
        max_iterations (int): agent executor 的最大迭代次数。默认值为 100。
        return_intermediate_steps (bool): 是否返回 agent 执行过程中的中间步骤。
            设为 True 会增加内存使用，但可以提供更详细的执行轨迹。默认值为 False。

    Attributes:
        chat_history (list): 表示聊天历史的消息列表。

    Methods:
        clear_chat(): 清空聊天历史。
        invoke(query: str) -> str: 处理用户查询并返回 agent 响应。
        astream(query: str) -> AsyncIterable[Dict[str, Any]]: 以异步流方式返回 agent 响应。

    Note:
        - `tools` 和 `tool_packages` 参数可用于扩展 agent 能力。
        - 可以传入自定义 `prompts`，让 agent 行为适配特定机器人或特定使用场景。
        - 启用 streaming 时会自动关闭 token 用量显示。
        - 非流式响应使用 `invoke()`；流式响应使用 `astream()`。
    """

    def __init__(
        self,
        ros_version: Literal[2],
        llm: ChatModel,
        tools: Optional[list] = None,
        tool_packages: Optional[list] = None,
        prompts: Optional[RobotSystemPrompts] = None,
        verbose: bool = False,
        blacklist: Optional[list] = None,
        accumulate_chat_history: bool = True,
        show_token_usage: bool = False,
        streaming: bool = True,
        max_iterations: int = 100,
        return_intermediate_steps: bool = False,
    ):
        self.__chat_history = []
        self.__ros_version = ros_version
        self.__llm = llm.with_config({"streaming": streaming})
        self.__memory_key = "chat_history"
        self.__scratchpad = "agent_scratchpad"
        self.__blacklist = blacklist if blacklist else []
        self.__accumulate_chat_history = accumulate_chat_history
        self.__streaming = streaming
        self.__max_iterations = max_iterations
        self.__return_intermediate_steps = return_intermediate_steps
        self.__tools = self._get_tools(
            ros_version, packages=tool_packages, tools=tools, blacklist=self.__blacklist
        )
        self.__prompts = self._get_prompts(prompts)
        self.__agent = self._get_agent()
        self.__executor = self._get_executor(verbose=verbose)
        # 缓存模型类型检查结果，避免每次 invoke 都重复执行 isinstance。
        self.__supports_token_tracking = isinstance(llm, (ChatOpenAI, AzureChatOpenAI))
        self.__show_token_usage = show_token_usage if not streaming else False

        if self.__show_token_usage and not self.__supports_token_tracking:
            logger.warning(
                "token 用量统计只支持 OpenAI/Azure 模型，不支持 %s。已自动禁用。",
                type(llm).__name__,
            )
            self.__show_token_usage = False

    @property
    def chat_history(self):
        """获取聊天历史。"""
        return self.__chat_history

    def clear_chat(self):
        """清空聊天历史。"""
        self.__chat_history = []

    def invoke(self, query: str) -> str:
        """
        使用用户查询调用 agent，并返回最终响应。

        该方法会把用户查询交给 agent 处理，按需统计 token 用量，并更新聊天历史。

        Args:
            query (str): 需要 agent 处理的用户输入查询。

        返回：
            str: agent 对查询的响应；如果发生错误，则返回错误说明。

        Raises:
            调用过程中除 KeyboardInterrupt 外的异常都会被捕获，并以错误说明的形式返回。

        Note:
            - 如果启用了 token 用量统计，本方法会使用 OpenAI callback。
            - 成功执行后，查询和响应会被写入聊天历史。
            - 如果设置了 show_token_usage，会打印 token 用量。
        """
        try:
            with self._token_callback() as cb:
                result = self.__executor.invoke(
                    {"input": query, "chat_history": self.__chat_history}
                )
                self._print_usage(cb)
        except KeyboardInterrupt:
            # 重新抛出 KeyboardInterrupt，让上层调用者可以正确处理中断。
            raise
        except Exception as e:
            return f"发生错误：{str(e)}"

        self._record_chat_history(query, result["output"])
        return result["output"]

    async def astream(self, query: str) -> AsyncIterable[Dict[str, Any]]:
        """
        以异步流的形式返回 agent 对用户查询的响应。

        该方法会处理用户查询，并在事件发生时逐步产出事件，包括 token 生成、
        工具调用和最终输出。它用于启用 streaming 的场景。

        Args:
            query (str): 用户输入查询。

        返回：
            AsyncIterable[Dict[str, Any]]: 异步字典迭代器，每个字典都包含事件信息。
            每个事件都有 `type` 字段，并根据事件类型包含额外字段：
            - 'token': 通过 'content' 产出生成中的 token。
            - 'tool_start': 表示工具开始执行，包含 'name' 和 'input'。
            - 'tool_end': 表示工具执行结束，包含 'name' 和 'output'。
            - 'final': 通过 'content' 给出 agent 最终输出。
            - 'error': 表示发生错误，'content' 中包含错误说明。

        Raises:
            ValueError: 当前 ROSA 实例未启用 streaming 时抛出。
            Exception: streaming 过程中发生错误时抛出。

        Note:
            成功执行时，本方法会用最终输出更新聊天历史。
        """
        if not self.__streaming:
            raise ValueError(
                "当前未启用 streaming。请改用 'invoke' 方法，或在初始化 ROSA 时设置 streaming=True。"
            )

        try:
            final_output = ""
            # 从 agent 响应中逐个读取 streaming 事件。
            async for event in self.__executor.astream_events(
                input={"input": query, "chat_history": self.__chat_history},
                config={"run_name": "Agent"},
                version="v2",
            ):
                # 提取事件类型。
                kind = event["event"]

                # 处理聊天模型的 token 流事件。
                if kind == "on_chat_model_stream":
                    # 从事件中提取文本内容，并将其产出给调用者。
                    content = event["data"]["chunk"].content
                    if content:
                        final_output += f" {content}"
                        yield {"type": "token", "content": content}

                # 处理工具开始执行事件。
                elif kind == "on_tool_start":
                    yield {
                        "type": "tool_start",
                        "name": event["name"],
                        "input": event["data"].get("input"),
                    }

                # 处理工具执行结束事件。
                elif kind == "on_tool_end":
                    yield {
                        "type": "tool_end",
                        "name": event["name"],
                        "output": event["data"].get("output"),
                    }

                # 处理 agent 链路结束事件。
                elif kind == "on_chain_end":
                    if event["name"] == "Agent":
                        chain_output = event["data"].get("output", {}).get("output")
                        if chain_output:
                            final_output = (
                                chain_output  # 如果存在最终输出，用它覆盖 token 拼接结果。
                            )
                            yield {"type": "final", "content": chain_output}

            if final_output:
                self._record_chat_history(query, final_output)
        except KeyboardInterrupt:
            # 将用户中断转换成 streaming error 事件，方便上层 UI 统一展示。
            yield {"type": "error", "content": "操作已被用户中断"}
        except Exception as e:
            yield {"type": "error", "content": f"发生错误：{e}"}

    def _get_executor(self, verbose: bool) -> AgentExecutor:
        """创建并返回用于处理用户输入、生成响应的 executor。"""
        executor = AgentExecutor(
            agent=self.__agent,
            tools=self.__tools.get_tools(),
            stream_runnable=self.__streaming,
            verbose=verbose,
            max_iterations=self.__max_iterations,
            handle_parsing_errors=True,
            return_intermediate_steps=self.__return_intermediate_steps,
        )
        return executor

    def _get_agent(self):
        """创建并返回用于处理用户输入、生成响应的 agent。"""
        agent = create_tool_calling_agent(
            llm=self.__llm,
            tools=self.__tools.get_tools(),
            prompt=self.__prompts,
        )
        return agent

    def _get_tools(
        self,
        ros_version: Literal[2],
        packages: Optional[list],
        tools: Optional[list],
        blacklist: Optional[list],
    ) -> ROSATools:
        """根据 ROS2 工具、额外工具包和黑名单创建 ROSA 工具集合。"""
        rosa_tools = ROSATools(ros_version, blacklist=blacklist)
        if tools:
            rosa_tools.add_tools(tools)
        if packages:
            rosa_tools.add_packages(packages, blacklist=blacklist)
        return rosa_tools

    def _get_prompts(
        self, robot_prompts: Optional[RobotSystemPrompts] = None
    ) -> ChatPromptTemplate:
        """用默认系统 prompt 和机器人专属 prompt 创建聊天 prompt 模板。"""
        # 从默认系统 prompt 开始。
        prompts = system_prompts

        # 如果调用者提供了机器人专属 prompt，就把它追加到默认 prompt 后面。
        if robot_prompts:
            prompts.append(robot_prompts.as_message())

        template = ChatPromptTemplate.from_messages(
            prompts
            + [
                MessagesPlaceholder(variable_name=self.__memory_key),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name=self.__scratchpad),
            ]
        )
        return template

    @contextmanager
    def _token_callback(self):
        """用于 token 用量统计的上下文管理器。

        当 LLM 是 OpenAI 系模型时使用 OpenAI callback；否则产出 None，
        确保其余执行流程不受影响。
        """
        if self.__supports_token_tracking:
            with get_openai_callback() as cb:
                yield cb
        else:
            yield None

    def _print_usage(self, cb):
        """在启用 show_token_usage 时打印 token 用量。"""
        if cb is None or not self.__show_token_usage:
            return
        print(f"[bold]Prompt Tokens:[/bold] {cb.prompt_tokens}")
        print(f"[bold]Completion Tokens:[/bold] {cb.completion_tokens}")
        print(f"[bold]Total Cost (USD):[/bold] ${cb.total_cost}")

    def _record_chat_history(self, query: str, response: str):
        """如果启用了聊天历史累积，则记录本轮查询和响应。"""
        if self.__accumulate_chat_history:
            self.__chat_history.extend(
                [HumanMessage(content=query), AIMessage(content=response)]
            )
