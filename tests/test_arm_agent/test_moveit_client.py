import json

from arm_agent.moveit_client import MoveItRuntimeClient


class StubMoveItRuntimeClient(MoveItRuntimeClient):
    """用固定命令结果替代真实 ROS2 CLI，避免单元测试依赖外部 ROS graph。"""

    def __init__(self, results):
        super().__init__(timeout=0.1)
        self.results = results
        self.commands = []

    def _run_ros2(self, args, timeout=None):
        del timeout
        self.commands.append(list(args))
        key = tuple(args)
        return self.results.get(key, {"success": False, "error": f"unexpected command: {' '.join(args)}"})


def test_list_controllers_falls_back_to_controller_manager_service():
    """CLI 没拿到 controller 列表时，应继续尝试 controller_manager service。"""
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "control", "list_controllers", "-c", "/controller_manager"): {
                "success": False,
                "error": "explicit cli failed",
            },
            ("ros2", "control", "list_controllers"): {
                "success": False,
                "error": "default cli failed",
            },
            (
                "ros2",
                "service",
                "call",
                "/controller_manager/list_controllers",
                "controller_manager_msgs/srv/ListControllers",
                "{}",
            ): {
                "success": True,
                "lines": ["name: panda_arm_controller", "state: active"],
            },
        }
    )

    result = client._list_controllers()

    assert result["success"] is True
    assert result["source"] == "controller_manager_service"
    assert "panda_arm_controller" in "\n".join(result["lines"])


def test_readiness_accepts_visible_controller_graph_when_cli_fails():
    """controller graph 已经在线时，不要只因为 ros2 control CLI 异常就误判未就绪。"""
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "node", "list"): {
                "success": True,
                "lines": [
                    "/move_group",
                    "/robot_state_publisher",
                    "/controller_manager",
                    "/joint_state_broadcaster",
                    "/panda_arm_controller",
                    "/panda_hand_controller",
                    "/mujoco_ros2_control_node",
                ],
            },
            ("ros2", "topic", "list"): {
                "success": True,
                "lines": ["/joint_states", "/robot_description"],
            },
            ("ros2", "service", "list"): {
                "success": True,
                "lines": ["/controller_manager/list_controllers", "/plan_kinematic_path"],
            },
            ("ros2", "control", "list_controllers", "-c", "/controller_manager"): {
                "success": False,
                "error": "explicit cli failed",
            },
            ("ros2", "control", "list_controllers"): {
                "success": False,
                "error": "default cli failed",
            },
            (
                "ros2",
                "service",
                "call",
                "/controller_manager/list_controllers",
                "controller_manager_msgs/srv/ListControllers",
                "{}",
            ): {
                "success": False,
                "error": "service call failed",
            },
        }
    )

    readiness = client.check_readiness()

    assert readiness["success"] is True
    assert "ros2 control list_controllers" not in readiness["missing"]
    assert readiness["detected"]["controller_query_source"] == "controller_query_failed"
    assert "service call failed" in readiness["detected"]["controller_query_error"]


def test_joint_states_parser_accepts_ros2_block_yaml_with_status_prefix():
    """`ros2 topic echo` 的 block YAML 输出应能解析，即使前面有 DDS 状态提示。"""
    client = StubMoveItRuntimeClient(
        {
            (
                "ros2",
                "topic",
                "echo",
                "/joint_states",
                "--once",
                "--spin-time",
                "0.1",
            ): {
                "success": True,
                "output": """
A message was lost!!!
\ttotal count change:1
\ttotal count: 1---
header:
  stamp:
    sec: 55
    nanosec: 948000000
  frame_id: base_link
name:
- panda_finger_joint1
- panda_finger_joint2
- panda_joint1
- panda_joint2
- panda_joint3
- panda_joint4
- panda_joint5
- panda_joint6
- panda_joint7
position:
- 4.783619422869794e-08
- -9.096024813507766e-10
- 4.0003475776561256e-22
- 0.0065817142426712865
- -1.7787051379161643e-06
- -1.5771029104160732
- -0.0003344845339609694
- 1.5696488841127192
- -0.7852999791214366
velocity: []
effort:
- .nan
- .nan
---
""",
            },
        }
    )

    result = client.get_joint_states()

    assert result["success"] is True
    assert result["joint_states"]["panda_joint4"] == -1.5771029104160732
    assert result["joint_states"]["panda_joint7"] == -0.7852999791214366


def test_named_target_uses_srdf_joint_values_without_moveit_py():
    """named target 应直接从 SRDF 解析关节值，避免触发 MoveItPy 的仿真时间 abort。"""
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "param", "get", "/move_group", "robot_description_semantic"): {
                "success": True,
                "output": """
String value is: <robot name="panda">
  <group_state group="panda_arm" name="home">
    <joint name="panda_joint1" value="0.1"/>
    <joint name="panda_joint2" value="0.2"/>
    <joint name="panda_joint3" value="0.3"/>
    <joint name="panda_joint4" value="0.4"/>
    <joint name="panda_joint5" value="0.5"/>
    <joint name="panda_joint6" value="0.6"/>
    <joint name="panda_joint7" value="0.7"/>
  </group_state>
</robot>
""",
            },
        }
    )

    result = client.plan_to_named_target("panda_arm", "home")

    assert result["success"] is True
    assert result["raw_plan"]["adapter"] == "joint_trajectory_topic"
    assert result["raw_plan"]["joint_goal"]["panda_joint4"] == 0.4
    assert result["metadata"]["moveit_py_bypassed"] is True


def test_robot_description_semantic_is_cached_for_repeated_named_targets():
    """同一个 plan 里连续 named target 不应反复读取 `/move_group` 的 SRDF 参数。

    VM 实测里第一步 `extended` 可以成功，但第二步 `home` 再次执行
    `ros2 param get /move_group robot_description_semantic` 时可能超时。runtime client 成功读到
    一次 SRDF 后应该缓存本轮 XML，连续动作直接复用这份配置。
    """
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "param", "get", "/move_group", "robot_description_semantic"): {
                "success": True,
                "output": """
String value is: <robot name="panda">
  <group_state group="panda_arm" name="extended">
    <joint name="panda_joint1" value="1.1"/>
    <joint name="panda_joint2" value="1.2"/>
    <joint name="panda_joint3" value="1.3"/>
    <joint name="panda_joint4" value="1.4"/>
    <joint name="panda_joint5" value="1.5"/>
    <joint name="panda_joint6" value="1.6"/>
    <joint name="panda_joint7" value="1.7"/>
  </group_state>
  <group_state group="panda_arm" name="home">
    <joint name="panda_joint1" value="0.1"/>
    <joint name="panda_joint2" value="0.2"/>
    <joint name="panda_joint3" value="0.3"/>
    <joint name="panda_joint4" value="0.4"/>
    <joint name="panda_joint5" value="0.5"/>
    <joint name="panda_joint6" value="0.6"/>
    <joint name="panda_joint7" value="0.7"/>
  </group_state>
</robot>
""",
            },
        }
    )

    extended = client.plan_to_named_target("panda_arm", "extended")
    home = client.plan_to_named_target("panda_arm", "home")
    semantic_reads = [
        command
        for command in client.commands
        if command == ["ros2", "param", "get", "/move_group", "robot_description_semantic"]
    ]

    assert extended["success"] is True
    assert home["success"] is True
    assert extended["raw_plan"]["joint_goal"]["panda_joint7"] == 1.7
    assert home["raw_plan"]["joint_goal"]["panda_joint7"] == 0.7
    assert semantic_reads == [["ros2", "param", "get", "/move_group", "robot_description_semantic"]]


def test_robot_description_semantic_falls_back_to_srdf_file_when_parameter_times_out(tmp_path):
    """参数服务超时时，应优先读取已安装 MoveIt config 里的 panda.srdf 文件。

    VM 里 `ros2 topic echo /robot_description_semantic` 对较长 SRDF XML 可能只吐出不完整文本，
    继续依赖 topic 会导致 `unclosed token`。这里模拟 `/move_group` 参数超时，但 ROS2 package
    prefix 可用，确保执行 named target 时直接从静态 SRDF 文件恢复。
    """
    package_prefix = tmp_path / "install" / "moveit_resources_panda_moveit_config"
    srdf_path = package_prefix / "share" / "moveit_resources_panda_moveit_config" / "config" / "panda.srdf"
    srdf_path.parent.mkdir(parents=True)
    srdf_path.write_text(
        """
<robot name="panda">
  <group_state group="panda_arm" name="ready">
    <joint name="panda_joint1" value="0.1"/>
    <joint name="panda_joint2" value="0.2"/>
    <joint name="panda_joint3" value="0.3"/>
    <joint name="panda_joint4" value="0.4"/>
    <joint name="panda_joint5" value="0.5"/>
    <joint name="panda_joint6" value="0.6"/>
    <joint name="panda_joint7" value="0.7"/>
  </group_state>
</robot>
""".strip(),
        encoding="utf-8",
    )
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "param", "get", "/move_group", "robot_description_semantic"): {
                "success": False,
                "error": "命令超时：ros2 param get /move_group robot_description_semantic",
            },
            ("ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"): {
                "success": True,
                "output": str(package_prefix),
            },
        }
    )

    result = client.plan_to_named_target("panda_arm", "ready")

    assert result["success"] is True
    assert result["raw_plan"]["joint_goal"]["panda_joint7"] == 0.7
    assert client.commands == [
        ["ros2", "param", "get", "/move_group", "robot_description_semantic"],
        ["ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"],
    ]


def test_robot_description_semantic_falls_back_to_topic_when_parameter_and_files_fail(monkeypatch):
    """参数和 SRDF 文件都不可用时，才最后尝试从 `/robot_description_semantic` topic 读取。"""
    monkeypatch.setattr("arm_agent.moveit_client.os.path.exists", lambda path: False)
    client = StubMoveItRuntimeClient(
        {
            ("ros2", "param", "get", "/move_group", "robot_description_semantic"): {
                "success": False,
                "error": "命令超时：ros2 param get /move_group robot_description_semantic",
            },
            ("ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"): {
                "success": False,
                "error": "package not found",
            },
            (
                "ros2",
                "topic",
                "echo",
                "/robot_description_semantic",
                "--once",
                "--spin-time",
                "0.1",
            ): {
                "success": True,
                "output": """
data: |
  <robot name="panda">
    <group_state group="panda_arm" name="ready">
      <joint name="panda_joint1" value="0.1"/>
      <joint name="panda_joint2" value="0.2"/>
      <joint name="panda_joint3" value="0.3"/>
      <joint name="panda_joint4" value="0.4"/>
      <joint name="panda_joint5" value="0.5"/>
      <joint name="panda_joint6" value="0.6"/>
      <joint name="panda_joint7" value="0.7"/>
    </group_state>
  </robot>
""",
            },
        }
    )

    result = client.plan_to_named_target("panda_arm", "ready")

    assert result["success"] is True
    assert result["raw_plan"]["joint_goal"]["panda_joint7"] == 0.7
    assert client.commands == [
        ["ros2", "param", "get", "/move_group", "robot_description_semantic"],
        ["ros2", "pkg", "prefix", "moveit_resources_panda_moveit_config"],
        [
            "ros2",
            "topic",
            "echo",
            "/robot_description_semantic",
            "--once",
            "--spin-time",
            "0.1",
        ],
    ]


def test_execute_direct_joint_trajectory_publishes_controller_topic(monkeypatch):
    """direct trajectory 执行时应发布给 ros2_control，而不是先加载 MoveItPy。"""

    class PublishingClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args[:5] == [
                "ros2",
                "topic",
                "pub",
                "--once",
                "/panda_arm_controller/joint_trajectory",
            ]:
                return {"success": True, "output": "published", "lines": ["published"]}
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

        def get_joint_states(self):
            return {"success": True, "joint_states": {"panda_joint1": 0.1}}

    client = PublishingClient()
    monkeypatch.setattr("arm_agent.moveit_client.time.sleep", lambda seconds: None)
    plan_result = {
        "success": True,
        "status": "planned",
        "summary": "direct",
        "raw_plan": {
            "adapter": "joint_trajectory_topic",
            "duration": 0.0,
            "joint_goal": {f"panda_joint{index}": float(index) for index in range(1, 8)},
        },
        "metadata": {"adapter": "joint_trajectory_topic"},
    }

    executed = client.execute_plan(plan_result)

    assert executed["success"] is True
    assert executed["status"] == "executed"
    assert client.commands[0][:6] == [
        "ros2",
        "topic",
        "pub",
        "--once",
        "/panda_arm_controller/joint_trajectory",
        "trajectory_msgs/msg/JointTrajectory",
    ]
    assert '"panda_joint7"' in client.commands[0][-1]


def test_execute_direct_joint_trajectory_supports_multiple_waypoints(monkeypatch):
    """连续 named target 应能合成一条多 waypoint trajectory，避免两步之间 Python 停顿。"""

    class PublishingClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})
            self.sleeps = []

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args[:5] == [
                "ros2",
                "topic",
                "pub",
                "--once",
                "/panda_arm_controller/joint_trajectory",
            ]:
                return {"success": True, "output": "published", "lines": ["published"]}
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

        def get_joint_states(self):
            return {"success": True, "joint_states": {"panda_joint1": 0.1}}

    client = PublishingClient()
    monkeypatch.setattr("arm_agent.moveit_client.time.sleep", lambda seconds: client.sleeps.append(seconds))
    first_goal = {f"panda_joint{index}": float(index) for index in range(1, 8)}
    second_goal = {f"panda_joint{index}": float(index + 10) for index in range(1, 8)}
    plan_result = {
        "success": True,
        "status": "planned",
        "summary": "combined",
        "raw_plan": {
            "adapter": "joint_trajectory_topic",
            "duration": 1.5,
            "joint_goals": [first_goal, second_goal],
        },
        "metadata": {"adapter": "joint_trajectory_topic"},
    }

    executed = client.execute_plan(plan_result)
    payload = json.loads(client.commands[0][-1])

    assert executed["success"] is True
    assert len(payload["points"]) == 2
    assert payload["points"][0]["positions"][-1] == 7.0
    assert payload["points"][0]["time_from_start"] == {"sec": 1, "nanosec": 500000000}
    assert payload["points"][1]["positions"][-1] == 17.0
    assert payload["points"][1]["time_from_start"] == {"sec": 3, "nanosec": 0}
    assert client.sleeps == [3.2]


def test_execute_direct_joint_trajectory_uses_joint_distance_timing(monkeypatch):
    """连续 waypoint 应按关节距离分配时间，短距离不要被固定 duration 拖慢。"""

    class PublishingClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})
            self.sleeps = []

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args[:5] == [
                "ros2",
                "topic",
                "pub",
                "--once",
                "/panda_arm_controller/joint_trajectory",
            ]:
                return {"success": True, "output": "published", "lines": ["published"]}
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

        def get_joint_states(self):
            return {
                "success": True,
                "joint_states": {f"panda_joint{index}": 0.0 for index in range(1, 8)},
            }

    client = PublishingClient()
    monkeypatch.setattr("arm_agent.moveit_client.time.sleep", lambda seconds: client.sleeps.append(seconds))
    first_goal = {f"panda_joint{index}": 0.2 for index in range(1, 8)}
    second_goal = {f"panda_joint{index}": 0.4 for index in range(1, 8)}
    plan_result = {
        "success": True,
        "status": "planned",
        "summary": "short combined",
        "raw_plan": {
            "adapter": "joint_trajectory_topic",
            "duration": 1.5,
            "joint_goals": [first_goal, second_goal],
        },
        "metadata": {"adapter": "joint_trajectory_topic"},
    }

    executed = client.execute_plan(plan_result)
    payload = json.loads(client.commands[0][-1])

    assert executed["success"] is True
    assert payload["points"][0]["time_from_start"] == {"sec": 0, "nanosec": 350000000}
    assert payload["points"][1]["time_from_start"] == {"sec": 0, "nanosec": 700000000}
    assert executed["execution"]["segment_durations"] == [0.35, 0.35]
    assert executed["execution"]["time_from_start"] == [0.35, 0.7]
    assert round(client.sleeps[0], 3) == 0.9


def test_partial_joint_goal_is_completed_from_current_joint_states():
    """用户只指定部分关节时，工具应从当前状态补齐完整 Panda 7 轴目标。"""

    class JointStateClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def get_joint_states(self):
            return {
                "success": True,
                "joint_states": {f"panda_joint{index}": float(index) for index in range(1, 8)},
            }

    result = JointStateClient().plan_to_joint_goal("panda_arm", {"panda_joint1": 9.0})

    assert result["success"] is True
    assert result["raw_plan"]["joint_goal"]["panda_joint1"] == 9.0
    assert result["raw_plan"]["joint_goal"]["panda_joint7"] == 7.0


def test_set_gripper_width_sends_gripper_action_and_reports_state():
    """夹爪宽度应换算成单侧 finger joint position 后发送 GripperCommand action。"""

    class GripperClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args == ["ros2", "action", "list", "-t"]:
                return {
                    "success": True,
                    "lines": [
                        "/panda_hand_controller/gripper_cmd [control_msgs/action/ParallelGripperCommand]"
                    ],
                }
            if args[:5] == [
                "ros2",
                "action",
                "send_goal",
                "/panda_hand_controller/gripper_cmd",
                "control_msgs/action/ParallelGripperCommand",
            ]:
                return {"success": True, "output": "Goal accepted\nResult: success", "lines": ["Goal accepted"]}
            if args[:5] == ["ros2", "topic", "echo", "/joint_states", "--once"]:
                return {
                    "success": True,
                    "output": """
---
name:
- panda_finger_joint1
- panda_finger_joint2
position:
- 0.03
- 0.03
---
""",
                }
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

    client = GripperClient()
    result = client.set_gripper_width(0.06, max_effort=2.0)

    assert result["success"] is True
    assert result["target_width"] == 0.06
    assert result["command_position"] == 0.03
    assert result["action_type"] == "control_msgs/action/ParallelGripperCommand"
    assert result["final_state"]["estimated_width"] == 0.06
    assert '"name": ["panda_finger_joint1"]' in client.commands[1][-1]
    assert '"position": [0.03]' in client.commands[1][-1]
    assert '"effort": [2.0]' in client.commands[1][-1]


def test_set_gripper_width_supports_legacy_gripper_action_type():
    """Humble 旧 controller 仍使用 control_msgs/action/GripperCommand。"""

    class LegacyGripperClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args == ["ros2", "action", "list", "-t"]:
                return {
                    "success": True,
                    "lines": ["/panda_hand_controller/gripper_cmd [control_msgs/action/GripperCommand]"],
                }
            if args[:5] == [
                "ros2",
                "action",
                "send_goal",
                "/panda_hand_controller/gripper_cmd",
                "control_msgs/action/GripperCommand",
            ]:
                return {"success": True, "output": "Goal accepted", "lines": ["Goal accepted"]}
            if args[:5] == ["ros2", "topic", "echo", "/joint_states", "--once"]:
                return {
                    "success": True,
                    "output": """
---
name:
- panda_finger_joint1
- panda_finger_joint2
position:
- 0.02
- 0.02
---
""",
                }
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

    client = LegacyGripperClient()
    result = client.set_gripper_width(0.04)

    assert result["success"] is True
    assert result["action_type"] == "control_msgs/action/GripperCommand"
    assert '"position": 0.02' in client.commands[1][-1]
    assert '"max_effort": 0.0' in client.commands[1][-1]


def test_set_gripper_width_prefers_parallel_type_when_action_lists_multiple_types():
    """同名夹爪 action 同时报告新旧 type 时，应优先使用 Jazzy 的 ParallelGripperCommand。"""

    class MultiTypeGripperClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args == ["ros2", "action", "list", "-t"]:
                return {
                    "success": True,
                    "lines": [
                        "/panda_hand_controller/gripper_cmd "
                        "[control_msgs/action/GripperCommand, control_msgs/action/ParallelGripperCommand]"
                    ],
                }
            if args[:5] == [
                "ros2",
                "action",
                "send_goal",
                "/panda_hand_controller/gripper_cmd",
                "control_msgs/action/ParallelGripperCommand",
            ]:
                return {"success": True, "output": "Goal accepted", "lines": ["Goal accepted"]}
            if args[:5] == ["ros2", "topic", "echo", "/joint_states", "--once"]:
                return {
                    "success": True,
                    "output": """
---
name:
- panda_finger_joint1
- panda_finger_joint2
position:
- 0.035
- 0.035
---
""",
                }
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

    result = MultiTypeGripperClient().set_gripper_width(0.07)

    assert result["success"] is True
    assert result["action_type"] == "control_msgs/action/ParallelGripperCommand"


def test_set_gripper_width_reports_failure_when_final_width_is_not_reached():
    """action 返回成功但 joint state 没到目标时，工具应该报告失败。"""

    class StalledGripperClient(StubMoveItRuntimeClient):
        def __init__(self):
            super().__init__({})

        def _run_ros2(self, args, timeout=None):
            del timeout
            self.commands.append(list(args))
            if args == ["ros2", "action", "list", "-t"]:
                return {
                    "success": True,
                    "lines": [
                        "/panda_hand_controller/gripper_cmd [control_msgs/action/ParallelGripperCommand]"
                    ],
                }
            if args[:5] == [
                "ros2",
                "action",
                "send_goal",
                "/panda_hand_controller/gripper_cmd",
                "control_msgs/action/ParallelGripperCommand",
            ]:
                return {
                    "success": True,
                    "output": "Result:\nstalled: true\nreached_goal: false\nGoal finished with status: SUCCEEDED",
                    "lines": ["Goal finished with status: SUCCEEDED"],
                }
            if args[:5] == ["ros2", "topic", "echo", "/joint_states", "--once"]:
                return {
                    "success": True,
                    "output": """
---
name:
- panda_finger_joint1
- panda_finger_joint2
position:
- 0.000005
- 0.000005
---
""",
                }
            return {"success": False, "error": f"unexpected command: {' '.join(args)}"}

    result = StalledGripperClient().set_gripper_width(0.07)

    assert result["success"] is False
    assert result["status"] == "goal_not_reached"
    assert result["actual_width"] == 0.00001


def test_set_gripper_width_rejects_out_of_range_width():
    result = StubMoveItRuntimeClient({}).set_gripper_width(0.2)

    assert result["success"] is False
    assert "0.080" in result["error"]
