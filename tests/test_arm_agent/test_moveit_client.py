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
