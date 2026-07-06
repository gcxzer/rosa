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
from typing import Optional, Literal

from langchain_core.tools import tool


@tool
def read_log(
    log_file_directory: str,
    log_filename: str,
    level_filter: Optional[
        Literal["ERROR", "INFO", "DEBUG", "WARNING", "CRITICAL", "FATAL", "TRACE"]
    ] = None,
    num_lines: Optional[int] = None,
) -> dict:
    """
    读取日志文件，并返回符合日志级别过滤条件和行数范围的日志行。

    :param log_file_directory: 包含待读取日志文件的目录，请先使用工具获取正确目录。
    :param log_filename: 需要读取的日志文件路径。
    :param level_filter: 只显示包含该日志级别的行，例如 "ERROR"、"INFO"、"DEBUG" 等。
    :param num_lines: 从日志文件末尾返回的最近行数。
    """
    if num_lines is not None and num_lines < 1:
        return {"error": "`num_lines` 参数无效。它必须是一个正整数。"}

    if not os.path.exists(log_file_directory):
        return {
            "error": f"日志目录 '{log_file_directory}' 不存在。你应该先使用工具获取正确的日志目录。"
        }

    full_log_path = os.path.join(log_file_directory, log_filename)

    if not os.path.exists(full_log_path):
        return {
            "error": f"日志文件 '{log_filename}' 不存在于日志目录 '{log_file_directory}' 中。"
        }

    if not os.path.isfile(full_log_path):
        return {"error": f"路径 '{full_log_path}' 不是文件。"}

    with open(full_log_path, "r") as f:
        log_lines = f.readlines()

    total_lines = len(log_lines)

    for i in range(len(log_lines)):
        log_lines[i] = f"第 {i+1} 行： " + log_lines[i].strip()

    if num_lines is not None:
        # 从日志文件末尾取出最近的 num_lines 行。
        log_lines = log_lines[-num_lines:]

    # 如果日志行数超过 200 行，提示调用者使用 num_lines 参数分批读取。
    if len(log_lines) > 200:
        return {
            "error": f"日志文件 '{log_filename}' 超过 200 行。请使用 `num_lines` 参数，每次只读取日志文件的一部分。"
        }

    if level_filter is not None:
        log_lines = [line for line in log_lines if level_filter.lower() in line.lower()]

    result = {
        "log_filename": log_filename,
        "log_file_directory": log_file_directory,
        "level_filter": level_filter,
        "requested_num_lines": num_lines,
        "total_lines": total_lines,
        "lines_returned": len(log_lines),
        "lines": log_lines,
    }

    return result
