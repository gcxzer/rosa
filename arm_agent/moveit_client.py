"""ArmAgent 的 MoveIt2 运行时适配边界。

这个模块刻意不在导入阶段依赖 `rclpy`、MoveIt2 或 MuJoCo。普通开发机运行单元测试时，
只需要能导入这些 Python 文件；真正和 ROS2/MoveIt2/MuJoCo 通信的逻辑集中在运行时 client
里。后续如果要从 `moveit_py` 切换到 action/service 级客户端，也只需要替换这里。
"""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
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
        controllers = self._run_ros2(["ros2", "control", "list_controllers"])

        detected = {
            "nodes": nodes.get("lines", []),
            "topics": topics.get("lines", []),
            "services": services.get("lines", []),
            "controllers": controllers.get("lines", []),
        }
        missing: list[str] = []
        if not nodes.get("success"):
            missing.append("ros2 node list")
        if not topics.get("success"):
            missing.append("ros2 topic list")
        if not services.get("success"):
            missing.append("ros2 service list")
        if not controllers.get("success"):
            missing.append("ros2 control list_controllers")

        node_text = "\n".join(detected["nodes"])
        topic_text = "\n".join(detected["topics"])
        service_text = "\n".join(detected["services"])
        controller_text = "\n".join(detected["controllers"])
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
        runtime = self._load_moveit_py()
        if not runtime.get("success"):
            return runtime

        try:
            planning_component = runtime["moveit_py"].get_planning_component(planning_group)
            planning_component.set_start_state_to_current_state()
            planning_component.set_goal_state(configuration_name=target_name)
            plan_result = planning_component.plan()
        except Exception as error:
            return {"success": False, "error": f"MoveIt2 named target 规划失败：{error}"}

        return _normalize_plan_result(
            plan_result,
            summary=f"已规划到 named target `{target_name}`。",
            adapter="moveit_py",
        )

    def plan_to_joint_goal(self, planning_group: str, joint_goal: dict[str, float]) -> dict[str, Any]:
        runtime = self._load_moveit_py()
        if not runtime.get("success"):
            return runtime

        try:
            robot_state = runtime["robot_state_class"](runtime["moveit_py"].get_robot_model())
            joint_model_group = runtime["moveit_py"].get_robot_model().get_joint_model_group(planning_group)
            robot_state.set_joint_group_positions(joint_model_group, joint_goal)
            planning_component = runtime["moveit_py"].get_planning_component(planning_group)
            planning_component.set_start_state_to_current_state()
            planning_component.set_goal_state(robot_state=robot_state)
            plan_result = planning_component.plan()
        except Exception as error:
            return {"success": False, "error": f"MoveIt2 joint goal 规划失败：{error}"}

        return _normalize_plan_result(
            plan_result,
            summary="已规划到 joint goal。",
            adapter="moveit_py",
        )

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
        “移动到目标”这一件事；MoveIt 内部仍然按正常流程先规划 trajectory，再交给控制器执行。
        """
        runtime = self._load_moveit_py()
        if not runtime.get("success"):
            return runtime

        if not plan_result.get("success"):
            return {"success": False, "error": "规划未成功，不能执行。", "planning": plan_result}
        raw_plan = plan_result.get("raw_plan")
        if raw_plan is None:
            return {"success": False, "error": "规划结果中没有 raw_plan，不能执行。", "planning": plan_result}

        try:
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
        controllers = self._run_ros2(["ros2", "control", "list_controllers"])
        if not controllers.get("success"):
            return controllers
        return {
            "success": True,
            "summary": "已请求停止检查；如需硬停止，请在控制器侧执行 halt/stop 或急停流程。",
            "controllers": controllers.get("lines", []),
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
        except ImportError as error:
            # 这里是唯一需要报告 MoveIt2 Python adapter 缺失的地方，直接返回完整错误，
            # 不再额外包一层只调用一次的私有 helper。
            return {
                "success": False,
                "error": (
                    f"加载 MoveIt2 Python adapter 失败：{error}。需要 MoveIt2 Python 运行时适配器。"
                    "请确认当前 ROS2 shell 已安装并 source `moveit_py`、MoveIt2、ros2_control "
                    "和 mujoco_ros2_control。"
                ),
            }

        try:
            if not rclpy.ok():
                # 只在运行时初始化 rclpy，保证普通 import/test 不需要 ROS2 环境。
                rclpy.init()
            moveit_py = planning_module.MoveItPy(node_name="rosa_arm_agent")
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
