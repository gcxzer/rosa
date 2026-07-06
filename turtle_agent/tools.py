"""TurtleSim 专用 LangChain tools。

这些工具都通过 `ros2` CLI 访问 turtlesim，不在导入时依赖 `rclpy` 或 `turtlesim`
Python 包。这样做的好处是：普通开发机即使没有安装 ROS2，也可以导入模块和运行单元测试；
只有真正调用工具时，才要求当前 shell 已经 source ROS2 环境，并且 turtlesim 正在运行。
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from typing import Any, List, Optional, Tuple

from langchain_core.tools import tool


_TURTLESIM_MIN = 0.0
_TURTLESIM_MAX = 11.0
_DEFAULT_TURTLE = "turtle1"
_DEFAULT_LINE_COLOR = {"r": 0, "g": 0, "b": 0, "width": 2}

@tool
def turtle_get_pose(name: str = _DEFAULT_TURTLE, timeout: float = 2.0) -> dict:
    """
    读取某只 turtle 的当前位置和速度。

    :param name: turtle 名称，不要带前导斜杠，例如 turtle1。
    :param timeout: 等待 pose 消息的最长时间，单位为秒。
    """
    name = _normalize_turtle_name(name)
    success, output = _run_ros2(
        [
            "ros2",
            "topic",
            "echo",
            f"/{name}/pose",
            "--once",
            "--spin-time",
            str(timeout),
        ],
        timeout=timeout + 3.0,
    )
    if not success:
        return {"success": False, "error": output}

    pose: dict[str, float] = {}
    for line in output.splitlines():
        match = re.match(
            r"^(x|y|theta|linear_velocity|angular_velocity):\s*"
            r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
            line.strip(),
        )
        if match:
            pose[match.group(1)] = float(match.group(2))

    if not pose:
        return {"success": False, "error": "没有从 pose topic 输出中解析到坐标。", "raw": output}
    pose["name"] = name
    return {"success": True, "pose": pose}


@tool
def turtle_spawn(
    name: str,
    x: float,
    y: float,
    theta: float = 0.0,
) -> dict:
    """
    在 turtlesim 中创建一只新的 turtle。

    :param name: 新 turtle 名称，不要带前导斜杠。
    :param x: 初始 x 坐标，范围 0 到 11。
    :param y: 初始 y 坐标，范围 0 到 11。
    :param theta: 初始朝向，单位为弧度。
    """
    error = _bounds_error(x, y)
    if error:
        return {"success": False, "error": error}
    name = _normalize_turtle_name(name)
    return _service_call(
        "/spawn",
        "turtlesim/srv/Spawn",
        {"x": float(x), "y": float(y), "theta": float(theta), "name": name},
    )


@tool
def turtle_kill(name: str) -> dict:
    """
    删除一只 turtle。

    :param name: 需要删除的 turtle 名称，不要带前导斜杠。
    """
    name = _normalize_turtle_name(name)
    return _service_call("/kill", "turtlesim/srv/Kill", {"name": name})


@tool
def turtlesim_clear() -> dict:
    """清空 turtlesim 画布，但保留当前 turtle。"""
    return _service_call("/clear", "std_srvs/srv/Empty", {})


@tool
def turtlesim_reset() -> dict:
    """重置 turtlesim，清空画布并恢复默认 turtle1。"""
    return _service_call("/reset", "std_srvs/srv/Empty", {})


@tool
def turtlesim_set_background(r: int, g: int, b: int, clear_after: bool = True) -> dict:
    """
    设置 turtlesim 背景色。

    :param r: 红色通道，0 到 255。
    :param g: 绿色通道，0 到 255。
    :param b: 蓝色通道，0 到 255。
    :param clear_after: 是否调用 /clear 让背景色立即刷新。
    """
    for channel, value in {"r": r, "g": g, "b": b}.items():
        error = _byte_error(channel, value)
        if error:
            return {"success": False, "error": error}

    results = {}
    for param_name, value in {
        "background_r": r,
        "background_g": g,
        "background_b": b,
    }.items():
        success, output = _run_ros2(
            ["ros2", "param", "set", "/turtlesim", param_name, str(int(value))]
        )
        results[param_name] = {"success": success, "output": output}
        if not success:
            return {"success": False, "error": output, "results": results}

    if clear_after:
        clear_result = _service_call("/clear", "std_srvs/srv/Empty", {})
        results["clear"] = clear_result
        if not clear_result.get("success"):
            return {"success": False, "error": clear_result.get("error"), "results": results}

    return {"success": True, "color": {"r": r, "g": g, "b": b}, "results": results}


@tool
def turtle_set_pen(
    name: str = _DEFAULT_TURTLE,
    r: int = 0,
    g: int = 0,
    b: int = 0,
    width: int = 2,
    off: int = 0,
) -> dict:
    """
    设置 turtle 画笔。

    :param name: turtle 名称，不要带前导斜杠。
    :param r: 红色通道，0 到 255。
    :param g: 绿色通道，0 到 255。
    :param b: 蓝色通道，0 到 255。
    :param width: 线宽，建议 1 到 5。
    :param off: 0 表示开启画笔，1 表示关闭画笔。
    """
    return _set_pen_raw(name, r, g, b, width, off)


@tool
def turtle_teleport_absolute(
    name: str = _DEFAULT_TURTLE,
    x: float = 5.544,
    y: float = 5.544,
    theta: float = 0.0,
    hide_pen: bool = True,
) -> dict:
    """
    将 turtle 精确传送到指定坐标和朝向。

    :param name: turtle 名称，不要带前导斜杠。
    :param x: 目标 x 坐标，范围 0 到 11。
    :param y: 目标 y 坐标，范围 0 到 11。
    :param theta: 目标朝向，单位为弧度。
    :param hide_pen: 传送期间是否临时关闭画笔，避免留下无意义线条。
    """
    return _teleport_absolute_raw(name, x, y, theta, hide_pen)


@tool
def turtle_teleport_relative(
    name: str = _DEFAULT_TURTLE,
    linear: float = 0.0,
    angular: float = 0.0,
) -> dict:
    """
    按当前姿态相对移动或旋转 turtle。

    :param name: turtle 名称，不要带前导斜杠。
    :param linear: 前进距离，负数表示后退。
    :param angular: 旋转角度，单位为弧度，正数表示逆时针。
    """
    name = _normalize_turtle_name(name)
    return _service_call(
        f"/{name}/teleport_relative",
        "turtlesim/srv/TeleportRelative",
        {"linear": float(linear), "angular": float(angular)},
    )


@tool
def turtle_publish_twist(
    name: str = _DEFAULT_TURTLE,
    linear_x: float = 0.0,
    angular_z: float = 0.0,
    linear_y: float = 0.0,
    duration: float = 1.0,
    rate: float = 10.0,
    stop_after: bool = True,
) -> dict:
    """
    向 turtle 的 cmd_vel topic 发布 Twist 速度命令。

    :param name: turtle 名称，不要带前导斜杠。
    :param linear_x: 前进速度，单位约为 turtlesim 坐标单位/秒。
    :param angular_z: 角速度，单位为弧度/秒。
    :param linear_y: 横向速度，通常保持 0。
    :param duration: 发布持续时间，单位为秒。
    :param rate: 发布频率，单位为 Hz。
    :param stop_after: 发布结束后是否立刻发送零速度命令。
    """
    return _publish_twist_raw(name, linear_x, angular_z, linear_y, duration, rate, stop_after)


@tool
def turtle_stop(name: str = _DEFAULT_TURTLE) -> dict:
    """
    停止 turtle 运动。

    :param name: turtle 名称，不要带前导斜杠。
    """
    return _publish_twist_raw(
        name=name,
        linear_x=0.0,
        angular_z=0.0,
        linear_y=0.0,
        duration=0.1,
        rate=10.0,
        stop_after=False,
    )


@tool
def draw_line_segment(
    name: str = _DEFAULT_TURTLE,
    x1: float = 0.0,
    y1: float = 0.0,
    x2: float = 1.0,
    y2: float = 1.0,
    r: int = _DEFAULT_LINE_COLOR["r"],
    g: int = _DEFAULT_LINE_COLOR["g"],
    b: int = _DEFAULT_LINE_COLOR["b"],
    width: int = _DEFAULT_LINE_COLOR["width"],
) -> dict:
    """
    画一条从 (x1, y1) 到 (x2, y2) 的直线。

    :param name: turtle 名称，不要带前导斜杠。
    :param x1: 起点 x 坐标。
    :param y1: 起点 y 坐标。
    :param x2: 终点 x 坐标。
    :param y2: 终点 y 坐标。
    :param r: 线条红色通道，0 到 255。
    :param g: 线条绿色通道，0 到 255。
    :param b: 线条蓝色通道，0 到 255。
    :param width: 线宽。
    """
    return _draw_line_segment_raw(name, x1, y1, x2, y2, r, g, b, width)


@tool
def draw_polyline(
    name: str,
    points: List[Tuple[float, float]],
    closed: bool = False,
    r: int = _DEFAULT_LINE_COLOR["r"],
    g: int = _DEFAULT_LINE_COLOR["g"],
    b: int = _DEFAULT_LINE_COLOR["b"],
    width: int = _DEFAULT_LINE_COLOR["width"],
) -> dict:
    """
    按点列表绘制折线，可选择自动闭合。

    :param name: turtle 名称，不要带前导斜杠。
    :param points: 坐标点列表，例如 [[2, 2], [5, 2], [5, 5]]。
    :param closed: 是否从最后一个点再画回第一个点。
    :param r: 线条红色通道。
    :param g: 线条绿色通道。
    :param b: 线条蓝色通道。
    :param width: 线宽。
    """
    if len(points) < 2:
        return {"success": False, "error": "至少需要两个点才能绘制折线。"}

    draw_points = list(points)
    if closed and len(draw_points) > 2:
        draw_points.append(draw_points[0])

    segments = []
    for index in range(len(draw_points) - 1):
        x1, y1 = draw_points[index]
        x2, y2 = draw_points[index + 1]
        result = _draw_line_segment_raw(name, x1, y1, x2, y2, r, g, b, width)
        segments.append(result)
        if not result.get("success"):
            return {
                "success": False,
                "error": f"第 {index + 1} 段绘制失败：{result.get('error')}",
                "segments": segments,
            }

    return {"success": True, "segments_drawn": len(segments), "closed": closed, "segments": segments}


@tool
def draw_rectangle(
    name: str = _DEFAULT_TURTLE,
    x: float = 2.0,
    y: float = 2.0,
    width: float = 1.0,
    height: float = 1.0,
    filled: bool = False,
    r: int = _DEFAULT_LINE_COLOR["r"],
    g: int = _DEFAULT_LINE_COLOR["g"],
    b: int = _DEFAULT_LINE_COLOR["b"],
    line_width: int = _DEFAULT_LINE_COLOR["width"],
) -> dict:
    """
    绘制矩形，默认只画边框。

    :param name: turtle 名称，不要带前导斜杠。
    :param x: 左下角 x 坐标。
    :param y: 左下角 y 坐标。
    :param width: 矩形宽度。
    :param height: 矩形高度。
    :param filled: 是否用水平线填充矩形。
    :param r: 线条红色通道。
    :param g: 线条绿色通道。
    :param b: 线条蓝色通道。
    :param line_width: 线宽。
    """
    if width <= 0 or height <= 0:
        return {"success": False, "error": "width 和 height 必须大于 0。"}

    points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    outline = draw_polyline.invoke(
        {
            "name": name,
            "points": points,
            "closed": True,
            "r": r,
            "g": g,
            "b": b,
            "width": line_width,
        }
    )
    if not outline.get("success"):
        return outline

    fill_segments = []
    if filled:
        # 填充只用水平线，间隔随矩形高度自动收敛，避免小矩形生成过多命令。
        step = max(0.15, min(0.4, height / 12.0))
        current_y = y + step
        while current_y < y + height:
            result = _draw_line_segment_raw(name, x, current_y, x + width, current_y, r, g, b, 1)
            fill_segments.append(result)
            if not result.get("success"):
                return {
                    "success": False,
                    "error": f"矩形填充失败：{result.get('error')}",
                    "outline": outline,
                    "fill_segments": fill_segments,
                }
            current_y += step

    return {
        "success": True,
        "bounds": calculate_rectangle_bounds.invoke(
            {"x": x, "y": y, "width": width, "height": height}
        ),
        "outline": outline,
        "fill_segments": fill_segments,
    }


@tool
def draw_circle(
    name: str = _DEFAULT_TURTLE,
    center_x: float = 5.5,
    center_y: float = 5.5,
    radius: float = 1.0,
    segments: int = 36,
    r: int = _DEFAULT_LINE_COLOR["r"],
    g: int = _DEFAULT_LINE_COLOR["g"],
    b: int = _DEFAULT_LINE_COLOR["b"],
    width: int = _DEFAULT_LINE_COLOR["width"],
) -> dict:
    """
    用多段短线近似绘制圆。

    :param name: turtle 名称，不要带前导斜杠。
    :param center_x: 圆心 x 坐标。
    :param center_y: 圆心 y 坐标。
    :param radius: 半径。
    :param segments: 分段数量，越大越平滑，但执行更慢。
    :param r: 线条红色通道。
    :param g: 线条绿色通道。
    :param b: 线条蓝色通道。
    :param width: 线宽。
    """
    if radius <= 0:
        return {"success": False, "error": "radius 必须大于 0。"}
    if segments < 6 or segments > 120:
        return {"success": False, "error": "segments 建议在 6 到 120 之间。"}

    points = []
    for index in range(segments):
        angle = 2 * math.pi * index / segments
        points.append((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))

    return draw_polyline.invoke(
        {
            "name": name,
            "points": points,
            "closed": True,
            "r": r,
            "g": g,
            "b": b,
            "width": width,
        }
    )


@tool
def draw_arc(
    name: str = _DEFAULT_TURTLE,
    center_x: float = 5.5,
    center_y: float = 5.5,
    radius: float = 1.0,
    start_angle: float = 0.0,
    arc_angle: float = math.pi,
    segments: int = 18,
    r: int = _DEFAULT_LINE_COLOR["r"],
    g: int = _DEFAULT_LINE_COLOR["g"],
    b: int = _DEFAULT_LINE_COLOR["b"],
    width: int = _DEFAULT_LINE_COLOR["width"],
) -> dict:
    """
    用多段短线近似绘制圆弧。

    :param name: turtle 名称，不要带前导斜杠。
    :param center_x: 圆心 x 坐标。
    :param center_y: 圆心 y 坐标。
    :param radius: 半径。
    :param start_angle: 起始角度，单位为弧度。
    :param arc_angle: 扫过角度，正数逆时针，负数顺时针。
    :param segments: 分段数量。
    :param r: 线条红色通道。
    :param g: 线条绿色通道。
    :param b: 线条蓝色通道。
    :param width: 线宽。
    """
    if radius <= 0:
        return {"success": False, "error": "radius 必须大于 0。"}
    if segments < 2 or segments > 120:
        return {"success": False, "error": "segments 建议在 2 到 120 之间。"}
    if abs(arc_angle) < 0.001:
        return {"success": False, "error": "arc_angle 太小，无法绘制可见圆弧。"}

    points = []
    for index in range(segments + 1):
        angle = start_angle + arc_angle * index / segments
        points.append((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))

    return draw_polyline.invoke(
        {
            "name": name,
            "points": points,
            "closed": False,
            "r": r,
            "g": g,
            "b": b,
            "width": width,
        }
    )


@tool
def calculate_rectangle_bounds(x: float, y: float, width: float, height: float) -> dict:
    """
    计算矩形四个角、中心点和坐标范围。

    :param x: 左下角 x 坐标。
    :param y: 左下角 y 坐标。
    :param width: 矩形宽度。
    :param height: 矩形高度。
    """
    return {
        "bottom_left": [x, y],
        "bottom_right": [x + width, y],
        "top_right": [x + width, y + height],
        "top_left": [x, y + height],
        "center": [x + width / 2, y + height / 2],
        "x_range": [x, x + width],
        "y_range": [y, y + height],
        "width": width,
        "height": height,
    }


@tool
def check_rectangles_overlap(
    rect1: Tuple[float, float, float, float],
    rect2: Tuple[float, float, float, float],
) -> dict:
    """
    检查两个矩形是否重叠。

    :param rect1: 第一个矩形，格式为 [x, y, width, height]。
    :param rect2: 第二个矩形，格式为 [x, y, width, height]。
    """
    x1, y1, w1, h1 = rect1
    x2, y2, w2, h2 = rect2
    if w1 <= 0 or h1 <= 0 or w2 <= 0 or h2 <= 0:
        return {"success": False, "error": "两个矩形的 width 和 height 都必须大于 0。"}

    left = max(x1, x2)
    right = min(x1 + w1, x2 + w2)
    bottom = max(y1, y2)
    top = min(y1 + h1, y2 + h2)
    overlap = left < right and bottom < top

    return {
        "success": True,
        "overlap": overlap,
        "overlap_region": (
            {"x": left, "y": bottom, "width": right - left, "height": top - bottom}
            if overlap
            else None
        ),
        "rect1_bounds": {"x_range": [x1, x1 + w1], "y_range": [y1, y1 + h1]},
        "rect2_bounds": {"x_range": [x2, x2 + w2], "y_range": [y2, y2 + h2]},
    }

def _run_ros2(args: list[str], timeout: float = 10.0) -> tuple[bool, str]:
    """执行一条 ROS2 CLI 命令，并把成功状态和文本输出返回给工具函数。

    这里使用参数列表而不是 shell 字符串，避免 YAML/JSON 请求内容里出现引号时被 shell
    二次解析。工具层只关心“是否成功”和“输出内容”，因此统一把 stdout/stderr 合并成字符串。
    """
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, "找不到 ros2 命令。请确认已经安装 ROS2，并在当前 shell 中 source 了 ROS2 环境。"
    except subprocess.TimeoutExpired:
        return False, f"命令超时：{' '.join(args)}"

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, output or f"命令失败，返回码：{result.returncode}"
    return True, output


def _normalize_turtle_name(name: str) -> str:
    """把用户或模型传入的 turtle 名称规范成 service/topic 使用的格式。"""
    normalized = str(name or _DEFAULT_TURTLE).strip().lstrip("/")
    return normalized or _DEFAULT_TURTLE


def _bounds_error(x: float, y: float) -> Optional[str]:
    """检查坐标是否在 turtlesim 的 11x11 平面内；合法时返回 None。"""
    if _TURTLESIM_MIN <= x <= _TURTLESIM_MAX and _TURTLESIM_MIN <= y <= _TURTLESIM_MAX:
        return None
    return f"坐标 ({x}, {y}) 超出 turtlesim 范围；x 和 y 都必须在 0 到 11 之间。"


def _byte_error(channel: str, value: int) -> Optional[str]:
    """检查 RGB 和线宽这类整数参数是否落在 ROS service 可接受的范围内。"""
    if 0 <= int(value) <= 255:
        return None
    return f"{channel} 必须在 0 到 255 之间，当前值是 {value}。"


def _service_call(
    service_name: str,
    service_type: str,
    request: dict[str, Any],
    timeout: float = 10.0,
) -> dict:
    """调用 ROS2 service，并返回统一结构，方便 agent 判断成功或失败。"""
    success, output = _run_ros2(
        [
            "ros2",
            "service",
            "call",
            service_name,
            service_type,
            json.dumps(request),
        ],
        timeout=timeout,
    )
    if not success:
        return {"success": False, "error": output}
    return {"success": True, "output": output}


def _set_pen_raw(
    name: str,
    r: int,
    g: int,
    b: int,
    width: int,
    off: int,
) -> dict:
    """底层画笔设置逻辑；高层绘图工具会频繁复用它。"""
    name = _normalize_turtle_name(name)
    for channel, value in {"r": r, "g": g, "b": b, "width": width, "off": off}.items():
        error = _byte_error(channel, value)
        if error:
            return {"success": False, "error": error}

    return _service_call(
        f"/{name}/set_pen",
        "turtlesim/srv/SetPen",
        {
            "r": int(r),
            "g": int(g),
            "b": int(b),
            "width": int(width),
            "off": int(off),
        },
    )


def _teleport_absolute_raw(
    name: str,
    x: float,
    y: float,
    theta: float,
    hide_pen: bool,
) -> dict:
    """底层绝对传送逻辑；绘图工具用它把 turtle 精确放到起点。"""
    name = _normalize_turtle_name(name)
    error = _bounds_error(x, y)
    if error:
        return {"success": False, "error": error}

    if hide_pen:
        pen_result = _set_pen_raw(name, 0, 0, 0, 1, 1)
        if not pen_result.get("success"):
            return pen_result

    result = _service_call(
        f"/{name}/teleport_absolute",
        "turtlesim/srv/TeleportAbsolute",
        {"x": float(x), "y": float(y), "theta": float(theta)},
    )

    if hide_pen and result.get("success"):
        # turtlesim 的 set_pen 不提供“恢复旧颜色”的查询接口。这里恢复到 TurtleAgent 的默认蓝色，
        # 后续高层绘图工具会在真正画线前再设置用户指定的颜色。
        restore_result = _set_pen_raw(name, 30, 30, 255, 1, 0)
        if not restore_result.get("success"):
            return restore_result

    return result


def _publish_twist_raw(
    name: str,
    linear_x: float,
    angular_z: float,
    linear_y: float,
    duration: float,
    rate: float,
    stop_after: bool,
) -> dict:
    """底层速度发布逻辑；用于直接移动，也用于高层线段绘制。"""
    name = _normalize_turtle_name(name)
    if duration <= 0:
        return {"success": False, "error": "duration 必须大于 0。"}
    if rate <= 0:
        return {"success": False, "error": "rate 必须大于 0。"}

    twist = {
        "linear": {"x": float(linear_x), "y": float(linear_y), "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": float(angular_z)},
    }
    times = max(1, int(round(duration * rate)))
    success, output = _run_ros2(
        [
            "ros2",
            "topic",
            "pub",
            "--rate",
            str(rate),
            "--times",
            str(times),
            f"/{name}/cmd_vel",
            "geometry_msgs/msg/Twist",
            json.dumps(twist),
        ],
        timeout=max(5.0, duration + 5.0),
    )
    if not success:
        return {"success": False, "error": output}

    if stop_after:
        stop_twist = {
            "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
        stop_success, stop_output = _run_ros2(
            [
                "ros2",
                "topic",
                "pub",
                "--once",
                f"/{name}/cmd_vel",
                "geometry_msgs/msg/Twist",
                json.dumps(stop_twist),
            ],
            timeout=5.0,
        )
        if not stop_success:
            return {"success": False, "error": stop_output}

    return {"success": True, "output": output}


def _draw_line_segment_raw(
    name: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: int,
    g: int,
    b: int,
    width: int,
) -> dict:
    """底层线段绘制逻辑；矩形、折线、圆和弧都会复用它。"""
    start_error = _bounds_error(x1, y1)
    if start_error:
        return {"success": False, "error": f"起点错误：{start_error}"}
    end_error = _bounds_error(x2, y2)
    if end_error:
        return {"success": False, "error": f"终点错误：{end_error}"}

    dx = x2 - x1
    dy = y2 - y1
    distance = math.sqrt(dx**2 + dy**2)
    if distance <= 0:
        return {"success": False, "error": "线段长度必须大于 0。"}

    name = _normalize_turtle_name(name)
    angle = math.atan2(dy, dx)
    teleport_result = _teleport_absolute_raw(name, x1, y1, angle, hide_pen=True)
    if not teleport_result.get("success"):
        return teleport_result

    pen_result = _set_pen_raw(name, r, g, b, width, 0)
    if not pen_result.get("success"):
        return pen_result

    move_result = _publish_twist_raw(
        name=name,
        linear_x=distance,
        angular_z=0.0,
        linear_y=0.0,
        duration=1.0,
        rate=10.0,
        stop_after=True,
    )
    if not move_result.get("success"):
        return move_result

    return {
        "success": True,
        "from": [x1, y1],
        "to": [x2, y2],
        "distance": distance,
        "theta": angle,
    }
