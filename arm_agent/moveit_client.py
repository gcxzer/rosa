"""ArmAgent 的 MoveIt2 运行时适配边界。

这个模块刻意不在导入阶段依赖 `rclpy`、MoveIt2 或 MuJoCo。普通开发机运行单元测试时，
只需要能导入这些 Python 文件；真正和 ROS2/MoveIt2/MuJoCo 通信的逻辑集中在运行时 client
里。后续如果要从 `moveit_py` 切换到 action/service 级客户端，也只需要替换这里。
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import time
from typing import Any, Optional
from xml.etree import ElementTree


DEFAULT_PLANNING_GROUP = "panda_arm"
DEFAULT_BASE_FRAME = "panda_link0"
DEFAULT_END_EFFECTOR_LINK = "panda_hand"


class MoveItRuntimeClient:
    """默认 MoveIt2 运行时客户端。

    这里先提供 ROS2 graph/readiness 和 joint_states 的真实 CLI 读取能力；规划/执行方法保留稳定接口，
    在没有 MoveIt2 Python adapter 时返回清晰错误。工具层和测试都依赖这个接口，而不是依赖某个
    具体 ROS2 Python API。
    """

    def __init__(
        self,
        *,
        planning_group: str = DEFAULT_PLANNING_GROUP,
        base_frame: str = DEFAULT_BASE_FRAME,
        end_effector_link: str = DEFAULT_END_EFFECTOR_LINK,
        timeout: float = 3.0,
    ) -> None:
        self.planning_group = planning_group
        self.base_frame = base_frame
        self.end_effector_link = end_effector_link
        self.timeout = timeout
        self._moveit_py = None

    def check_readiness(self) -> dict[str, Any]:
        nodes = self._run_ros2(["ros2", "node", "list"])
        topics = self._run_ros2(["ros2", "topic", "list"])
        services = self._run_ros2(["ros2", "service", "list"])
        controllers = self._list_controllers()

        detected = {
            "nodes": nodes.get("lines", []),
            "topics": topics.get("lines", []),
            "services": services.get("lines", []),
            "controllers": controllers.get("lines", []),
        }
        if controllers.get("source"):
            detected["controller_query_source"] = controllers["source"]
        if controllers.get("error"):
            detected["controller_query_error"] = controllers["error"]

        missing: list[str] = []
        if not nodes.get("success"):
            missing.append("ros2 node list")
        if not topics.get("success"):
            missing.append("ros2 topic list")
        if not services.get("success"):
            missing.append("ros2 service list")

        node_text = "\n".join(detected["nodes"])
        topic_text = "\n".join(detected["topics"])
        service_text = "\n".join(detected["services"])
        controller_text = "\n".join(detected["controllers"])
        controller_graph_visible = (
            "panda_arm_controller" in node_text
            and "joint_state_broadcaster" in node_text
            and "/controller_manager/list_controllers" in service_text
        )
        if not controllers.get("success") and not controller_graph_visible:
            missing.append("ros2 control list_controllers")
        if "move_group" not in node_text and "move_group" not in service_text:
            missing.append("MoveIt2 move_group")
        if "/joint_states" not in topic_text:
            missing.append("/joint_states topic")
        if "robot_state_publisher" not in node_text:
            missing.append("robot_state_publisher")
        if "controller_manager" not in node_text and "controller_manager" not in service_text:
            missing.append("controller_manager")
        if "mujoco" not in node_text.lower() and "mujoco" not in controller_text.lower():
            missing.append("mujoco_ros2_control")

        return {
            "success": not missing,
            "missing": missing,
            "detected": detected,
            "summary": "Manipulator stack is ready." if not missing else "Manipulator stack is incomplete.",
        }

    def get_joint_states(self) -> dict[str, Any]:
        result = self._run_ros2(
            [
                "ros2",
                "topic",
                "echo",
                "/joint_states",
                "--once",
                "--spin-time",
                str(self.timeout),
            ],
            timeout=self.timeout + 3.0,
        )
        if not result.get("success"):
            return result

        output = result.get("output", "")
        # `ros2 topic echo /joint_states --once` 输出的是 YAML 风格文本。
        names_match = re.search(r"name:\s*(\[[^\]]*\])", output, flags=re.DOTALL)
        positions_match = re.search(r"position:\s*(\[[^\]]*\])", output, flags=re.DOTALL)
        if not names_match or not positions_match:
            return {"success": False, "error": "没有从 /joint_states 输出中解析到 name 和 position。", "raw": output}

        try:
            names = ast.literal_eval(names_match.group(1))
            positions = ast.literal_eval(positions_match.group(1))
        except (SyntaxError, ValueError) as error:
            return {"success": False, "error": f"解析 /joint_states 失败：{error}", "raw": output}

        if not isinstance(names, list) or not isinstance(positions, list):
            return {"success": False, "error": "/joint_states name 和 position 必须是列表。", "raw": output}

        pairs = {
            str(name): float(position)
            for name, position in zip(names, positions)
            if isinstance(position, (int, float))
        }
        return {"success": True, "joint_states": pairs, "names": list(pairs.keys()), "positions": list(pairs.values())}

    def get_planning_groups(self) -> dict[str, Any]:
        semantic = self._read_robot_description_semantic()
        if not semantic.get("success"):
            return semantic

        try:
            root = ElementTree.fromstring(semantic["value"])
        except ElementTree.ParseError as error:
            return {"success": False, "error": f"解析 robot_description_semantic 失败：{error}"}

        groups = [
            str(group.get("name") or "")
            for group in root.findall("group")
            if str(group.get("name") or "")
        ]
        return {"success": True, "planning_groups": groups, "source": "robot_description_semantic"}

    def get_named_targets(self, planning_group: str) -> dict[str, Any]:
        semantic = self._read_robot_description_semantic()
        if not semantic.get("success"):
            return semantic

        try:
            root = ElementTree.fromstring(semantic["value"])
        except ElementTree.ParseError as error:
            return {"success": False, "error": f"解析 robot_description_semantic 失败：{error}"}

        targets = [
            str(target.get("name") or "")
            for target in root.findall("group_state")
            if str(target.get("group") or "") == planning_group and str(target.get("name") or "")
        ]
        return {
            "success": True,
            "planning_group": planning_group,
            "named_targets": targets,
            "source": "robot_description_semantic",
        }

    def get_end_effector_link(self, planning_group: str) -> dict[str, Any]:
        semantic = self._read_robot_description_semantic()
        if semantic.get("success"):
            try:
                root = ElementTree.fromstring(semantic["value"])
            except ElementTree.ParseError as error:
                return {"success": False, "error": f"解析 robot_description_semantic 失败：{error}"}

            for end_effector in root.findall("end_effector"):
                if str(end_effector.get("group") or "") == planning_group:
                    parent_link = str(end_effector.get("parent_link") or "").strip()
                    return {
                        "success": bool(parent_link),
                        "planning_group": planning_group,
                        "end_effector_link": parent_link,
                        "source": "robot_description_semantic",
                    }

        # Panda MVP 的默认 link 是 prompt 和文档约定的一部分。SRDF 不可读时仍返回这个默认值，
        # 但显式标记 source，避免用户误以为它来自实时 MoveIt2 配置。
        return {
            "success": True,
            "planning_group": planning_group,
            "end_effector_link": self.end_effector_link,
            "source": "ArmAgent default",
            "warning": semantic.get("error", "没有从 MoveIt2 SRDF 读取到 end effector link。"),
        }

    def get_end_effector_pose(self, frame_id: str, end_effector_link: str) -> dict[str, Any]:
        result = self._run_ros2(
            ["ros2", "run", "tf2_ros", "tf2_echo", frame_id, end_effector_link, "--once"],
            timeout=self.timeout + 2.0,
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": f"读取 {end_effector_link} 在 {frame_id} 下的 TF 失败：{result.get('error')}",
            }

        output = result.get("output", "")
        # `tf2_echo` 的输出格式比较固定：一段 Translation 和一段 Quaternion。解析逻辑只在这个
        # 工具入口使用一次，所以直接放在这里，避免读者为了两行正则来回跳转。
        translation_match = re.search(
            r"Translation:\s*\[\s*([-+\deE.]+),\s*([-+\deE.]+),\s*([-+\deE.]+)\s*\]",
            output,
        )
        rotation_match = re.search(
            r"Rotation:\s*in Quaternion\s*\[\s*([-+\deE.]+),\s*([-+\deE.]+),\s*([-+\deE.]+),\s*([-+\deE.]+)\s*\]",
            output,
        )
        if not translation_match or not rotation_match:
            return {
                "success": False,
                "error": "没有从 tf2_echo 输出中解析到 Translation 和 Quaternion。",
                "raw": output,
            }

        return {
            "success": True,
            "frame_id": frame_id,
            "end_effector_link": end_effector_link,
            "pose": {
                "position": {
                    "x": float(translation_match.group(1)),
                    "y": float(translation_match.group(2)),
                    "z": float(translation_match.group(3)),
                },
                "orientation": {
                    "x": float(rotation_match.group(1)),
                    "y": float(rotation_match.group(2)),
                    "z": float(rotation_match.group(3)),
                    "w": float(rotation_match.group(4)),
                },
            },
        }

    def validate_joint_goal(self, planning_group: str, joint_goal: dict[str, float]) -> dict[str, Any]:
        del planning_group
        if not joint_goal:
            return {"success": False, "error": "joint_goal 不能为空。"}
        invalid = [name for name, value in joint_goal.items() if not isinstance(value, (int, float))]
        if invalid:
            return {"success": False, "error": f"这些关节目标不是数值：{invalid}"}
        return {"success": True, "checked": "numeric"}

    def plan_to_named_target(self, planning_group: str, target_name: str) -> dict[str, Any]:
        if planning_group != DEFAULT_PLANNING_GROUP:
            return {
                "success": False,
                "error": f"当前直接执行 adapter 只支持 `{DEFAULT_PLANNING_GROUP}`，收到 `{planning_group}`。",
            }

        semantic = self._read_robot_description_semantic()
        if not semantic.get("success"):
            return semantic

        try:
            root = ElementTree.fromstring(semantic["value"])
        except ElementTree.ParseError as error:
            return {"success": False, "error": f"解析 robot_description_semantic 失败：{error}"}

        joint_goal: dict[str, float] = {}
        for group_state in root.findall("group_state"):
            if (
                str(group_state.get("group") or "") == planning_group
                and str(group_state.get("name") or "") == target_name
            ):
                # SRDF 的 named target 本质是一组关节角。当前 MuJoCo + MoveItPy 组合里，
                # MoveItPy 一旦打开 `use_sim_time=True` 会触发 upstream 的
                # `qos_overrides./clock.subscription.durability` abort；不开仿真时间又会把
                # `/joint_states` 判断成过期。因此第一阶段对 named target 直接把 SRDF 关节角
                # 发送给 ros2_control 的 JointTrajectoryController，绕开 MoveItPy 的执行管理器。
                for joint in group_state.findall("joint"):
                    name = str(joint.get("name") or "").strip()
                    if name:
                        joint_goal[name] = float(str(joint.get("value") or "0"))
                break

        if not joint_goal:
            return {
                "success": False,
                "error": f"没有在 `{planning_group}` 里找到 named target `{target_name}`。",
            }

        return {
            "success": True,
            "status": "planned",
            "summary": f"已解析 named target `{target_name}` 为直接关节轨迹。",
            "raw_plan": {
                "adapter": "joint_trajectory_topic",
                "joint_goal": joint_goal,
                "duration": 3.0,
            },
            "metadata": {
                "adapter": "joint_trajectory_topic",
                "source": "robot_description_semantic",
                "moveit_py_bypassed": True,
            },
        }

    def plan_to_joint_goal(self, planning_group: str, joint_goal: dict[str, float]) -> dict[str, Any]:
        if planning_group != DEFAULT_PLANNING_GROUP:
            return {
                "success": False,
                "error": f"当前直接执行 adapter 只支持 `{DEFAULT_PLANNING_GROUP}`，收到 `{planning_group}`。",
            }

        required_joints = [f"panda_joint{index}" for index in range(1, 8)]
        current_state = self.get_joint_states()
        if not current_state.get("success"):
            return {
                "success": False,
                "error": "无法读取当前 joint states，不能补齐 joint goal。",
                "joint_states": current_state,
            }

        current_joints = dict(current_state.get("joint_states") or {})
        missing_joints = [
            joint_name
            for joint_name in required_joints
            if joint_name not in joint_goal and joint_name not in current_joints
        ]
        if missing_joints:
            return {
                "success": False,
                "error": f"joint goal 缺少这些关节，且当前状态里也没有：{missing_joints}",
            }

        # JointTrajectoryController 默认通常不接受 partial joint goal。这里允许用户只给一两个
        # 关节，是因为工具会用当前 `/joint_states` 补齐其余 Panda arm 关节，最后发出的仍是
        # 完整 7 轴 trajectory。
        full_joint_goal = {
            joint_name: float(joint_goal[joint_name] if joint_name in joint_goal else current_joints[joint_name])
            for joint_name in required_joints
        }
        return {
            "success": True,
            "status": "planned",
            "summary": "已生成直接关节轨迹。",
            "raw_plan": {
                "adapter": "joint_trajectory_topic",
                "joint_goal": full_joint_goal,
                "duration": 3.0,
            },
            "metadata": {
                "adapter": "joint_trajectory_topic",
                "source": "direct_joint_goal",
                "moveit_py_bypassed": True,
            },
        }

    def plan_to_pose_goal(
        self,
        *,
        planning_group: str,
        frame_id: str,
        end_effector_link: str,
        pose: dict[str, float],
    ) -> dict[str, Any]:
        runtime = self._load_moveit_py()
        if not runtime.get("success"):
            return runtime

        try:
            pose_msg = runtime["pose_stamped_class"]()
            pose_msg.header.frame_id = frame_id
            pose_msg.pose.position.x = pose["position"]["x"]
            pose_msg.pose.position.y = pose["position"]["y"]
            pose_msg.pose.position.z = pose["position"]["z"]
            pose_msg.pose.orientation.x = pose["orientation"]["x"]
            pose_msg.pose.orientation.y = pose["orientation"]["y"]
            pose_msg.pose.orientation.z = pose["orientation"]["z"]
            pose_msg.pose.orientation.w = pose["orientation"]["w"]

            planning_component = runtime["moveit_py"].get_planning_component(planning_group)
            planning_component.set_start_state_to_current_state()
            planning_component.set_goal_state(
                pose_stamped_msg=pose_msg,
                pose_link=end_effector_link,
            )
            plan_result = planning_component.plan()
        except Exception as error:
            return {"success": False, "error": f"MoveIt2 pose goal 规划失败：{error}"}

        return _normalize_plan_result(
            plan_result,
            summary=f"已规划到 {frame_id} 下的末端 pose。",
            adapter="moveit_py",
        )

    def execute_plan(self, plan_result: dict[str, Any]) -> dict[str, Any]:
        """执行当前工具调用刚刚得到的 MoveIt2 规划结果。

        ArmAgent 现在不再把 plan 暴露成用户可见的 `plan_id`。公开 tool 会先调用
        `plan_to_*()` 得到本轮 trajectory，再立刻把这个 result 传进来执行。这样用户看到的是
        “移动到目标”这一件事。

        注意：Jazzy 当前的 MoveItPy + `use_sim_time=True` 会在 C++ 层 abort 进程；所以
        named target / joint goal 会生成 `joint_trajectory_topic` direct plan，直接交给
        ros2_control 的 `panda_arm_controller`。只有非 direct plan 才会进入 MoveItPy execute。
        """
        if not plan_result.get("success"):
            return {"success": False, "error": "规划未成功，不能执行。", "planning": plan_result}
        raw_plan = plan_result.get("raw_plan")
        if raw_plan is None:
            return {"success": False, "error": "规划结果中没有 raw_plan，不能执行。", "planning": plan_result}

        try:
            if isinstance(raw_plan, dict) and raw_plan.get("adapter") == "joint_trajectory_topic":
                joint_goal = dict(raw_plan.get("joint_goal") or {})
                joint_names = [f"panda_joint{index}" for index in range(1, 8)]
                missing_joints = [joint_name for joint_name in joint_names if joint_name not in joint_goal]
                if missing_joints:
                    return {
                        "success": False,
                        "error": f"直接关节轨迹缺少这些 Panda arm 关节：{missing_joints}",
                        "planning": plan_result,
                    }

                duration = float(raw_plan.get("duration", 3.0))
                sec = max(1, int(duration))
                nanosec = max(0, int((duration - sec) * 1_000_000_000))
                payload = {
                    "joint_names": joint_names,
                    "points": [
                        {
                            "positions": [float(joint_goal[joint_name]) for joint_name in joint_names],
                            "time_from_start": {"sec": sec, "nanosec": nanosec},
                        }
                    ],
                }
                publish_result = self._run_ros2(
                    [
                        "ros2",
                        "topic",
                        "pub",
                        "--once",
                        "/panda_arm_controller/joint_trajectory",
                        "trajectory_msgs/msg/JointTrajectory",
                        json.dumps(payload),
                    ],
                    timeout=self.timeout + 4.0,
                )
                if not publish_result.get("success"):
                    return {
                        "success": False,
                        "error": f"发布 joint trajectory 失败：{publish_result.get('error')}",
                        "planning": plan_result,
                    }

                # `ros2 topic pub --once` 只保证消息发出去，不代表控制器已经走完轨迹。
                # 这里等待 trajectory 的 `time_from_start`，再读取最终 joint state，避免用户看到
                # “已执行”但状态还是起点。
                time.sleep(duration + 0.2)
            else:
                runtime = self._load_moveit_py()
                if not runtime.get("success"):
                    return runtime
                trajectory = getattr(raw_plan, "trajectory", raw_plan)
                runtime["moveit_py"].execute(trajectory, controllers=[])
        except Exception as error:
            return {"success": False, "error": f"MoveIt2 执行 plan 失败：{error}"}

        return {
            "success": True,
            "status": "executed",
            "summary": f"已完成规划并执行：{plan_result.get('summary', 'MoveIt2 trajectory')}",
            "planning": {
                "status": plan_result.get("status"),
                "summary": plan_result.get("summary"),
                "metadata": dict(plan_result.get("metadata") or {}),
            },
            "final_state": self.get_joint_states(),
        }

    def stop_motion(self) -> dict[str, Any]:
        controllers = self._list_controllers()
        if not controllers.get("success"):
            return controllers
        return {
            "success": True,
            "summary": "已请求停止检查；如需硬停止，请在控制器侧执行 halt/stop 或急停流程。",
            "controllers": controllers.get("lines", []),
        }

    def _list_controllers(self) -> dict[str, Any]:
        """尽量稳定地读取 controller_manager 当前控制器列表。

        不同 ROS2 / ros2_control 版本的 `ros2 control list_controllers` 默认 controller manager
        解析不完全一致。你的 Jazzy 环境里 graph 和 service 都在线，但默认 CLI 没拿到列表，
        会导致 readiness 被误判为失败。所以这里按顺序尝试：
        1. 显式指定 `/controller_manager` 的 ros2 control CLI。
        2. 兼容旧写法的默认 ros2 control CLI。
        3. 直接调用 `/controller_manager/list_controllers` service。
        """
        attempts = [
            (
                "ros2_control_cli_explicit",
                ["ros2", "control", "list_controllers", "-c", "/controller_manager"],
            ),
            (
                "ros2_control_cli_default",
                ["ros2", "control", "list_controllers"],
            ),
            (
                "controller_manager_service",
                [
                    "ros2",
                    "service",
                    "call",
                    "/controller_manager/list_controllers",
                    "controller_manager_msgs/srv/ListControllers",
                    "{}",
                ],
            ),
        ]

        errors: list[str] = []
        for source, command in attempts:
            result = self._run_ros2(command, timeout=self.timeout + 2.0)
            if result.get("success"):
                lines = result.get("lines", [])
                # service call 的输出会带 requester/response 包装，但仍包含 controller name/state；
                # 保留原始行给用户和测试看，避免为了显示好看做脆弱的 YAML 解析。
                return {**result, "source": source, "lines": lines}
            errors.append(f"{source}: {result.get('error', 'unknown error')}")

        return {
            "success": False,
            "source": "controller_query_failed",
            "lines": [],
            "error": "；".join(errors),
        }

    def _run_ros2(self, args: list[str], timeout: Optional[float] = None) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except FileNotFoundError:
            return {"success": False, "error": "找不到 ros2 命令。请先 source ROS2 环境。"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"命令超时：{' '.join(args)}"}

        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return {"success": False, "error": output or f"命令失败：{completed.returncode}"}
        return {
            "success": True,
            "output": output,
            "lines": [line.strip() for line in output.splitlines() if line.strip()],
        }

    def _read_robot_description_semantic(self) -> dict[str, Any]:
        """读取 MoveIt2 的 SRDF 语义配置文本。

        MoveIt2 启动后，`move_group` 节点通常会带着一个
        `robot_description_semantic` 参数。

        ArmAgent 的 `arm_get_planning_groups()`、`arm_get_named_targets()`、
        `arm_get_end_effector_link()` 都需要这段 SRDF，所以这里单独保留为复用方法。
        """
        # 通过 ROS2 CLI 读取 `/move_group` 节点上的参数。这里不用直接 import MoveIt，
        # 是为了让“只查看配置”的工具在没有 Python MoveIt adapter 时也能工作。
        result = self._run_ros2(["ros2", "param", "get", "/move_group", "robot_description_semantic"])
        if not result.get("success"):
            return {
                "success": False,
                "error": f"无法读取 /move_group robot_description_semantic：{result.get('error')}",
            }

        value = str(result.get("output") or "")
        # `ros2 param get` 的输出不是纯 XML，通常会在真正的 SRDF 前面加一段提示文字：
        #   String value is: <?xml version="1.0" ...>
        # 不同 ROS2 版本可能写成 `String value is:` 或 `value is:`，所以这里兼容这两种前缀，
        # 剥掉前缀以后，后续 `ElementTree.fromstring(...)` 才能直接解析 XML。
        for prefix in ("String value is:", "value is:"):
            if prefix in value:
                value = value.split(prefix, 1)[1].strip()
                break
        if not value:
            return {"success": False, "error": "robot_description_semantic 为空。"}
        return {"success": True, "value": value}

    def _load_moveit_py(self) -> dict[str, Any]:
        if self._moveit_py is not None:
            return self._moveit_py

        try:
            rclpy = importlib.import_module("rclpy")
            planning_module = importlib.import_module("moveit.planning")
            robot_state_module = importlib.import_module("moveit.core.robot_state")
            geometry_msgs = importlib.import_module("geometry_msgs.msg")
            configs_utils = importlib.import_module("moveit_configs_utils")
            ament_packages = importlib.import_module("ament_index_python.packages")
        except ImportError as error:
            # 这里是唯一需要报告 MoveIt2 Python adapter 缺失的地方，直接返回完整错误，
            # 不再额外包一层只调用一次的私有 helper。
            return {
                "success": False,
                "error": (
                    f"加载 MoveIt2 Python adapter 失败：{error}。需要 MoveIt2 Python 运行时适配器。"
                    "请确认当前 ROS2 shell 已安装并 source `moveit_py`、`moveit_configs_utils`、"
                    "MoveIt2、ros2_control 和 mujoco_ros2_control。"
                ),
            }

        try:
            if not rclpy.ok():
                # 只在运行时初始化 rclpy，保证普通 import/test 不需要 ROS2 环境。
                rclpy.init()

            package_name = "moveit_resources_panda_moveit_config"
            package_share = ament_packages.get_package_share_directory(package_name)
            mujoco_model = os.path.join(package_share, "mujoco", "franka_emika_panda", "scene_moveit.xml")
            moveit_py_config = os.path.join(package_share, "config", "arm_moveit_py.yaml")

            # MoveItPy 会在当前 Python 进程里创建一个新的 rclcpp node。它不会自动继承
            # 外面 `/move_group` 节点已经加载好的 planning pipeline 参数，所以必须像官方
            # MoveIt Python API 示例那样，把 URDF、SRDF、kinematics、joint limits、OMPL
            # pipeline 和 `moveit_cpp` 配置打包成 config_dict 传进去。否则就会出现用户日志里
            # 的 fatal：`Failed to load planning pipelines from parameter server`。
            #
            # 这里使用 colcon 安装后的 package share 路径，而不是仓库相对路径。这样只要用户
            # 先 `source scripts/load_arm_ros2_resources.sh`，无论从哪个目录启动 main.py，
            # MoveItPy 都能找到同一份 Panda 描述文件和 MuJoCo MJCF。
            moveit_config = (
                configs_utils.MoveItConfigsBuilder(
                    robot_name="moveit_resources_panda",
                    package_name=package_name,
                )
                .robot_description(
                    file_path="config/panda.urdf.xacro",
                    mappings={
                        "ros2_control_hardware_type": "mujoco",
                        "mujoco_model": mujoco_model,
                        "headless": "false",
                        "sim_speed_factor": "1.0",
                    },
                )
                .robot_description_semantic(file_path="config/panda.srdf")
                .trajectory_execution(file_path="config/gripper_moveit_controllers.yaml")
                .planning_pipelines(pipelines=["ompl"])
                .moveit_cpp(file_path=moveit_py_config)
                .to_moveit_configs()
            )

            moveit_config_dict = moveit_config.to_dict()
            # 不在这里设置 `use_sim_time=True`。MoveItPy 当前版本存在 upstream 问题：
            # 仿真时间会让 TrajectoryExecutionManager 拒绝 `/clock` 的 QoS override 参数，
            # 然后 C++ 侧直接 abort Python 进程。named target / joint goal 已经在上层走
            # ros2_control direct trajectory fallback；pose goal 如果需要 MoveItPy，宁可返回
            # 一个可捕获的初始化/规划失败，也不能让整个 agent 进程崩掉。
            moveit_py = planning_module.MoveItPy(
                node_name="rosa_arm_agent",
                config_dict=moveit_config_dict,
            )
        except Exception as error:
            return {"success": False, "error": f"初始化 MoveItPy 失败：{error}"}

        self._moveit_py = {
            "success": True,
            "moveit_py": moveit_py,
            "robot_state_class": robot_state_module.RobotState,
            "pose_stamped_class": geometry_msgs.PoseStamped,
        }
        return self._moveit_py

def _normalize_plan_result(plan_result: Any, *, summary: str, adapter: str) -> dict[str, Any]:
    if not plan_result:
        return {"success": False, "status": "planning_failed", "error": "MoveIt2 没有返回可执行轨迹。"}
    return {
        "success": True,
        "status": "planned",
        "summary": summary,
        "raw_plan": plan_result,
        "metadata": {"adapter": adapter},
    }
