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

import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

from langchain_core.tools import tool
try:
    from rclpy.logging import get_logging_directory
except ModuleNotFoundError:
    # 本项目的 ROS2 工具主要通过 `ros2` CLI 工作；本地单元测试会 mock CLI 调用。
    # 如果当前机器没有安装 rclpy，仍允许模块被导入，只在日志目录查询时使用通用默认路径。
    get_logging_directory = None


def execute_ros_command(command: str) -> Tuple[bool, str]:
    """
    执行 ROS2 命令。

    :param command: 需要执行的 ROS2 命令。
    :return: 二元组，第一个值表示是否成功，第二个值是命令输出。
    """

    # 校验传入命令是否是允许执行的 ROS2 命令。
    cmd = command.split(" ")
    valid_ros2_commands = ["node", "topic", "service", "param", "doctor"]

    if len(cmd) < 2:
        raise ValueError(f"'{command}' 不是有效的 ROS2 命令。")
    if cmd[0] != "ros2":
        raise ValueError(f"'{command}' 不是有效的 ROS2 命令。")
    if cmd[1] not in valid_ros2_commands:
        raise ValueError(f"'ros2 {cmd[1]}' 不是有效的 ros2 子命令。")

    try:
        output = subprocess.check_output(command, shell=True).decode()
        return True, output
    except Exception as e:
        return False, str(e)


def get_entities(
    cmd: str,
    delimiter: str = "\n",
    pattern: str = None,
    blacklist: Optional[List[str]] = None,
) -> List[str]:
    """
    获取 ROS2 实体列表，例如 node、topic、service 等。

    :param cmd: 需要执行的 ROS2 命令。
    :param delimiter: 用于切分命令输出的分隔符。
    :param pattern: 用于过滤实体列表的正则表达式。
    :return:
    """
    success, output = execute_ros_command(cmd)

    if not success:
        return [output]

    entities = output.split(delimiter)

    # 过滤命中黑名单的实体。
    if blacklist:
        entities = list(
            filter(
                lambda x: not any(
                    re.match(f".*{pattern}.*", x) for pattern in blacklist
                ),
                entities,
            )
        )

    if pattern:
        entities = list(filter(lambda x: re.match(f".*{pattern}.*", x), entities))

    entities = [e for e in entities if e.strip() != ""]

    return entities


@tool
def ros2_node_list(pattern: Optional[str] = None, blacklist: Optional[List[str]] = None) -> dict:
    """
    获取系统中正在运行的 ROS2 node 列表。

    :param pattern: 用于过滤 node 列表的正则表达式。
    """
    cmd = "ros2 node list"
    nodes = get_entities(cmd, pattern=pattern, blacklist=blacklist)
    return {"nodes": nodes}


@tool
def ros2_topic_list(pattern: Optional[str] = None, blacklist: Optional[List[str]] = None) -> dict:
    """
    获取 ROS2 topic 列表。

    :param pattern: 用于过滤 topic 列表的正则表达式。
    """
    cmd = "ros2 topic list"
    topics = get_entities(cmd, pattern=pattern, blacklist=blacklist)
    return {"topics": topics}


@tool
def ros2_topic_echo(
    topic: str,
    count: int = 1,
    return_echoes: bool = False,
    delay: float = 1.0,
    timeout: float = 1.0,
) -> dict:
    """
    echo 指定 ROS2 topic 的内容。

    :param topic: 需要 echo 的 ROS topic 名称。
    :param count: 需要 echo 的消息数量。有效范围是 1-10。
    :param return_echoes: 如果为 True，则在响应中以列表形式返回消息。
    :param delay: 每条消息之间的等待时间，单位为秒。
    :param timeout: 等待消息的最长时间，超时后停止等待，单位为秒。

    :note: 如果消息数量很大，不要将 return_echoes 设置为 True。
           这会导致响应过大，并可能使工具执行失败。
    """
    cmd = f"ros2 topic echo {topic} --once --spin-time {timeout}"

    if count < 1 or count > 10:
        return {"error": "count 必须在 1 到 10 之间。"}

    echoes = []
    for i in range(count):
        success, output = execute_ros_command(cmd)

        if not success:
            return {"error": output}

        print(output)
        if return_echoes:
            echoes.append(output)

        time.sleep(delay)

    if return_echoes:
        return {"echoes": echoes}

    return {"success": True}


@tool
def ros2_service_list(
    pattern: Optional[str] = None, blacklist: Optional[List[str]] = None
) -> dict:
    """
    获取 ROS2 service 列表。

    :param pattern: 用于过滤 service 列表的正则表达式。
    """
    cmd = "ros2 service list"
    services = get_entities(cmd, pattern=pattern, blacklist=blacklist)
    return {"services": services}


@tool
def ros2_node_info(nodes: List[str]) -> dict:
    """
    获取 ROS2 node 的信息。

    :param node_name: ROS2 node 名称。
    """
    data = {}

    for node_name in nodes:

        cmd = f"ros2 node info {node_name}"
        success, output = execute_ros_command(cmd)
        if not success:
            data[node_name] = dict(error=output)
            continue
        data[node_name] = output

    return data


@tool
def ros2_topic_info(topics: List[str]) -> dict:
    """
    获取 ROS2 topic 的信息。

    :param topic_name: ROS2 topic 名称。
    """
    data = {}

    for topic in topics:
        cmd = f"ros2 topic info {topic} --verbose"
        success, output = execute_ros_command(cmd)
        if not success:
            topic_info = dict(error=output)
        else:
            topic_info = output

        data[topic] = topic_info

    return data


@tool
def ros2_param_list(
    node_name: Optional[str] = None,
    pattern: str = None,
    blacklist: Optional[List[str]] = None,
) -> dict:
    """
    获取 ROS2 node 的 parameter 列表。

    :param node_name: 可选的 ROS2 node 名称；如果提供，则只获取该 node 的 parameter。未提供时列出所有 parameter。
    :param pattern: 用于过滤 parameter 列表的正则表达式。
    """
    if node_name:
        cmd = f"ros2 param list {node_name}"
        success, output = execute_ros_command(cmd)
        if not success:
            return {"error": output}

        params = [o for o in output.split("\n") if o]
        if pattern:
            params = [p for p in params if re.match(f".*{pattern}.*", p)]
        if blacklist:
            params = [
                p for p in params if not any(re.match(f".*{b}.*", p) for b in blacklist)
            ]
        return {node_name: params}
    else:
        cmd = f"ros2 param list"
        success, output = execute_ros_command(cmd)

        if not success:
            return {"error": output}

        # 获取所有 node 的 parameter 列表时，需要手动解析命令输出。
        # node 名称以 '/' 开头，其下方的 parameter 行会带缩进。
        lines = output.split("\n")
        data = {}
        current_node = None
        for line in lines:
            if line.startswith("/"):
                current_node = line
                data[current_node] = []
            elif line.strip() != "":
                data[current_node].append(line.strip())

        if pattern:
            data = {k: v for k, v in data.items() if re.match(f".*{pattern}.*", k)}
        if blacklist:
            data = {
                k: v
                for k, v in data.items()
                if not any(re.match(f".*{b}.*", k) for b in blacklist)
            }
        return data


@tool
def ros2_param_get(node_name: str, param_name: str) -> dict:
    """
    获取 ROS2 node 上某个 parameter 的值。

    :param node_name: ROS2 node 名称。
    :param param_name: parameter 名称。
    """
    cmd = f"ros2 param get {node_name} {param_name}"
    success, output = execute_ros_command(cmd)

    if not success:
        return {"error": output}

    return {param_name: output}


@tool
def ros2_param_set(node_name: str, param_name: str, param_value: str) -> dict:
    """
    设置 ROS2 node 上某个 parameter 的值。

    :param node_name: ROS2 node 名称。
    :param param_name: parameter 名称。
    :param param_value: 要写入该 parameter 的值。
    """
    cmd = f"ros2 param set {node_name} {param_name} {param_value}"
    success, output = execute_ros_command(cmd)

    if not success:
        return {"error": output}

    return {param_name: output}


@tool
def ros2_service_info(services: List[str]) -> dict:
    """
    获取 ROS2 service 的信息。

    :param services: ROS2 service 名称列表。
    """
    data = {}

    for service_name in services:
        cmd = f"ros2 service type {service_name}"
        success, output = execute_ros_command(cmd)

        if not success:
            data[service_name] = dict(error=output)
            continue

        data[service_name] = output

    return data


@tool
def ros2_service_call(service_name: str, srv_type: str, request: str) -> dict:
    """
    调用 ROS2 service。

    :param service_name: ROS2 service 名称。
    :param srv_type: service 类型，请使用 ros2_service_info 验证。
    :param request: 发送给 service 的请求内容。
    """
    cmd = f'ros2 service call {service_name} {srv_type} "{request}"'
    success, output = execute_ros_command(cmd)
    if not success:
        return {"error": output}
    return {"response": output}


@tool
def ros2_doctor() -> dict:
    """
    检查 ROS 配置和其他潜在问题。
    """
    cmd = "ros2 doctor"
    success, output = execute_ros_command(cmd)
    if not success:
        return {"error": output}
    return {"results": output}


def ros2_log_directories():
    """获取所有可用的 ROS2 日志目录。"""
    if get_logging_directory is None:
        log_dir = os.path.join(os.path.expanduser("~"), ".ros", "log")
    else:
        log_dir = get_logging_directory()
    print(f"ROS 2 日志存储在：{log_dir}")

    return {"default": f"{log_dir}"}


@tool
def roslog_list(min_size: int = 2048, blacklist: Optional[List[str]] = None) -> dict:
    """
    返回 ROS 日志文件列表。

    :param min_size: 日志文件被纳入列表所需的最小大小，单位为字节。
    """

    logs = []
    log_dirs = ros2_log_directories()

    for _, log_dir in log_dirs.items():
        if not log_dir:
            continue

        # 获取目录中的所有 .log 文件。
        log_files = [
            os.path.join(log_dir, f)
            for f in os.listdir(log_dir)
            if os.path.isfile(os.path.join(log_dir, f)) and f.endswith(".log")
        ]

        print(f"日志文件：{log_files}")

        # 过滤命中黑名单的文件。
        if blacklist:
            log_files = list(
                filter(
                    lambda x: not any(
                        re.match(f".*{pattern}.*", x) for pattern in blacklist
                    ),
                    log_files,
                )
            )

        # 过滤过小的文件。
        log_files = list(filter(lambda x: os.path.getsize(x) > min_size, log_files))

        # 获取每个日志文件的大小；小于 1 MB 时用 KB 表示，否则用 MB 表示。
        log_files = [
            {
                f.replace(log_dir, ""): (
                    f"{round(os.path.getsize(f) / 1024, 2)} KB"
                    if os.path.getsize(f) < 1024 * 1024
                    else f"{round(os.path.getsize(f) / (1024 * 1024), 2)} MB"
                ),
            }
            for f in log_files
        ]

        if len(log_files) > 0:
            logs.append(
                {
                    "directory": log_dir,
                    "total": len(log_files),
                    "files": log_files,
                }
            )

    return dict(
        total=len(logs),
        logs=logs,
    )
