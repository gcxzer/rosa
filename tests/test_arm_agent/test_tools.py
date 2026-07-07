import pytest

from arm_agent import tools as arm_tools


class FakeMoveItClient:
    """测试用 MoveIt client：模拟 ROS2/MoveIt2/MuJoCo 返回值，不启动外部进程。"""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.executed_plans = []

    def check_readiness(self):
        if self.ready:
            return {
                "success": True,
                "missing": [],
                "detected": {
                    "nodes": ["/move_group", "/robot_state_publisher"],
                    "topics": ["/joint_states"],
                    "services": ["/controller_manager/list_controllers"],
                    "controllers": ["panda_arm_controller active", "mujoco_ros2_control active"],
                },
                "summary": "ready",
            }
        return {
            "success": False,
            "missing": ["MoveIt2 move_group", "mujoco_ros2_control"],
            "detected": {"nodes": [], "topics": [], "services": [], "controllers": []},
            "summary": "missing",
        }

    def get_joint_states(self):
        return {"success": True, "joint_states": {"panda_joint1": 0.1}}

    def get_planning_groups(self):
        return {"success": True, "planning_groups": ["panda_arm"]}

    def get_named_targets(self, planning_group):
        return {"success": True, "planning_group": planning_group, "named_targets": ["home", "ready"]}

    def get_end_effector_link(self, planning_group):
        return {"success": True, "planning_group": planning_group, "end_effector_link": "panda_hand"}

    def get_end_effector_pose(self, frame_id, end_effector_link):
        return {
            "success": True,
            "frame_id": frame_id,
            "end_effector_link": end_effector_link,
            "pose": {"position": {"x": 0.4, "y": 0.0, "z": 0.4}},
        }

    def validate_joint_goal(self, planning_group, joint_goal):
        del planning_group
        if any(not isinstance(value, (int, float)) for value in joint_goal.values()):
            return {"success": False, "error": "invalid joint"}
        return {"success": True}

    def plan_to_named_target(self, planning_group, target_name):
        return {
            "success": True,
            "summary": f"{planning_group}:{target_name}",
            "raw_plan": {"kind": "named"},
        }

    def plan_to_joint_goal(self, planning_group, joint_goal):
        return {
            "success": True,
            "summary": f"{planning_group}:{sorted(joint_goal)}",
            "raw_plan": {"kind": "joint"},
        }

    def plan_to_pose_goal(self, *, planning_group, frame_id, end_effector_link, pose):
        return {
            "success": True,
            "summary": f"{planning_group}:{frame_id}:{end_effector_link}:{pose['position']['x']}",
            "raw_plan": {"kind": "pose"},
        }

    def execute_plan(self, plan_result):
        self.executed_plans.append(plan_result["raw_plan"]["kind"])
        return {
            "success": True,
            "status": "executed",
            "planning": {"summary": plan_result["summary"]},
            "final_state": {"joint_states": {"panda_joint1": 0.2}},
        }

    def stop_motion(self):
        return {"success": True, "summary": "stopped"}


@pytest.fixture(autouse=True)
def reset_arm_tools(monkeypatch):
    # 每个测试都恢复默认 client 工厂，避免不同 fake client 互相污染。
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", arm_tools.MoveItRuntimeClient)


def test_readiness_success_and_missing_failure(monkeypatch):
    ready_client = FakeMoveItClient(ready=True)
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: ready_client)

    ready = arm_tools.arm_check_readiness.invoke({})
    assert ready["success"] is True
    assert "mujoco_ros2_control active" in ready["detected"]["controllers"]

    missing_client = FakeMoveItClient(ready=False)
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: missing_client)
    missing = arm_tools.arm_move_to_named_target.invoke({"target_name": "home"})

    assert missing["success"] is False
    assert "未就绪" in missing["error"]
    assert "MoveIt2 move_group" in missing["readiness"]["missing"]
    assert missing_client.executed_plans == []


def test_tool_descriptions_explain_safety_contracts():
    """LangChain tool description 会直接给模型看，必须包含关键安全语义。"""
    assert "未就绪会拒绝移动" in arm_tools.arm_move_to_named_target.description
    assert "一步执行" in arm_tools.arm_move_to_named_target.description
    assert "关节名" in arm_tools.arm_move_to_joint_goal.description
    assert "必须有明确坐标系" in arm_tools.arm_move_to_pose_goal.description
    assert "不需要 `plan_id`" in arm_tools.arm_move_to_pose_goal.description
    assert "真实硬件急停必须走硬件安全链路" in arm_tools.arm_stop.description


def test_state_tools_return_stable_shapes(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    assert arm_tools.arm_get_joint_states.invoke({})["joint_states"] == {"panda_joint1": 0.1}
    assert arm_tools.arm_get_planning_groups.invoke({})["planning_groups"] == ["panda_arm"]
    assert arm_tools.arm_get_named_targets.invoke({"planning_group": "panda_arm"})["named_targets"] == ["home", "ready"]
    assert arm_tools.arm_get_end_effector_link.invoke({"planning_group": "panda_arm"})["end_effector_link"] == "panda_hand"
    assert arm_tools.arm_get_end_effector_pose.invoke({"frame_id": "panda_link0"})["frame_id"] == "panda_link0"


def test_named_joint_and_pose_moves_execute_immediately(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    named = arm_tools.arm_move_to_named_target.invoke({"target_name": "home"})
    joint = arm_tools.arm_move_to_joint_goal.invoke({"joint_goal": {"panda_joint1": 0.2}})
    pose = arm_tools.arm_move_to_pose_goal.invoke({"x": 0.4, "y": 0.0, "z": 0.4, "frame_id": "panda_link0"})

    assert named["success"] is True
    assert named["status"] == "executed"
    assert named["target_type"] == "named_target"
    assert joint["target_type"] == "joint_goal"
    assert pose["target_type"] == "pose_goal"
    assert client.executed_plans == ["named", "joint", "pose"]


def test_pose_planning_requires_frame_id(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_move_to_pose_goal.invoke({"x": 0.4, "y": 0.0, "z": 0.4, "frame_id": ""})

    assert result["success"] is False
    assert "frame_id" in result["error"]
    assert client.executed_plans == []


def test_planning_failure_does_not_execute(monkeypatch):
    class FailingPlanClient(FakeMoveItClient):
        def plan_to_named_target(self, planning_group, target_name):
            del planning_group, target_name
            return {"success": False, "error": "planning failed"}

    client = FailingPlanClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_move_to_named_target.invoke({"target_name": "home"})

    assert result["success"] is False
    assert result["error"] == "planning failed"
    assert client.executed_plans == []


def test_successful_execution_returns_final_state_and_stop_reports_success(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    executed = arm_tools.arm_move_to_named_target.invoke({"target_name": "home"})
    stopped = arm_tools.arm_stop.invoke({})

    assert executed["success"] is True
    assert executed["status"] == "executed"
    assert executed["final_state"]["joint_states"]["panda_joint1"] == 0.2
    assert stopped["success"] is True
