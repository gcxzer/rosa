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

import time

from langchain_core.tools import tool


VERBOSE = False
DEBUG = False


@tool
def set_verbosity(enable_verbose_messages: bool) -> str:
    """设置 agent 是否输出 verbose 详细信息。
    将该值设为 true 时，会向用户提供更详细的输出。

    :arg enable_verbose_messages: 用于启用或禁用 verbose 详细信息的布尔值。
    """
    global VERBOSE
    # 这个工具现在维护 ROSA 自己的偏好状态，供外层调试入口或集成 UI 读取。
    VERBOSE = enable_verbose_messages
    return f"verbose 详细信息现在已{'启用' if VERBOSE else '禁用'}。"


@tool
def set_debugging(enable_debug_messages: bool) -> str:
    """设置 agent 是否启用 debug 调试信息。
    将该值设为 true 时，会向用户提供 debug 输出。debug 输出包含 API 调用、
    工具执行以及其他内部执行信息。

    :arg enable_debug_messages: 用于启用或禁用 debug 调试信息的布尔值。
    """
    global DEBUG
    # `create_agent(debug=...)` 是构造期配置，运行中的工具无法安全地重编译 agent。
    # 因此这里记录 ROSA 侧 debug 偏好。
    DEBUG = enable_debug_messages
    return f"debug 调试信息现在已{'启用' if DEBUG else '禁用'}。"


@tool
def wait(seconds: int) -> str:
    """等待指定秒数后再继续执行。

    :arg seconds: 需要等待的秒数。
    """
    start = time.time()
    time.sleep(seconds)
    end = time.time()
    return f"已准确等待 {end - start} 秒。"
