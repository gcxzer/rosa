"""ArmAgent 的 MoveIt2 运行时适配边界。

这个模块刻意不在导入阶段依赖 `rclpy`、MoveIt2 或 MuJoCo。普通开发机运行单元测试时，
只需要能导入这些 Python 文件；真正和 ROS2/MoveIt2/MuJoCo 通信的逻辑集中在运行时 client
里。后续如果要从 `moveit_py` 切换到 action/service 级客户端，也只需要替换这里。
"""

from __future__ import annotations

import glob
import importlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional
from xml.etree import ElementTree

import yaml


DEFAULT_PLANNING_GROUP = "panda_arm"
DEFAULT_BASE_FRAME = "panda_link0"
DEFAULT_END_EFFECTOR_LINK = "panda_hand"
DEFAULT_GRIPPER_OPENING_WIDTH = 0.07
MAX_GRIPPER_OPENING_WIDTH = 0.08
DEFAULT_DIRECT_TRAJECTORY_DURATION = 1.5
DIRECT_TRAJECTORY_MIN_SEGMENT_DURATION = 0.35
DIRECT_TRAJECTORY_MAX_JOINT_SPEED = 2.0
DIRECT_TRAJECTORY_SETTLE_MARGIN = 0.2
GRIPPER_ACTION_NAME = "/panda_hand_controller/gripper_cmd"
GRIPPER_ACTION_TYPE = "control_msgs/action/ParallelGripperCommand"
LEGACY_GRIPPER_ACTION_TYPE = "control_msgs/action/GripperCommand"


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
        self._robot_description_semantic: Optional[str] = None

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
        # `ros2 topic echo /joint_states --once` 输出的是 YAML 风格文本，但格式不固定：
        # - 有些环境会输出 `name: [joint1, joint2]` 这种一行列表。
        # - Jazzy 的常见输出是 `name:\n- joint1\n- joint2` 这种 block list。
        # - DDS 偶尔还会在 YAML 文档前插入 `A message was lost!!!` 之类的状态提示。
        # 所以这里不再用正则硬抠方括号，而是从 `---` 分隔的文档里挑出真正包含
        # `name` 和 `position` 的 JointState YAML，再让 PyYAML 负责解析列表语法。
        parsed_message = None
        for document in str(output).split("---"):
            if "name:" not in document or "position:" not in document:
                continue
            try:
                candidate = yaml.safe_load(document)
            except yaml.YAMLError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("name"), list) and isinstance(
                candidate.get("position"), list
            ):
                parsed_message = candidate
                break

        if parsed_message is None:
            return {"success": False, "error": "没有从 /joint_states 输出中解析到 name 和 position。", "raw": output}

        names = parsed_message["name"]
        positions = parsed_message["position"]
        if not isinstance(names, list) or not isinstance(positions, list):
            return {"success": False, "error": "/joint_states name 和 position 必须是列表。", "raw": output}

        pairs = {
            str(name): float(position)
            for name, position in zip(names, positions)
            if isinstance(position, (int, float))
        }
        return {"success": True, "joint_states": pairs, "names": list(pairs.keys()), "positions": list(pairs.values())}

    def get_gripper_state(self) -> dict[str, Any]:
        joint_states = self.get_joint_states()
        if not joint_states.get("success"):
            return joint_states

        states = dict(joint_states.get("joint_states") or {})
        finger1 = states.get("panda_finger_joint1")
        finger2 = states.get("panda_finger_joint2")
        if not isinstance(finger1, (int, float)):
            return {
                "success": False,
                "error": "当前 /joint_states 里没有 panda_finger_joint1，不能读取夹爪状态。",
                "joint_states": joint_states,
            }

        # Panda hand 的 controller 只命令 panda_finger_joint1；panda_finger_joint2 是跟随/被动关节。
        # 用户更关心的是两指之间的总开口宽度，所以这里同时返回 controller 命令关节值和估算总宽度。
        # 如果第二个 finger 状态暂时不可见，就按对称夹爪估算为 2 * finger1。
        finger1_position = float(finger1)
        finger2_position = float(finger2) if isinstance(finger2, (int, float)) else finger1_position
        estimated_width = max(0.0, finger1_position) + max(0.0, finger2_position)
        return {
            "success": True,
            "controller": "panda_hand_controller",
            "action": GRIPPER_ACTION_NAME,
            "finger_joint_positions": {
                "panda_finger_joint1": finger1_position,
                "panda_finger_joint2": finger2_position,
            },
            "estimated_width": estimated_width,
            "joint_states": joint_states,
        }

    def set_gripper_width(self, width: float, max_effort: float = 0.0) -> dict[str, Any]:
        if not isinstance(width, (int, float)):
            return {"success": False, "error": "夹爪 width 必须是数字，单位是米。"}
        if not isinstance(max_effort, (int, float)):
            return {"success": False, "error": "夹爪 max_effort 必须是数字。"}

        width = float(width)
        max_effort = float(max_effort)
        if width < 0.0 or width > MAX_GRIPPER_OPENING_WIDTH:
            return {
                "success": False,
                "error": f"夹爪 width 必须在 0 到 {MAX_GRIPPER_OPENING_WIDTH:.3f} 米之间。",
                "requested_width": width,
            }

        # Jazzy+ 的 `parallel_gripper_action_controller/GripperActionController` 使用
        # `control_msgs/action/ParallelGripperCommand`，而 Humble 的旧
        # `position_controllers/GripperActionController` 使用 `control_msgs/action/GripperCommand`。
        # 两者 action 名都通常是 `/panda_hand_controller/gripper_cmd`，但 action type 不同；
        # 如果用错 type，`ros2 action send_goal` 会一直等不到匹配的 server，最后表现为超时。
        action_type = GRIPPER_ACTION_TYPE
        action_list = self._run_ros2(["ros2", "action", "list", "-t"], timeout=self.timeout + 2.0)
        if action_list.get("success"):
            matched_types: list[str] = []
            for line in action_list.get("lines", []):
                if line.startswith(f"{GRIPPER_ACTION_NAME} "):
                    match = re.search(r"\[([^\]]+)\]", line)
                    if match:
                        # `ros2 action list -t` 正常是一条 action 对一个 type，但你的 VM 里同名
                        # gripper action 会显示成 `[GripperCommand, ParallelGripperCommand]`。
                        # 这通常是旧 MoveIt controller 配置和 Jazzy controller 同时在 graph 里留下
                        # type 信息。这里拆成候选列表，再按当前 Jazzy controller 的 type 优先选择。
                        matched_types = [
                            action_type.strip()
                            for action_type in match.group(1).split(",")
                            if action_type.strip()
                        ]
                    break
            if matched_types:
                if GRIPPER_ACTION_TYPE in matched_types:
                    action_type = GRIPPER_ACTION_TYPE
                elif LEGACY_GRIPPER_ACTION_TYPE in matched_types:
                    action_type = LEGACY_GRIPPER_ACTION_TYPE
                else:
                    return {
                        "success": False,
                        "error": f"不支持的夹爪 action type：{', '.join(matched_types)}",
                        "supported_action_types": [GRIPPER_ACTION_TYPE, LEGACY_GRIPPER_ACTION_TYPE],
                        "action": GRIPPER_ACTION_NAME,
                    }
            else:
                return {
                    "success": False,
                    "error": f"没有发现夹爪 action server：{GRIPPER_ACTION_NAME}",
                    "available_actions": action_list.get("lines", []),
                }

        # 面向用户的 width 用“两指之间总开口宽度”表达；Panda 当前 controller 命令的是
        # `panda_finger_joint1` 这个单侧 finger joint position，所以发送前要除以 2。
        command_position = width / 2.0
        if action_type == GRIPPER_ACTION_TYPE:
            payload = {
                "command": {
                    "name": ["panda_finger_joint1"],
                    "position": [command_position],
                    "velocity": [],
                    "effort": [max_effort] if max_effort > 0.0 else [],
                }
            }
        elif action_type == LEGACY_GRIPPER_ACTION_TYPE:
            payload = {"command": {"position": command_position, "max_effort": max_effort}}
        else:
            return {
                "success": False,
                "error": f"不支持的夹爪 action type：{action_type}",
                "supported_action_types": [GRIPPER_ACTION_TYPE, LEGACY_GRIPPER_ACTION_TYPE],
                "action": GRIPPER_ACTION_NAME,
            }

        result = self._run_ros2(
            [
                "ros2",
                "action",
                "send_goal",
                GRIPPER_ACTION_NAME,
                action_type,
                json.dumps(payload),
            ],
            timeout=self.timeout + 8.0,
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": f"发送夹爪 action 失败：{result.get('error')}",
                "target_width": width,
                "command_position": command_position,
                "action": GRIPPER_ACTION_NAME,
                "action_type": action_type,
            }

        final_state = self.get_gripper_state()
        width_tolerance = 0.005
        if final_state.get("success"):
            actual_width = final_state.get("estimated_width")
            if isinstance(actual_width, (int, float)) and abs(float(actual_width) - width) > width_tolerance:
                return {
                    "success": False,
                    "status": "goal_not_reached",
                    "error": (
                        f"夹爪 action 已返回，但最终开口 {float(actual_width):.6f} m "
                        f"没有到达目标 {width:.6f} m。"
                    ),
                    "target_width": width,
                    "actual_width": float(actual_width),
                    "width_tolerance": width_tolerance,
                    "command_position": command_position,
                    "max_effort": max_effort,
                    "controller": "panda_hand_controller",
                    "action": GRIPPER_ACTION_NAME,
                    "action_type": action_type,
                    "raw_action_result": result.get("output", ""),
                    "final_state": final_state,
                }

        return {
            "success": True,
            "status": "executed",
            "summary": f"已将夹爪开口设置为 {width:.3f} m。",
            "target_width": width,
            "command_position": command_position,
            "max_effort": max_effort,
            "controller": "panda_hand_controller",
            "action": GRIPPER_ACTION_NAME,
            "action_type": action_type,
            "raw_action_result": result.get("output", ""),
            "final_state": final_state,
        }

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
        # Jazzy/Humble 的 `tf2_echo` CLI 不都有 `--once` 参数。你 VM 里的日志就是因为
        # `--once` 被当成非法参数，工具直接打印 Usage 并退出。这里改成用 `-r 1` 低频输出，
        # 再靠 subprocess timeout 结束进程；只要 timeout 前拿到一段 Translation/Rotation 文本，
        # 就可以解析出当前末端位姿。
        args = ["ros2", "run", "tf2_ros", "tf2_echo", frame_id, end_effector_link, "-r", "1"]
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout + 2.0,
            )
            output = (completed.stdout or completed.stderr or "").strip()
            if completed.returncode != 0:
                return {
                    "success": False,
                    "error": f"读取 {end_effector_link} 在 {frame_id} 下的 TF 失败：{output or completed.returncode}",
                }
        except FileNotFoundError:
            return {
                "success": False,
                "error": "找不到 ros2 命令。请先 source ROS2 环境。",
            }
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if error.stdout is not None else error.output
            stderr = error.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            output = (stdout or stderr or "").strip()
            if not output:
                return {
                    "success": False,
                    "error": f"读取 {end_effector_link} 在 {frame_id} 下的 TF 超时，且没有收到 tf2_echo 输出。",
                }

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
                # SRDF 的 named target 本质是一组关节角。named target 不需要 IK 或笛卡尔规划，
                # 直接把明确关节角发送给 ros2_control 的 JointTrajectoryController 更稳定，
                # 也能避免为了一个固定姿态去占用 MoveItPy planning scene。
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
                # direct trajectory 的等待时间由这个 duration 决定。3 秒比较保守，
                # 但连续执行 named target 时会在每一步结束后明显停顿；1.5 秒在仿真里更利落。
                "duration": DEFAULT_DIRECT_TRAJECTORY_DURATION,
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
                # joint goal 和 named target 共用 direct trajectory adapter，默认时长保持一致。
                "duration": DEFAULT_DIRECT_TRAJECTORY_DURATION,
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
        return {
            "success": True,
            "status": "prepared",
            "summary": f"已准备通过常驻 MoveItPy server 执行 {frame_id} 下的末端 pose。",
            "raw_plan": {
                "adapter": "moveit_py_service",
                "service": "/rosa_arm_moveit_server/move_pose",
                "planning_group": planning_group,
                "frame_id": frame_id,
                "end_effector_link": end_effector_link,
                "pose": pose,
            },
            "metadata": {
                "adapter": "moveit_py_service",
                "source": "rosa_arm_moveit_server",
            },
        }

    def execute_plan(self, plan_result: dict[str, Any]) -> dict[str, Any]:
        """执行当前工具调用刚刚得到的 MoveIt2 规划结果。

        ArmAgent 现在不再把 plan 暴露成用户可见的 `plan_id`。公开 tool 会先调用
        `plan_to_*()` 得到本轮 trajectory，再立刻把这个 result 传进来执行。这样用户看到的是
        “移动到目标”这一件事。

        named target / joint goal 会生成 `joint_trajectory_topic` direct plan，直接交给
        ros2_control 的 `panda_arm_controller`。pose goal 会通过 launch 中常驻的
        `/rosa_arm_moveit_server/move_pose` service 执行，让 MoveItPy 与仿真共享同一个 ROS 时间域。
        """
        if not plan_result.get("success"):
            return {"success": False, "error": "规划未成功，不能执行。", "planning": plan_result}
        raw_plan = plan_result.get("raw_plan")
        if raw_plan is None:
            return {"success": False, "error": "规划结果中没有 raw_plan，不能执行。", "planning": plan_result}

        try:
            if isinstance(raw_plan, dict) and raw_plan.get("adapter") == "joint_trajectory_topic":
                raw_joint_goals = raw_plan.get("joint_goals")
                if raw_joint_goals is None:
                    raw_joint_goals = [raw_plan.get("joint_goal")]
                if not isinstance(raw_joint_goals, list) or not raw_joint_goals:
                    return {
                        "success": False,
                        "error": "直接关节轨迹缺少 joint_goal 或 joint_goals。",
                        "planning": plan_result,
                    }

                joint_names = [f"panda_joint{index}" for index in range(1, 8)]
                normalized_joint_goals: list[dict[str, float]] = []
                for point_index, raw_joint_goal in enumerate(raw_joint_goals, start=1):
                    joint_goal = dict(raw_joint_goal or {})
                    missing_joints = [joint_name for joint_name in joint_names if joint_name not in joint_goal]
                    if missing_joints:
                        return {
                            "success": False,
                            "error": f"直接关节轨迹第 {point_index} 个 waypoint 缺少这些 Panda arm 关节：{missing_joints}",
                            "planning": plan_result,
                        }
                    normalized_joint_goals.append(
                        {joint_name: float(joint_goal[joint_name]) for joint_name in joint_names}
                    )

                configured_duration = float(raw_plan.get("duration", DEFAULT_DIRECT_TRAJECTORY_DURATION))
                if configured_duration <= 0:
                    configured_duration = DEFAULT_DIRECT_TRAJECTORY_DURATION

                # `JointTrajectoryController` 需要每个 waypoint 都有递增的 `time_from_start`。
                # 之前的做法是“每个 waypoint 固定 1.5 秒”，短距离和长距离一样慢，所以机械臂
                # 到达中间姿态附近后会像是在等下一拍。这里改成按关节距离估算每段时间：
                # - 先尽量读取当前 /joint_states，用它计算“当前位置 -> 第一个 waypoint”。
                # - 后续段直接用“上一个 waypoint -> 下一个 waypoint”。
                # - 取 7 个关节里最大的角度变化作为这一段的距离，除以一个保守的仿真速度上限。
                # - 再用最小段时间兜底，避免极短距离被压到 controller 来不及跟踪。
                current_state = self.get_joint_states()
                previous_joint_goal: Optional[dict[str, float]] = None
                if current_state.get("success"):
                    current_joint_states = dict(current_state.get("joint_states") or {})
                    if all(isinstance(current_joint_states.get(joint_name), (int, float)) for joint_name in joint_names):
                        previous_joint_goal = {
                            joint_name: float(current_joint_states[joint_name])
                            for joint_name in joint_names
                        }

                cumulative_time = 0.0
                segment_durations: list[float] = []
                time_from_start_values: list[float] = []
                points = []
                for point_index, joint_goal in enumerate(normalized_joint_goals, start=1):
                    if previous_joint_goal is None:
                        # 如果启动时没有读到完整 joint_states，就只对第一段使用配置的保守时间。
                        # 从第二个 waypoint 开始仍然可以用上一目标点计算距离。
                        segment_duration = configured_duration
                    else:
                        max_joint_delta = max(
                            abs(joint_goal[joint_name] - previous_joint_goal[joint_name])
                            for joint_name in joint_names
                        )
                        segment_duration = max(
                            DIRECT_TRAJECTORY_MIN_SEGMENT_DURATION,
                            max_joint_delta / DIRECT_TRAJECTORY_MAX_JOINT_SPEED,
                        )
                        segment_duration = min(configured_duration, segment_duration)

                    cumulative_time += segment_duration
                    segment_durations.append(segment_duration)
                    time_from_start_values.append(cumulative_time)

                    # 支持小于 1 秒的 waypoint。原来的 `max(1, int(point_time))` 会把 0.35 秒
                    # 强行写成 1 秒，这正是“看起来怎么改 duration 都没变化”的原因之一。
                    sec = int(cumulative_time)
                    nanosec = int(round((cumulative_time - sec) * 1_000_000_000))
                    if nanosec >= 1_000_000_000:
                        sec += 1
                        nanosec -= 1_000_000_000
                    if sec == 0 and nanosec == 0:
                        nanosec = 1_000_000
                    points.append(
                        {
                            "positions": [joint_goal[joint_name] for joint_name in joint_names],
                            "time_from_start": {"sec": sec, "nanosec": nanosec},
                        }
                    )
                    previous_joint_goal = joint_goal
                payload = {
                    "joint_names": joint_names,
                    "points": points,
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
                # 这里只在整条 trajectory 的最后一个 waypoint 后读取最终 joint state；中间
                # waypoint 不再逐个 sleep/读状态，否则会重新制造连续动作之间的停顿。
                time.sleep(cumulative_time + DIRECT_TRAJECTORY_SETTLE_MARGIN)
                execution_info = {
                    "adapter": "joint_trajectory_topic",
                    "waypoints": len(normalized_joint_goals),
                    "segment_durations": segment_durations,
                    "time_from_start": time_from_start_values,
                }
            elif isinstance(raw_plan, dict) and raw_plan.get("adapter") == "moveit_py_service":
                try:
                    rclpy = importlib.import_module("rclpy")
                except ImportError as error:
                    return {
                        "success": False,
                        "error": (
                            f"加载 rclpy 失败：{error}。请确认当前 shell 已 source ROS2，"
                            "并且 `uv run` 使用的环境可以访问 ROS2 Python 包。"
                        ),
                        "planning": plan_result,
                    }

                try:
                    srv_module = importlib.import_module("moveit_resources_panda_moveit_config.srv")
                except ImportError as error:
                    service_import_error = error
                    generated_python_paths: list[str] = []

                    # `MovePose.srv` 是 colcon build 时生成的 Python 包，不是仓库源码里手写的包。
                    # 正常情况下 `source .rosa/arm_ros2_ws/install/setup.*` 会把它加入 PYTHONPATH。
                    # 但用户实际运行时通常是 `uv run python main.py`：uv 的虚拟环境有时不会继承
                    # overlay 里生成的 dist-packages 路径，导致 ROS2 graph 在线、service server 在线，
                    # 但聊天进程 import `moveit_resources_panda_moveit_config.srv` 失败。
                    #
                    # 这里不要求用户手动猜 PYTHONPATH，而是用 ROS2 自己的 package index 找到
                    # moveit_resources_panda_moveit_config 的 install prefix，再把常见的 generated
                    # Python 安装目录补进 sys.path。这样只要 workspace 已经构建过，client 就能
                    # 稳定加载自定义 service 类型。
                    package_prefix = self._run_ros2(
                        ["ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"],
                        timeout=self.timeout + 2.0,
                    )
                    candidate_prefixes: list[str] = []
                    if package_prefix.get("success"):
                        prefix = str(package_prefix.get("output") or "").strip()
                        if prefix:
                            candidate_prefixes.append(prefix)

                    repo_local_prefix = os.path.abspath(
                        os.path.join(
                            os.path.dirname(__file__),
                            "..",
                            ".rosa",
                            "arm_ros2_ws",
                            "install",
                            "moveit_resources_panda_moveit_config",
                        )
                    )
                    if repo_local_prefix not in candidate_prefixes:
                        candidate_prefixes.append(repo_local_prefix)

                    for prefix in candidate_prefixes:
                        for pattern in (
                            "local/lib/python*/dist-packages",
                            "local/lib/python*/site-packages",
                            "lib/python*/dist-packages",
                            "lib/python*/site-packages",
                        ):
                            generated_python_paths.extend(glob.glob(os.path.join(prefix, pattern)))

                    added_python_paths: list[str] = []
                    for python_path in generated_python_paths:
                        if os.path.isdir(python_path) and python_path not in sys.path:
                            sys.path.insert(0, python_path)
                            added_python_paths.append(python_path)

                    try:
                        srv_module = importlib.import_module("moveit_resources_panda_moveit_config.srv")
                    except ImportError:
                        package_prefix_error = (
                            ""
                            if package_prefix.get("success")
                            else f"；ros2 pkg prefix 失败：{package_prefix.get('error')}"
                        )
                        searched = ", ".join(candidate_prefixes) if candidate_prefixes else "无"
                        added = ", ".join(added_python_paths) if added_python_paths else "无"
                        return {
                            "success": False,
                            "error": (
                                f"加载 MoveItPy service client 失败：{service_import_error}。"
                                "已尝试从 ROS2 package prefix 自动补 generated service 的 Python 路径，"
                                f"但仍无法导入。请先运行 `uv sync`，然后重新执行 "
                                "`ARM_AGENT_FORCE_BUILD=1 source scripts/load_arm_ros2_resources.sh`。"
                                f" 已搜索 prefix：{searched}；已加入 sys.path：{added}{package_prefix_error}"
                            ),
                            "planning": plan_result,
                        }

                try:
                    move_pose_service = getattr(srv_module, "MovePose")
                except AttributeError:
                    return {
                        "success": False,
                        "error": (
                            "加载 MoveItPy service client 失败："
                            "`moveit_resources_panda_moveit_config.srv` 中没有 MovePose。"
                            "请重新执行 `ARM_AGENT_FORCE_BUILD=1 source scripts/load_arm_ros2_resources.sh`，"
                            "让 colcon 重新生成 MovePose.srv 的 Python interface。"
                        ),
                        "planning": plan_result,
                    }

                # ArmAgent 主进程只作为 service client，不在这里创建 MoveItPy 或 planning scene。
                # 真正的 MoveItPy 节点由 `arm_mujoco.launch.py` 启动，并使用 `use_sim_time=True`。
                # 这样可以避免聊天进程临时初始化 MoveItPy 时出现 wall time / sim time 混用。
                if not rclpy.ok():
                    rclpy.init()
                node = rclpy.create_node("rosa_arm_agent_pose_client")
                try:
                    service_name = str(raw_plan.get("service") or "/rosa_arm_moveit_server/move_pose")
                    client = node.create_client(move_pose_service, service_name)
                    if not client.wait_for_service(timeout_sec=self.timeout):
                        return {
                            "success": False,
                            "error": (
                                f"找不到 {service_name}。请用 "
                                "`ros2 launch moveit_resources_panda_moveit_config arm_mujoco.launch.py` "
                                "启动带 MoveItPy server 的机械臂仿真。"
                            ),
                            "planning": plan_result,
                        }

                    request = move_pose_service.Request()
                    request.planning_group = str(raw_plan.get("planning_group") or DEFAULT_PLANNING_GROUP)
                    request.frame_id = str(raw_plan.get("frame_id") or DEFAULT_BASE_FRAME)
                    request.end_effector_link = str(raw_plan.get("end_effector_link") or self.end_effector_link)
                    pose = dict(raw_plan.get("pose") or {})
                    position = dict(pose.get("position") or {})
                    orientation = dict(pose.get("orientation") or {})
                    request.pose.position.x = float(position.get("x", 0.0))
                    request.pose.position.y = float(position.get("y", 0.0))
                    request.pose.position.z = float(position.get("z", 0.0))
                    request.pose.orientation.x = float(orientation.get("x", 0.0))
                    request.pose.orientation.y = float(orientation.get("y", 0.0))
                    request.pose.orientation.z = float(orientation.get("z", 0.0))
                    request.pose.orientation.w = float(orientation.get("w", 1.0))

                    future = client.call_async(request)
                    rclpy.spin_until_future_complete(node, future, timeout_sec=self.timeout + 30.0)
                    if not future.done():
                        return {
                            "success": False,
                            "error": f"调用 {service_name} 超时，MoveItPy server 没有在限定时间内返回。",
                            "planning": plan_result,
                        }
                    response = future.result()
                finally:
                    node.destroy_node()

                if response is None:
                    return {
                        "success": False,
                        "error": "MoveItPy server 没有返回 response。",
                        "planning": plan_result,
                    }
                if not bool(response.success):
                    return {
                        "success": False,
                        "error": str(response.error or "MoveItPy server 执行 pose goal 失败。"),
                        "status": str(response.status or "failed"),
                        "planning": plan_result,
                    }
                execution_info = {
                    "adapter": "moveit_py_service",
                    "service": str(raw_plan.get("service") or "/rosa_arm_moveit_server/move_pose"),
                    "status": str(response.status or "executed"),
                    "summary": str(response.summary or ""),
                }
            else:
                runtime = self._load_moveit_py()
                if not runtime.get("success"):
                    return runtime
                trajectory = getattr(raw_plan, "trajectory", raw_plan)
                runtime["moveit_py"].execute(trajectory, controllers=[])
                execution_info = {"adapter": "moveit_py"}
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
            "execution": execution_info,
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
        if self._robot_description_semantic:
            return {
                "success": True,
                "value": self._robot_description_semantic,
                "cached": True,
            }

        # 通过 ROS2 CLI 读取 `/move_group` 节点上的参数。这里不用直接 import MoveIt，
        # 是为了让“只查看配置”的工具在没有 Python MoveIt adapter 时也能工作。
        # 同一个 ArmAgent plan 里可能连续移动到多个 named target；如果每一步都重新
        # `ros2 param get`，VM 里的 `/move_group` 参数服务偶尔会在第二次读取时超时。
        # 因此第一次成功读取并剥出 SRDF XML 后缓存到当前 runtime client，后续步骤直接复用。
        errors: list[str] = []
        result = self._run_ros2(["ros2", "param", "get", "/move_group", "robot_description_semantic"])
        if result.get("success"):
            value = str(result.get("output") or "")
            # `ros2 param get` 的输出不是纯 XML，通常会在真正的 SRDF 前面加一段提示文字：
            #   String value is: <?xml version="1.0" ...>
            # 不同 ROS2 版本可能写成 `String value is:` 或 `value is:`，所以这里兼容这两种前缀，
            # 剥掉前缀以后，后续 `ElementTree.fromstring(...)` 才能直接解析 XML。
            for prefix in ("String value is:", "value is:"):
                if prefix in value:
                    value = value.split(prefix, 1)[1].strip()
                    break
            if value:
                try:
                    ElementTree.fromstring(value)
                except ElementTree.ParseError as error:
                    errors.append(f"/move_group 参数 XML 解析失败：{error}")
                else:
                    self._robot_description_semantic = value
                    return {"success": True, "value": value, "cached": False, "source": "move_group_parameter"}
            else:
                errors.append("/move_group 参数为空。")
        else:
            errors.append(f"/move_group 参数读取失败：{result.get('error')}")

        # 参数服务在你的 VM 里会偶发超时。对于第一阶段 Panda ArmAgent，SRDF 是随仓库和
        # colcon overlay 一起提供的静态 MoveIt config；直接读文件比 `ros2 topic echo` 长字符串
        # 更稳，也避免 topic echo 对超长 XML 输出截断导致 `unclosed token`。
        package_prefix = self._run_ros2(["ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"])
        candidate_paths: list[str] = []
        if package_prefix.get("success"):
            prefix = str(package_prefix.get("output") or "").strip()
            if prefix:
                candidate_paths.append(
                    os.path.join(
                        prefix,
                        "share",
                        "moveit_resources_panda_moveit_config",
                        "config",
                        "panda.srdf",
                    )
                )
        else:
            errors.append(f"ros2 package share 查询失败：{package_prefix.get('error')}")
        candidate_paths.append(
            os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "resources",
                    "arm_agent",
                    "moveit_resources",
                    "panda_moveit_config",
                    "config",
                    "panda.srdf",
                )
            )
        )

        for candidate_path in candidate_paths:
            if not os.path.exists(candidate_path):
                errors.append(f"SRDF 文件不存在：{candidate_path}")
                continue
            try:
                with open(candidate_path, encoding="utf-8") as file:
                    value = file.read().strip()
            except OSError as error:
                errors.append(f"读取 SRDF 文件失败：{candidate_path}: {error}")
                continue
            if not value:
                errors.append(f"SRDF 文件为空：{candidate_path}")
                continue
            try:
                ElementTree.fromstring(value)
            except ElementTree.ParseError as error:
                errors.append(f"SRDF 文件 XML 解析失败：{candidate_path}: {error}")
                continue
            self._robot_description_semantic = value
            return {
                "success": True,
                "value": value,
                "cached": False,
                "source": "srdf_file",
                "path": candidate_path,
            }

        result = self._run_ros2(
            [
                "ros2",
                "topic",
                "echo",
                "/robot_description_semantic",
                "--once",
                "--spin-time",
                str(self.timeout),
            ],
            timeout=self.timeout + 3.0,
        )
        if not result.get("success"):
            errors.append(f"/robot_description_semantic topic 读取失败：{result.get('error')}")
            return {"success": False, "error": "无法读取 robot_description_semantic：" + "；".join(errors)}

        value = str(result.get("output") or "")
        # `ros2 param get` 的输出不是纯 XML，通常会在真正的 SRDF 前面加一段提示文字：
        #   String value is: <?xml version="1.0" ...>
        # 不同 ROS2 版本可能写成 `String value is:` 或 `value is:`，所以这里兼容这两种前缀，
        # 剥掉前缀以后，后续 `ElementTree.fromstring(...)` 才能直接解析 XML。
        for prefix in ("String value is:", "value is:"):
            if prefix in value:
                value = value.split(prefix, 1)[1].strip()
                break
        # `ros2 topic echo /robot_description_semantic --once` 常见输出是 YAML：
        #   data: "<robot ...>...</robot>"
        # 但 DDS 提示或 `---` 分隔符可能混在前面，所以先尝试从 YAML 文档里取 `data`；
        # 如果解析失败，再退回到从原始文本里截取 `<robot ...>`。
        for document in value.split("---"):
            if "data:" not in document:
                continue
            try:
                candidate = yaml.safe_load(document)
            except yaml.YAMLError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("data"), str):
                value = candidate["data"].strip()
                break
        else:
            robot_start = value.find("<robot")
            robot_end = value.rfind("</robot>")
            if robot_start >= 0 and robot_end >= robot_start:
                value = value[robot_start : robot_end + len("</robot>")].strip()
        if not value:
            return {"success": False, "error": "robot_description_semantic 为空。"}
        try:
            ElementTree.fromstring(value)
        except ElementTree.ParseError as error:
            return {
                "success": False,
                "error": "无法读取 robot_description_semantic：" + "；".join([*errors, f"topic XML 解析失败：{error}"]),
            }
        self._robot_description_semantic = value
        return {"success": True, "value": value, "cached": False, "source": "robot_description_semantic_topic"}

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
            # 这个本地 MoveItPy loader 只保留给旧 raw_plan fallback。正常 pose goal 已经改为调用
            # launch 中常驻的 `rosa_arm_moveit_server`，由那个 ROS2 进程显式使用 sim time。
            # 这里不再尝试修正仿真时间，避免聊天进程临时初始化 MoveItPy 时把 agent 带崩。
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
