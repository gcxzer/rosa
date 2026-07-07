"""ArmAgent 专用 LangChain tools。

"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

from langchain_core.tools import tool

from .moveit_client import (
    DEFAULT_BASE_FRAME,
    DEFAULT_END_EFFECTOR_LINK,
    DEFAULT_GRIPPER_OPENING_WIDTH,
    DEFAULT_PLANNING_GROUP,
    MAX_GRIPPER_OPENING_WIDTH,
    MoveItRuntimeClient,
)


_CLIENT_FACTORY: Callable[[], Any] = MoveItRuntimeClient
MAX_ARM_PLAN_STEPS = 12
_PLAN_ACTIONS = {
    "move_named",
    "move_joint",
    "move_pose",
    "open_gripper",
    "close_gripper",
    "set_gripper_width",
    "get_joint_states",
    "get_gripper_state",
    "stop",
}
_UNSUPPORTED_COMPLEX_ACTIONS = {
    "parallel",
    "run_parallel",
    "detect_object",
    "object_detection",
    "visual_reasoning",
    "grasp_success",
    "verify_grasp",
}


@tool
def arm_check_readiness() -> dict:
    """检查机械臂运行栈是否可以安全开始规划。

    典型使用场景：
    - 用户要求读取机械臂状态、规划或执行前，先调用这个工具确认外部 ROS2 栈是否在线。
    - 排查为什么 ArmAgent 不能规划或执行时，调用这个工具列出缺失组件。

    检查内容：
    - MoveIt2 `move_group` 是否在 ROS graph 或 service 列表中可见。
    - `/joint_states` topic 是否存在。
    - `robot_state_publisher` 是否存在。
    - `controller_manager` 是否存在。
    - controller 列表或 node 名称中是否能看到 `mujoco_ros2_control` 相关组件。

    返回：
    - `success`: 所有关键组件都可见时为 True。
    - `missing`: 缺失组件名称列表；非空时不要继续规划或执行。
    - `detected`: 已发现的 nodes、topics、services、controllers。
    - `summary`: 面向用户的简短状态说明。
    """
    return _client().check_readiness()


@tool
def arm_get_joint_states() -> dict:
    """读取当前机械臂关节状态。

    典型使用场景：
    - 用户询问机械臂当前姿态、关节角或执行后的最终状态。
    - 规划前后需要确认真实 `/joint_states` 是否变化。

    返回：
    - `success`: 是否成功读取并解析 `/joint_states`。
    - `joint_states`: `{关节名: 当前位置}` 字典，单位通常是弧度或关节原生单位。
    - `names` / `positions`: 与 ROS `sensor_msgs/JointState` 对齐的列表形式。

    注意：
    - 这个工具只读取状态，不会移动机械臂。
    - 如果读取失败，应先调用 `arm_check_readiness` 看 `/joint_states` 是否存在。
    """
    return _client().get_joint_states()


@tool
def arm_get_planning_groups() -> dict:
    """读取 MoveIt2 当前配置的 planning groups。

    典型使用场景：
    - 用户没有指定 planning group，或者你不确定当前机器人有哪些 group。
    - 需要确认默认 `panda_arm` 是否存在。

    返回：
    - `success`: 是否成功从 MoveIt2 SRDF / `robot_description_semantic` 读取配置。
    - `planning_groups`: 可用 group 名称列表，例如 `panda_arm`。
    - `source`: 信息来源，通常是 `robot_description_semantic`。

    注意：
    - 这个工具只读 MoveIt2 配置，不会规划或执行。
    """
    return _client().get_planning_groups()


@tool
def arm_get_named_targets(planning_group: str = DEFAULT_PLANNING_GROUP) -> dict:
    """读取某个 planning group 的 MoveIt2 named targets。

    参数：
    - `planning_group`: MoveIt2 planning group 名称。默认是 Panda MVP 的 `panda_arm`。

    典型使用场景：
    - 用户想“回 home / ready / extended”等预定义姿态，但不确定 target 名称。
    - 调用 `arm_move_to_named_target` 前，先确认目标名确实存在。

    返回：
    - `success`: 是否成功读取 named target。
    - `planning_group`: 本次查询的 group。
    - `named_targets`: target 名称列表。

    注意：
    - 这个工具只读取配置，不会规划，不会执行。
    """
    return _client().get_named_targets(planning_group)


@tool
def arm_get_end_effector_link(planning_group: str = DEFAULT_PLANNING_GROUP) -> dict:
    """读取指定 planning group 使用的末端 link。

    参数：
    - `planning_group`: MoveIt2 planning group 名称。默认是 `panda_arm`。

    典型使用场景：
    - 用户要求末端执行器 pose，但没有说明 end-effector link。
    - 规划 pose goal 前，确认应该使用哪个 link，例如 Panda 的 `panda_hand`。

    返回：
    - `success`: 是否成功获取 link。SRDF 不可读时可能返回默认值并带 warning。
    - `end_effector_link`: 末端 link 名称。
    - `source`: 来源，可能是 `robot_description_semantic` 或 ArmAgent 默认值。

    注意：
    - 这个工具只读配置，不会规划或执行。
    """
    return _client().get_end_effector_link(planning_group)


@tool
def arm_get_end_effector_pose(
    frame_id: str = DEFAULT_BASE_FRAME,
    end_effector_link: str = DEFAULT_END_EFFECTOR_LINK,
) -> dict:
    """读取末端执行器在指定坐标系下的当前位姿。

    参数：
    - `frame_id`: 参考坐标系。默认是 Panda MVP 的 `panda_link0`。如果用户指定了其他 frame，
      必须原样传入，不能自己猜。
    - `end_effector_link`: 末端 link。默认是 `panda_hand`，也可以先用
      `arm_get_end_effector_link` 查询。

    返回：
    - `success`: 是否成功从 TF 读取 pose。
    - `frame_id`: 返回位姿所使用的参考坐标系。
    - `end_effector_link`: 被查询的末端 link。
    - `pose`: position + orientation 四元数。

    注意：
    - 这个工具只读取 TF，不会移动机械臂。
    - 如果 TF 查询失败，应检查 `robot_state_publisher`、`/tf` 和 `/joint_states`。
    """
    return _client().get_end_effector_pose(frame_id, end_effector_link)


@tool
def arm_get_gripper_state() -> dict:
    """读取 Panda 夹爪/夹头当前状态。

    典型使用场景：
    - 用户询问夹爪当前是打开还是闭合。
    - 打开/闭合夹爪后，需要确认 `panda_finger_joint1` 和 `panda_finger_joint2` 的位置。

    返回：
    - `success`: 是否成功读取。
    - `finger_joint_positions`: 两个 finger joint 的当前位置。
    - `estimated_width`: 根据两个 finger joint 估算的两指总开口宽度，单位米。

    注意：
    - 这个工具只读取状态，不会移动夹爪。
    """
    return _client().get_gripper_state()


@tool
def arm_move_to_named_target(
    target_name: str,
    planning_group: str = DEFAULT_PLANNING_GROUP,
    require_readiness: bool = True,
) -> dict:
    """直接移动到 MoveIt2 named target。

    参数：
    - `target_name`: MoveIt2 SRDF 中定义的 named target，例如 `home` 或 `ready`。
    - `planning_group`: MoveIt2 planning group。默认 `panda_arm`。
    - `require_readiness`: 默认 True。为 True 时会先检查 MoveIt2、controller、joint states、
      robot_state_publisher 和 MuJoCo 控制链路是否就绪；未就绪会拒绝移动。

    返回：
    - 成功时返回 `success=True`、`status="executed"`、目标摘要、planning 摘要和最终机器人状态。
    - 失败时返回 `success=False` 和错误原因；规划失败时不会执行。

    说明：
    - 这个工具对用户是“一步执行”：内部把 SRDF named target 解析成明确关节目标，再立即执行。
    - 当前 MuJoCo + Jazzy MoveItPy 存在仿真时间 abort 问题，named target 先直接走
      `panda_arm_controller/joint_trajectory`，避免 MoveItPy 执行管理器把 agent 进程带崩。
    - 不再返回 `plan_id`，也不需要再调用单独的 execute 工具。
    """
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error

    client = _client()
    result = client.plan_to_named_target(planning_group, target_name)
    return _execute_motion_result(
        client,
        result,
        target_type="named_target",
        target={"target_name": target_name},
        planning_group=planning_group,
        frame_id=DEFAULT_BASE_FRAME,
    )


@tool
def arm_move_to_joint_goal(
    joint_goal: dict[str, float],
    planning_group: str = DEFAULT_PLANNING_GROUP,
    require_readiness: bool = True,
) -> dict:
    """直接移动到关节目标。

    参数：
    - `joint_goal`: `{关节名: 目标值}` 字典。目标值必须是数字；Panda 转动关节通常使用弧度。
    - `planning_group`: MoveIt2 planning group。默认 `panda_arm`。
    - `require_readiness`: 默认 True。为 True 时，运行栈未就绪会拒绝移动。

    返回：
    - 成功时返回 `success=True`、执行状态、目标摘要、planning 摘要和最终机器人状态。
    - 如果 joint goal 为空、非数字或 MoveIt2 规划失败，返回 `success=False`，不会执行。

    说明：
    - 工具会先做最小数值校验，用当前 `/joint_states` 补齐未指定关节，再发给
      `panda_arm_controller/joint_trajectory`。
    - 这里不会做 MoveIt 碰撞规划；只适合明确、安全的关节目标。
    - 这个工具内部完成规划并执行，不需要 `plan_id`。
    """
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error

    client = _client()
    validation = client.validate_joint_goal(planning_group, joint_goal)
    if not validation.get("success"):
        return validation

    result = client.plan_to_joint_goal(planning_group, joint_goal)
    return _execute_motion_result(
        client,
        result,
        target_type="joint_goal",
        target={"joint_goal": dict(joint_goal)},
        planning_group=planning_group,
        frame_id=DEFAULT_BASE_FRAME,
    )


@tool
def arm_move_to_pose_goal(
    x: float,
    y: float,
    z: float,
    qx: float = 0.0,
    qy: float = 0.0,
    qz: float = 0.0,
    qw: float = 1.0,
    frame_id: str = DEFAULT_BASE_FRAME,
    end_effector_link: str = DEFAULT_END_EFFECTOR_LINK,
    planning_group: str = DEFAULT_PLANNING_GROUP,
    require_readiness: bool = True,
) -> dict:
    """直接移动末端执行器到 pose 目标。

    参数：
    - `x`, `y`, `z`: 末端目标位置，单位是米，表达在 `frame_id` 坐标系下。
    - `qx`, `qy`, `qz`, `qw`: 目标姿态四元数。默认单位四元数 `(0, 0, 0, 1)`。
    - `frame_id`: 目标 pose 的参考坐标系。不能为空；默认是 `panda_link0`。
    - `end_effector_link`: 要移动到目标 pose 的末端 link。默认是 `panda_hand`。
    - `planning_group`: MoveIt2 planning group。默认 `panda_arm`。
    - `require_readiness`: 默认 True。为 True 时，运行栈未就绪会拒绝移动。

    返回：
    - 成功时返回 `success=True`、执行状态、pose 目标、planning 摘要和最终机器人状态。
    - 如果缺少 `frame_id`、运行栈未就绪或 MoveIt2 规划失败，返回 `success=False`，不会执行。

    说明：
    - pose goal 必须有明确坐标系，不能凭空猜测“世界坐标”。
    - 这个工具内部完成规划并执行，不需要 `plan_id`。
    """
    frame_id = str(frame_id or "").strip()
    if not frame_id:
        return {"success": False, "error": "pose 目标必须提供 frame_id。"}
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error

    client = _client()
    pose = {
        "position": {"x": float(x), "y": float(y), "z": float(z)},
        "orientation": {"x": float(qx), "y": float(qy), "z": float(qz), "w": float(qw)},
    }
    result = client.plan_to_pose_goal(
        planning_group=planning_group,
        frame_id=frame_id,
        end_effector_link=end_effector_link,
        pose=pose,
    )
    return _execute_motion_result(
        client,
        result,
        target_type="pose_goal",
        target={"pose": pose, "end_effector_link": end_effector_link},
        planning_group=planning_group,
        frame_id=frame_id,
    )


@tool
def arm_open_gripper(
    width: float = DEFAULT_GRIPPER_OPENING_WIDTH,
    max_effort: float = 0.0,
    require_readiness: bool = True,
) -> dict:
    """打开 Panda 夹爪/夹头。

    参数：
    - `width`: 两指之间的目标总开口宽度，单位米。默认 0.07 m，接近当前 SRDF `hand/open`。
    - `max_effort`: GripperCommand 最大努力值。默认 0.0，表示不额外限制 controller。
    - `require_readiness`: 默认 True。为 True 时会先检查 ROS2/MoveIt2/MuJoCo 控制链路是否就绪。

    返回：
    - 成功时返回 `success=True`、执行摘要、目标开口宽度、controller action 输出和最终夹爪状态。
    - 失败时返回 `success=False` 和错误原因。

    说明：
    - 工具内部通过 `/panda_hand_controller/gripper_cmd` 发送夹爪 action。
    - Jazzy+ 会自动使用 `control_msgs/action/ParallelGripperCommand`；Humble 旧 controller
      会自动使用 `control_msgs/action/GripperCommand`。
    - 用户说“打开夹爪”“松开”“张开夹头”时，应优先调用这个工具。
    """
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error
    return _client().set_gripper_width(width, max_effort)


@tool
def arm_close_gripper(
    max_effort: float = 0.0,
    require_readiness: bool = True,
) -> dict:
    """闭合 Panda 夹爪/夹头。

    参数：
    - `max_effort`: GripperCommand 最大努力值。默认 0.0，表示不额外限制 controller。
    - `require_readiness`: 默认 True。为 True 时会先检查 ROS2/MoveIt2/MuJoCo 控制链路是否就绪。

    返回：
    - 成功时返回 `success=True`、执行摘要、目标开口宽度 0.0 和最终夹爪状态。
    - 失败时返回 `success=False` 和错误原因。

    说明：
    - 用户说“闭合夹爪”“夹住”“合上夹头”时，应优先调用这个工具。
    - 这个工具只控制夹爪宽度，不做物体检测、抓取成功判定或力控闭环。
    """
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error
    return _client().set_gripper_width(0.0, max_effort)


@tool
def arm_set_gripper_width(
    width: float,
    max_effort: float = 0.0,
    require_readiness: bool = True,
) -> dict:
    """设置 Panda 夹爪/夹头开口宽度。

    参数：
    - `width`: 两指之间的目标总开口宽度，单位米。Panda 第一阶段允许范围是 0 到 0.08 m。
    - `max_effort`: GripperCommand 最大努力值。默认 0.0，表示不额外限制 controller。
    - `require_readiness`: 默认 True。为 True 时会先检查 ROS2/MoveIt2/MuJoCo 控制链路是否就绪。

    返回：
    - 成功时返回 `success=True`、目标宽度、controller 命令位置和最终夹爪状态。
    - 失败时返回 `success=False` 和错误原因。

    说明：
    - 用户给出具体开口，例如“夹爪打开到 3 cm / 0.03 m”时，调用这个工具。
    - 这个工具不做抓取语义判断；只是把宽度命令发给 `panda_hand_controller` 的 action server。
    """
    readiness_error = _readiness_error(require_readiness)
    if readiness_error:
        return readiness_error
    return _client().set_gripper_width(width, max_effort)


@tool
def arm_execute_plan(
    steps: list[dict[str, Any]],
    stop_on_failure: bool = True,
    require_readiness: bool = True,
) -> dict:
    """顺序执行一段 ArmAgent 高层动作计划。

    参数：
    - `steps`: 有序步骤列表。每个步骤必须是字典，并包含 `action` 字段；可以包含可选
      `id` 和 `label`，方便终端展示。第一阶段支持的 action 只有：
      `move_named`、`move_joint`、`move_pose`、`open_gripper`、`close_gripper`、
      `set_gripper_width`、`get_joint_states`、`get_gripper_state`、`stop`。
    - `stop_on_failure`: 默认 True。任一步失败时，后续步骤会标记为 skipped，不再继续执行。
    - `require_readiness`: 默认 True。执行任何会移动机器人或夹爪的计划前，先检查
      MoveIt2、controller、joint states、robot_state_publisher 和 MuJoCo 控制链路是否就绪。

    返回：
    - `success`: 所有已要求执行的步骤都成功时为 True。
    - `executed_steps`: 实际执行的步骤数量。
    - `skipped_steps`: 因失败停止而跳过的步骤数量。
    - `steps`: 每一步的结构化结果，包含 step id、action、输入参数、状态、成功值和输出/错误。
    - `latest_state`: 最近一次动作或状态读取得到的 joint / gripper 状态。

    说明：
    - 这个工具解决“用户一句话里有多个连续动作”时 LLM 不稳定继续调工具的问题。
      LLM 只负责提交一次结构化计划；后续顺序执行由 Python 控制流完成。
    - 这个工具不是任意 LangChain tool dispatcher，也不会调用通用 ROS2 topic/service 工具。
      它只调 ArmAgent 白名单高层动作，避免绕过机械臂安全边界。
    - 单步动作仍优先使用原来的直接工具；只有用户明确要求连续多个动作时才使用本工具。
    """
    if not isinstance(steps, list) or not steps:
        return {
            "success": False,
            "status": "invalid_plan",
            "error": "steps 必须是非空列表。",
            "supported_actions": sorted(_PLAN_ACTIONS),
        }
    if len(steps) > MAX_ARM_PLAN_STEPS:
        return {
            "success": False,
            "status": "invalid_plan",
            "error": f"计划最多允许 {MAX_ARM_PLAN_STEPS} 步，当前收到 {len(steps)} 步。",
            "max_steps": MAX_ARM_PLAN_STEPS,
        }

    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            return {
                "success": False,
                "status": "invalid_plan",
                "error": f"第 {index} 步必须是字典。",
                "failed_step": index,
            }

        # 执行器先完整校验所有步骤，再执行第一步。这样如果第 3 步宽度单位写错，
        # 第 1 步机械臂也不会已经移动到半路。
        action = str(raw_step.get("action") or "").strip()
        step_id = str(raw_step.get("id") or index)
        label = str(raw_step.get("label") or "").strip()
        if not action:
            return {
                "success": False,
                "status": "invalid_plan",
                "error": f"第 {index} 步缺少 action。",
                "failed_step": step_id,
            }
        if raw_step.get("parallel") is True or action in _UNSUPPORTED_COMPLEX_ACTIONS:
            return {
                "success": False,
                "status": "unsupported_plan",
                "error": "第一阶段 plan executor 不支持并行动作、视觉感知、物体检测或抓取成功判定。",
                "failed_step": step_id,
                "action": action,
            }
        if action not in _PLAN_ACTIONS:
            return {
                "success": False,
                "status": "unsupported_action",
                "error": f"不支持的 plan action：{action}。",
                "failed_step": step_id,
                "action": action,
                "supported_actions": sorted(_PLAN_ACTIONS),
            }

        planning_group = str(raw_step.get("planning_group") or DEFAULT_PLANNING_GROUP).strip()
        if not planning_group:
            return {
                "success": False,
                "status": "invalid_plan",
                "error": f"第 {index} 步 planning_group 不能为空。",
                "failed_step": step_id,
                "action": action,
            }

        if action == "move_named":
            target_name = str(raw_step.get("target_name") or raw_step.get("target") or "").strip()
            if not target_name:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 move_named 必须提供 target_name。",
                    "failed_step": step_id,
                    "action": action,
                }
            normalized_steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "action": action,
                    "target_name": target_name,
                    "planning_group": planning_group,
                }
            )
            continue

        if action == "move_joint":
            joint_goal = raw_step.get("joint_goal")
            if not isinstance(joint_goal, dict) or not joint_goal:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 move_joint 必须提供非空 joint_goal 字典。",
                    "failed_step": step_id,
                    "action": action,
                }
            normalized_joint_goal: dict[str, float] = {}
            for joint_name, value in joint_goal.items():
                if not isinstance(joint_name, str) or not joint_name.strip():
                    return {
                        "success": False,
                        "status": "invalid_plan",
                        "error": f"第 {index} 步 joint_goal 里的关节名必须是非空字符串。",
                        "failed_step": step_id,
                        "action": action,
                    }
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    return {
                        "success": False,
                        "status": "invalid_plan",
                        "error": f"第 {index} 步关节 {joint_name} 的目标值必须是有限数字。",
                        "failed_step": step_id,
                        "action": action,
                    }
                normalized_joint_goal[joint_name.strip()] = float(value)
            normalized_steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "action": action,
                    "joint_goal": normalized_joint_goal,
                    "planning_group": planning_group,
                }
            )
            continue

        if action == "move_pose":
            frame_id_value = raw_step["frame_id"] if "frame_id" in raw_step else DEFAULT_BASE_FRAME
            link_value = raw_step["end_effector_link"] if "end_effector_link" in raw_step else DEFAULT_END_EFFECTOR_LINK
            frame_id = str(frame_id_value).strip()
            end_effector_link = str(link_value).strip()
            if not frame_id:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 move_pose 必须提供有效 frame_id。",
                    "failed_step": step_id,
                    "action": action,
                }
            if not end_effector_link:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 move_pose 必须提供有效 end_effector_link。",
                    "failed_step": step_id,
                    "action": action,
                }
            pose_numbers: dict[str, float] = {}
            for key, default in {
                "x": None,
                "y": None,
                "z": None,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "qw": 1.0,
            }.items():
                value = raw_step.get(key, default)
                if value is None:
                    return {
                        "success": False,
                        "status": "invalid_plan",
                        "error": f"第 {index} 步 move_pose 必须提供 {key}。",
                        "failed_step": step_id,
                        "action": action,
                    }
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    return {
                        "success": False,
                        "status": "invalid_plan",
                        "error": f"第 {index} 步 move_pose 的 {key} 必须是有限数字。",
                        "failed_step": step_id,
                        "action": action,
                    }
                pose_numbers[key] = float(value)
            normalized_steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "action": action,
                    "frame_id": frame_id,
                    "end_effector_link": end_effector_link,
                    "planning_group": planning_group,
                    **pose_numbers,
                }
            )
            continue

        if action in {"open_gripper", "set_gripper_width"}:
            width = raw_step.get("width", DEFAULT_GRIPPER_OPENING_WIDTH if action == "open_gripper" else None)
            if width is None:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 {action} 必须提供 width。",
                    "failed_step": step_id,
                    "action": action,
                }
            if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(float(width)):
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 {action} 的 width 必须是有限数字，单位米。",
                    "failed_step": step_id,
                    "action": action,
                }
            width = float(width)
            if width < 0.0 or width > MAX_GRIPPER_OPENING_WIDTH:
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 {action} 的 width 必须在 0 到 {MAX_GRIPPER_OPENING_WIDTH:.3f} 米之间。",
                    "failed_step": step_id,
                    "action": action,
                    "requested_width": width,
                }
            max_effort = raw_step.get("max_effort", 0.0)
            if isinstance(max_effort, bool) or not isinstance(max_effort, (int, float)) or not math.isfinite(float(max_effort)):
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 {action} 的 max_effort 必须是有限数字。",
                    "failed_step": step_id,
                    "action": action,
                }
            normalized_steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "action": action,
                    "width": width,
                    "max_effort": float(max_effort),
                }
            )
            continue

        if action == "close_gripper":
            max_effort = raw_step.get("max_effort", 0.0)
            if isinstance(max_effort, bool) or not isinstance(max_effort, (int, float)) or not math.isfinite(float(max_effort)):
                return {
                    "success": False,
                    "status": "invalid_plan",
                    "error": f"第 {index} 步 close_gripper 的 max_effort 必须是有限数字。",
                    "failed_step": step_id,
                    "action": action,
                }
            normalized_steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "action": action,
                    "max_effort": float(max_effort),
                }
            )
            continue

        normalized_steps.append({"id": step_id, "label": label, "action": action})

    client = _client()
    if require_readiness:
        readiness = client.check_readiness()
        if not readiness.get("success"):
            return {
                "success": False,
                "status": "not_ready",
                "error": "机械臂运行栈未就绪，已拒绝执行 plan。",
                "readiness": readiness,
                "steps": [
                    {
                        "id": step["id"],
                        "label": step["label"],
                        "action": step["action"],
                        "input": {
                            key: value
                            for key, value in step.items()
                            if key not in {"id", "label", "action"}
                        },
                        "status": "skipped",
                        "success": False,
                        "error": "readiness 检查失败，未执行。",
                    }
                    for step in normalized_steps
                ],
                "executed_steps": 0,
                "skipped_steps": len(normalized_steps),
            }

    step_results: list[dict[str, Any]] = []
    latest_joint_state: Optional[dict[str, Any]] = None
    latest_gripper_state: Optional[dict[str, Any]] = None
    failed = False
    failure_error = ""
    combined_named_segments = 0

    step_index = 0
    while step_index < len(normalized_steps):
        step = normalized_steps[step_index]
        step_input = {
            key: value
            for key, value in step.items()
            if key not in {"id", "label", "action"}
        }
        if failed and stop_on_failure:
            step_results.append(
                {
                    "id": step["id"],
                    "label": step["label"],
                    "action": step["action"],
                    "input": step_input,
                    "status": "skipped",
                    "success": False,
                    "error": "前序步骤失败，stop_on_failure=True，已跳过。",
                }
            )
            step_index += 1
            continue

        action = step["action"]
        if action == "move_named":
            move_run = []
            scan_index = step_index
            while scan_index < len(normalized_steps) and normalized_steps[scan_index]["action"] == "move_named":
                move_run.append(normalized_steps[scan_index])
                scan_index += 1

            if len(move_run) > 1:
                planned_results: list[dict[str, Any]] = []
                for move_step in move_run:
                    planned = client.plan_to_named_target(move_step["planning_group"], move_step["target_name"])
                    raw_plan = planned.get("raw_plan") if isinstance(planned, dict) else None
                    if not (
                        isinstance(planned, dict)
                        and planned.get("success")
                        and isinstance(raw_plan, dict)
                        and raw_plan.get("adapter") == "joint_trajectory_topic"
                        and isinstance(raw_plan.get("joint_goal"), dict)
                    ):
                        # 如果这一段里有 target 不能解析，退回单步执行路径。这样错误会落在
                        # 具体失败的步骤上，也保留原来的 stop_on_failure 行为。
                        planned_results = []
                        break
                    planned_results.append(planned)

                if len(planned_results) == len(move_run):
                    # mixed plan 里也可能出现连续 named target，例如：
                    # ready -> extended -> transport -> open_gripper -> home。
                    # 这里只合并当前连续段，让前三个 named target 一条 trajectory 连起来；
                    # 遇到夹爪或其它动作时再回到普通顺序执行。
                    first_raw_plan = dict(planned_results[0].get("raw_plan") or {})
                    combined_plan = {
                        "success": True,
                        "status": "planned",
                        "summary": "已合并连续 named targets 为一条多 waypoint 直接关节轨迹。",
                        "raw_plan": {
                            "adapter": "joint_trajectory_topic",
                            "joint_goals": [
                                dict(planned["raw_plan"]["joint_goal"])
                                for planned in planned_results
                            ],
                            "duration": float(first_raw_plan.get("duration", 1.5)),
                        },
                        "metadata": {
                            "adapter": "joint_trajectory_topic",
                            "source": "robot_description_semantic",
                            "moveit_py_bypassed": True,
                            "combined_waypoints": len(planned_results),
                        },
                    }
                    output = client.execute_plan(combined_plan)
                    success = bool(output.get("success")) if isinstance(output, dict) else False
                    if success:
                        combined_named_segments += 1
                        final_state = output.get("final_state") if isinstance(output, dict) else None
                        if isinstance(final_state, dict):
                            latest_joint_state = final_state
                        for waypoint_index, move_step in enumerate(move_run, start=1):
                            step_results.append(
                                {
                                    "id": move_step["id"],
                                    "label": move_step["label"],
                                    "action": move_step["action"],
                                    "input": {
                                        key: value
                                        for key, value in move_step.items()
                                        if key not in {"id", "label", "action"}
                                    },
                                    "status": "executed",
                                    "success": True,
                                    "output": {
                                        "success": True,
                                        "status": "executed",
                                        "summary": f"已作为连续 trajectory 的第 {waypoint_index} 个 waypoint 执行。",
                                        "target_type": "named_target",
                                        "target": {"target_name": move_step["target_name"]},
                                        "planning_group": move_step["planning_group"],
                                        "frame_id": DEFAULT_BASE_FRAME,
                                    },
                                }
                            )
                        step_index = scan_index
                        continue

                    failure_error = (
                        str(output.get("error") or "连续 trajectory 执行失败。")
                        if isinstance(output, dict)
                        else "连续 trajectory 执行失败。"
                    )
                    failed = True
                    first_move_step = move_run[0]
                    step_results.append(
                        {
                            "id": first_move_step["id"],
                            "label": first_move_step["label"],
                            "action": first_move_step["action"],
                            "input": {
                                key: value
                                for key, value in first_move_step.items()
                                if key not in {"id", "label", "action"}
                            },
                            "status": "failed",
                            "success": False,
                            "output": output,
                            "error": failure_error,
                        }
                    )
                    for skipped_step in move_run[1:]:
                        step_results.append(
                            {
                                "id": skipped_step["id"],
                                "label": skipped_step["label"],
                                "action": skipped_step["action"],
                                "input": {
                                    key: value
                                    for key, value in skipped_step.items()
                                    if key not in {"id", "label", "action"}
                                },
                                "status": "skipped",
                                "success": False,
                                "error": "连续 trajectory 执行失败，已跳过。",
                            }
                        )
                    step_index = scan_index
                    continue

            plan_result = client.plan_to_named_target(step["planning_group"], step["target_name"])
            output = _execute_motion_result(
                client,
                plan_result,
                target_type="named_target",
                target={"target_name": step["target_name"]},
                planning_group=step["planning_group"],
                frame_id=DEFAULT_BASE_FRAME,
            )
        elif action == "move_joint":
            validation = client.validate_joint_goal(step["planning_group"], step["joint_goal"])
            if validation.get("success"):
                plan_result = client.plan_to_joint_goal(step["planning_group"], step["joint_goal"])
                output = _execute_motion_result(
                    client,
                    plan_result,
                    target_type="joint_goal",
                    target={"joint_goal": dict(step["joint_goal"])},
                    planning_group=step["planning_group"],
                    frame_id=DEFAULT_BASE_FRAME,
                )
            else:
                output = validation
        elif action == "move_pose":
            pose = {
                "position": {"x": step["x"], "y": step["y"], "z": step["z"]},
                "orientation": {"x": step["qx"], "y": step["qy"], "z": step["qz"], "w": step["qw"]},
            }
            plan_result = client.plan_to_pose_goal(
                planning_group=step["planning_group"],
                frame_id=step["frame_id"],
                end_effector_link=step["end_effector_link"],
                pose=pose,
            )
            output = _execute_motion_result(
                client,
                plan_result,
                target_type="pose_goal",
                target={"pose": pose, "end_effector_link": step["end_effector_link"]},
                planning_group=step["planning_group"],
                frame_id=step["frame_id"],
            )
        elif action == "open_gripper":
            output = client.set_gripper_width(step["width"], step["max_effort"])
        elif action == "close_gripper":
            output = client.set_gripper_width(0.0, step["max_effort"])
        elif action == "set_gripper_width":
            output = client.set_gripper_width(step["width"], step["max_effort"])
        elif action == "get_joint_states":
            output = client.get_joint_states()
        elif action == "get_gripper_state":
            output = client.get_gripper_state()
        else:
            output = client.stop_motion()

        success = bool(output.get("success")) if isinstance(output, dict) else False
        status = str(output.get("status") or ("executed" if success else "failed")) if isinstance(output, dict) else "failed"
        step_result = {
            "id": step["id"],
            "label": step["label"],
            "action": action,
            "input": step_input,
            "status": status,
            "success": success,
            "output": output,
        }
        if not success:
            failure_error = str(output.get("error") or "步骤执行失败。") if isinstance(output, dict) else "步骤执行失败。"
            step_result["error"] = failure_error
            failed = True
        step_results.append(step_result)

        if isinstance(output, dict):
            final_state = output.get("final_state")
            if action == "get_joint_states" and output.get("success"):
                latest_joint_state = output
            elif isinstance(final_state, dict) and "joint_states" in final_state:
                latest_joint_state = final_state

            if action == "get_gripper_state" and output.get("success"):
                latest_gripper_state = output
            elif isinstance(final_state, dict) and (
                "estimated_width" in final_state or "finger_joint_positions" in final_state
            ):
                latest_gripper_state = final_state
        step_index += 1

    executed_steps = sum(1 for result in step_results if result["status"] != "skipped")
    skipped_steps = sum(1 for result in step_results if result["status"] == "skipped")
    all_executed_successfully = not failed and all(result["success"] for result in step_results)
    return {
        "success": all_executed_successfully,
        "status": "executed" if all_executed_successfully else "failed",
        "summary": (
            (
                (
                    f"已顺序执行 {executed_steps} 个 ArmAgent plan 步骤，"
                    f"其中 {combined_named_segments} 段连续 named target 已合并为 trajectory。"
                )
                if combined_named_segments
                else f"已顺序执行 {executed_steps} 个 ArmAgent plan 步骤。"
            )
            if all_executed_successfully
            else f"ArmAgent plan 在第 {executed_steps} 个执行步骤后停止：{failure_error}"
        ),
        "executed_steps": executed_steps,
        "skipped_steps": skipped_steps,
        "total_steps": len(step_results),
        "stop_on_failure": stop_on_failure,
        "steps": step_results,
        "latest_state": {
            "joint": latest_joint_state,
            "gripper": latest_gripper_state,
        },
    }


@tool
def arm_stop() -> dict:
    """停止或中断当前机械臂运动。

    典型使用场景：
    - 用户说“停止”“急停”“别动了”“halt”等。
    - 执行过程中出现异常、目标不安全或控制器状态不可信。

    返回：
    - `success`: 是否成功发出停止/检查请求。
    - `summary`: 停止结果说明。
    - 可能包含当前 controllers 状态，方便用户判断是否需要在外部控制器或仿真端进一步处理。

    注意：
    - 第一阶段默认 adapter 只能通过 ROS2/control 层报告和请求停止；真实硬件急停必须走硬件安全链路。
    """
    return _client().stop_motion()


def _client() -> Any:
    """创建一份运行时 client。

    这里保持为一个小边界，是因为所有 tool 都要通过同一种方式拿 client；测试会替换
    `_CLIENT_FACTORY`。它不是一次性 helper，而是工具层和 adapter 层之间的固定边界。
    """
    return _CLIENT_FACTORY()


def _readiness_error(require_readiness: bool) -> Optional[dict[str, Any]]:
    """在规划前统一执行 readiness gate。

    移动工具都要遵守同一个安全规则：默认先检查外部 ROS2/MoveIt2/MuJoCo 控制链路是否就绪。
    如果未就绪，返回结构化错误并阻止后续规划。
    """
    if not require_readiness:
        return None
    readiness = _client().check_readiness()
    if readiness.get("success"):
        return None
    return {
        "success": False,
        "error": "机械臂运行栈未就绪，已拒绝规划。",
        "readiness": readiness,
    }


def _execute_motion_result(
    client: Any,
    result: dict[str, Any],
    *,
    target_type: str,
    target: dict[str, Any],
    planning_group: str,
    frame_id: str,
) -> dict[str, Any]:
    """把本轮 MoveIt2 规划结果立即执行，并补齐返回给模型的目标元数据。

    用户现在不需要“先拿 plan_id，再执行”的两步流程。这里仍然保留一个小的内部边界，是因为
    三个 move tool 都要遵守同样的规则：
    - 规划失败就直接返回失败，不调用执行。
    - 规划成功就立刻交给同一个 MoveIt runtime client 执行。
    - 返回值里保留 target、planning_group、frame_id，方便用户知道刚才执行的是什么。
    """
    if not result.get("success"):
        return result

    execution = client.execute_plan(result)
    return {
        **execution,
        "target_type": target_type,
        "target": target,
        "planning_group": planning_group,
        "frame_id": frame_id,
    }
