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

import math
import statistics
from typing import List

from langchain_core.tools import tool


@tool
def add_all(numbers: List[float]) -> float:
    """返回一组数字的总和。"""
    return sum(numbers)


@tool
def multiply_all(numbers: List[float]) -> float:
    """返回一组数字的乘积。"""
    result = 1
    for number in numbers:
        result *= number
    return result


@tool
def mean(numbers: List[float]) -> dict:
    """返回一组数字的均值和标准差。"""
    return {
        "mean": statistics.mean(numbers),
        "stdev": statistics.stdev(numbers),
    }


@tool
def median(numbers: List[float]) -> float:
    """返回一组数字的中位数。"""
    return statistics.median(numbers)


@tool
def mode(numbers: List[float]) -> List[float]:
    """返回一组数字的众数。"""
    return statistics.mode(numbers)


@tool
def variance(numbers: List[float]) -> float:
    """返回一组数字的方差。"""
    return statistics.variance(numbers)


@tool
def add(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行加法。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}+{y}": x + y,
        }
        results.append(result)
    return results


@tool
def subtract(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行减法。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}-{y}": x - y,
        }
        results.append(result)
    return results


@tool
def multiply(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行乘法。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}*{y}": x * y,
        }
        results.append(result)
    return results


@tool
def divide(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行除法。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}/{y}": x / y if y != 0 else "undefined",
        }
        results.append(result)
    return results


@tool
def exponentiate(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行幂运算。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}^{y}": x**y,
        }
        results.append(result)
    return results


@tool
def modulo(xy_pairs: List[tuple]) -> List[dict]:
    """对输入的 x 和 y 执行取模运算。

    :arg xy_pairs: 包含输入值 x 和 y 的元组列表，例如 [(x1, y1), (x2, y2), ...]。
    """
    results = []
    for x, y in xy_pairs:
        result = {
            f"{x}%{y}": x % y if y != 0 else "undefined",
        }
        results.append(result)
    return results


@tool
def sine(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行正弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"sin({x})": math.sin(x),
        }
        results.append(result)
    return results


@tool
def cosine(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行余弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"cos({x})": math.cos(x),
        }
        results.append(result)
    return results


@tool
def tangent(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行正切计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"tan({x})": math.tan(x),
        }
        results.append(result)
    return results


@tool
def asin(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行反正弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        try:
            result = {
                f"asin({x})": math.asin(x),
            }
        except ValueError:
            result = {
                f"asin({x})": "undefined",
            }
        results.append(result)
    return results


@tool
def acos(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行反余弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        try:
            result = {
                f"acos({x})": math.acos(x),
            }
        except ValueError:
            result = {
                f"acos({x})": "undefined",
            }
        results.append(result)
    return results


@tool
def atan(x_values: List[float]) -> List[dict]:
    """
    计算输入值的反正切，返回弧度制角度。
    当你需要根据斜率（rise/run）求角度时，可以使用这个工具。
    
    如果要计算从点 A 到点 B 的方向角，应优先使用 atan2，因为它更适合该场景。
    
    示例：atan(1) = π/4 ≈ 0.785 弧度 = 45 度。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"atan({x})": math.atan(x),
        }
        results.append(result)
    return results


@tool
def sinh(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行双曲正弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"sinh({x})": math.sinh(x),
        }
        results.append(result)
    return results


@tool
def cosh(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行双曲余弦计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"cosh({x})": math.cosh(x),
        }
        results.append(result)
    return results


@tool
def tanh(x_values: List[float]) -> List[dict]:
    """对输入值 x 执行双曲正切计算。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        result = {
            f"tanh({x})": math.tanh(x),
        }
        results.append(result)
    return results


@tool
def count_list(items: List) -> int:
    """返回列表中的元素数量。"""
    return len(items)


@tool
def count_words(text: str) -> int:
    """返回字符串中的单词数量。"""
    return len(text.split())


@tool
def count_lines(text: str) -> int:
    """返回字符串中的行数。"""
    return len(text.split("\n"))


@tool
def degrees_to_radians(degrees: List[float]):
    """
    将角度从度转换为弧度。需要做角度单位转换时使用它。

    :param degrees: 一个或多个需要转换为弧度的度数。
    """
    rads = {}
    for degree in degrees:
        rads[degree] = degree * (math.pi / 180)
    return rads


@tool
def radians_to_degrees(radians: List[float]):
    """
    将角度从弧度转换为度。需要做角度单位转换时使用它。

    :param radians: 一个或多个需要转换为度的弧度值。
    """
    degs = {}
    for radian in radians:
        degs[radian] = radian * (180 / math.pi)
    return degs


@tool
def sqrt(x_values: List[float]) -> List[dict]:
    """
    计算输入值的平方根。距离计算中经常需要使用该工具。

    :arg x_values: x 值列表，例如 [x1, x2, ...]。
    """
    results = []
    for x in x_values:
        if x < 0:
            result = {f"sqrt({x})": "未定义（负数）"}
        else:
            result = {f"sqrt({x})": math.sqrt(x)}
        results.append(result)
    return results


@tool
def atan2(pairs: List[tuple]) -> List[dict]:
    """
    计算从 x 轴正方向到点 (x, y) 的角度，结果为弧度。
    这是计算两点之间方向角时最重要的工具。
    
    如果要计算从点 (x1, y1) 到点 (x2, y2) 的角度：
    使用 atan2(y2-y1, x2-x1)。
    
    示例：从 (1, 1) 到 (3, 4) 的角度 = atan2(4-1, 3-1) = atan2(3, 2) ≈ 0.98 弧度。

    :arg pairs: 元组列表，每个元组按 (y, x) 顺序提供值，例如 [(y1, x1), (y2, x2), ...]。
    """
    results = []
    for y, x in pairs:
        result = {f"atan2({y}, {x})": math.atan2(y, x)}
        results.append(result)
    return results


@tool
def distance_between_points(point_pairs: List[tuple]) -> List[dict]:
    """
    使用勾股定理计算两点之间的直线距离。
    公式：sqrt((x2-x1)^2 + (y2-y1)^2)。
    
    该工具对于确定 turtle 需要移动多远非常重要。

    :arg point_pairs: 元组列表，每个元素格式为 ((x1, y1), (x2, y2))。
    示例：[((0, 0), (3, 4))] 会计算从 (0,0) 到 (3,4) 的距离，结果为 5.0。
    """
    results = []
    for (x1, y1), (x2, y2) in point_pairs:
        dx = x2 - x1
        dy = y2 - y1
        dist = math.sqrt(dx**2 + dy**2)
        result = {f"从 ({x1},{y1}) 到 ({x2},{y2}) 的距离": dist}
        results.append(result)
    return results


@tool
def calculate_line_angle_and_distance(point_pairs: List[tuple]) -> List[dict]:
    """
    同时计算从点 A 到点 B 画线所需的角度（弧度）和距离。
    这是一个组合了 atan2 和距离计算的高层辅助工具。
    
    当你计划在两个具体坐标之间画线时，应使用该工具。
    返回角度相对于 x 轴正方向（向右 = 0，向上 = π/2）。

    :arg point_pairs: 元组列表，每个元素格式为 ((x1, y1), (x2, y2))。
    示例：[((2, 3), (5, 7))] 返回从 (2,3) 到 (5,7) 的角度和距离。
    """
    results = []
    for (x1, y1), (x2, y2) in point_pairs:
        dx = x2 - x1
        dy = y2 - y1
        angle = math.atan2(dy, dx)
        distance = math.sqrt(dx**2 + dy**2)
        result = {
            f"从 ({x1},{y1}) 到 ({x2},{y2}) 的线段": {
                "angle_radians": angle,
                "angle_degrees": angle * (180 / math.pi),
                "distance": distance,
            }
        }
        results.append(result)
    return results
