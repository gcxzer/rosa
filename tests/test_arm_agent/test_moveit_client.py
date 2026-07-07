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
