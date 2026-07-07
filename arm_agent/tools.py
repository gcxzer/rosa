"""ArmAgent 专用 LangChain tools。

"""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.tools import tool

from .moveit_client import (
    DEFAULT_BASE_FRAME,
    DEFAULT_END_EFFECTOR_LINK,
    DEFAULT_PLANNING_GROUP,
    MoveItRuntimeClient,
)


_CLIENT_FACTORY: Callable[[], Any] = MoveItRuntimeClient


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
    - 这个工具对用户是“一步执行”：内部先调用 MoveIt2 规划 trajectory，再立即执行。
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
    - 工具会先做最小数值校验，再交给 MoveIt2 做真正的关节限制、碰撞和可达性检查。
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
