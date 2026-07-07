import json

import pytest

from arm_agent import tools as arm_tools


class FakeMoveItClient:
    """测试用 MoveIt client：模拟 ROS2/MoveIt2/MuJoCo 返回值，不启动外部进程。"""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.executed_plans = []
        self.executed_named_targets = []
        self.gripper_widths = []

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
        return {"success": True, "planning_group": planning_group, "named_targets": ["home", "ready", "extended"]}

    def get_end_effector_link(self, planning_group):
        return {"success": True, "planning_group": planning_group, "end_effector_link": "panda_hand"}

    def get_end_effector_pose(self, frame_id, end_effector_link):
        return {
            "success": True,
            "frame_id": frame_id,
            "end_effector_link": end_effector_link,
            "pose": {"position": {"x": 0.4, "y": 0.0, "z": 0.4}},
        }

    def get_gripper_state(self):
        return {
            "success": True,
            "estimated_width": 0.07,
            "finger_joint_positions": {
                "panda_finger_joint1": 0.035,
                "panda_finger_joint2": 0.035,
            },
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
            "raw_plan": {"kind": "named", "target_name": target_name},
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
        if plan_result["raw_plan"]["kind"] == "named":
            self.executed_named_targets.append(plan_result["raw_plan"]["target_name"])
        return {
            "success": True,
            "status": "executed",
            "planning": {"summary": plan_result["summary"]},
            "final_state": {"joint_states": {"panda_joint1": 0.2}},
        }

    def set_gripper_width(self, width, max_effort=0.0):
        self.gripper_widths.append((width, max_effort))
        return {
            "success": True,
            "status": "executed",
            "target_width": width,
            "max_effort": max_effort,
            "final_state": self.get_gripper_state(),
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
    assert "两指之间的目标总开口宽度" in arm_tools.arm_set_gripper_width.description
    assert "不需要 `plan_id`" in arm_tools.arm_move_to_pose_goal.description
    assert "有序步骤列表" in arm_tools.arm_execute_plan.description
    assert "不是任意 LangChain tool dispatcher" in arm_tools.arm_execute_plan.description
    assert "真实硬件急停必须走硬件安全链路" in arm_tools.arm_stop.description


def test_state_tools_return_stable_shapes(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    assert arm_tools.arm_get_joint_states.invoke({})["joint_states"] == {"panda_joint1": 0.1}
    assert arm_tools.arm_get_planning_groups.invoke({})["planning_groups"] == ["panda_arm"]
    assert arm_tools.arm_get_named_targets.invoke({"planning_group": "panda_arm"})["named_targets"] == [
        "home",
        "ready",
        "extended",
    ]
    assert arm_tools.arm_get_end_effector_link.invoke({"planning_group": "panda_arm"})["end_effector_link"] == "panda_hand"
    assert arm_tools.arm_get_end_effector_pose.invoke({"frame_id": "panda_link0"})["frame_id"] == "panda_link0"
    assert arm_tools.arm_get_gripper_state.invoke({})["estimated_width"] == 0.07


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


def test_gripper_tools_open_close_and_set_width(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    opened = arm_tools.arm_open_gripper.invoke({"width": 0.06, "max_effort": 1.0})
    closed = arm_tools.arm_close_gripper.invoke({})
    set_width = arm_tools.arm_set_gripper_width.invoke({"width": 0.03})

    assert opened["success"] is True
    assert closed["success"] is True
    assert set_width["success"] is True
    assert client.gripper_widths == [(0.06, 1.0), (0.0, 0.0), (0.03, 0.0)]


def test_execute_plan_runs_named_targets_in_order(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"id": "go_extended", "label": "移动到 extended", "action": "move_named", "target_name": "extended"},
                {"id": "go_home", "label": "回到 home", "action": "move_named", "target_name": "home"},
            ]
        }
    )

    assert result["success"] is True
    assert result["executed_steps"] == 2
    assert result["skipped_steps"] == 0
    assert client.executed_named_targets == ["extended", "home"]
    assert [step["id"] for step in result["steps"]] == ["go_extended", "go_home"]
    assert result["steps"][0]["input"]["target_name"] == "extended"
    assert result["latest_state"]["joint"]["joint_states"]["panda_joint1"] == 0.2


def test_execute_plan_combines_direct_named_targets_into_one_trajectory(monkeypatch):
    class DirectTrajectoryClient(FakeMoveItClient):
        def __init__(self):
            super().__init__()
            self.executed_raw_plans = []

        def plan_to_named_target(self, planning_group, target_name):
            del planning_group
            offset = 10.0 if target_name == "home" else 0.0
            return {
                "success": True,
                "status": "planned",
                "summary": f"direct:{target_name}",
                "raw_plan": {
                    "adapter": "joint_trajectory_topic",
                    "duration": 1.5,
                    "joint_goal": {
                        f"panda_joint{index}": float(index) + offset
                        for index in range(1, 8)
                    },
                },
            }

        def execute_plan(self, plan_result):
            self.executed_raw_plans.append(plan_result["raw_plan"])
            return {
                "success": True,
                "status": "executed",
                "planning": {"summary": plan_result["summary"]},
                "final_state": {"joint_states": {"panda_joint1": 11.0}},
            }

    client = DirectTrajectoryClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "extended"},
                {"action": "move_named", "target_name": "home"},
            ]
        }
    )

    assert result["success"] is True
    assert result["executed_steps"] == 2
    assert "1 段连续 named target 已合并为 trajectory" in result["summary"]
    assert len(client.executed_raw_plans) == 1
    assert len(client.executed_raw_plans[0]["joint_goals"]) == 2
    assert client.executed_raw_plans[0]["joint_goals"][0]["panda_joint7"] == 7.0
    assert client.executed_raw_plans[0]["joint_goals"][1]["panda_joint7"] == 17.0
    assert result["latest_state"]["joint"]["joint_states"]["panda_joint1"] == 11.0


def test_execute_plan_combines_named_target_segments_inside_mixed_plan(monkeypatch):
    class MixedPlanClient(FakeMoveItClient):
        def __init__(self):
            super().__init__()
            self.executed_raw_plans = []

        def plan_to_named_target(self, planning_group, target_name):
            del planning_group
            offsets = {"ready": 0.0, "extended": 10.0, "transport": 20.0, "home": 30.0}
            return {
                "success": True,
                "status": "planned",
                "summary": f"direct:{target_name}",
                "raw_plan": {
                    "adapter": "joint_trajectory_topic",
                    "duration": 1.5,
                    "joint_goal": {
                        f"panda_joint{index}": float(index) + offsets[target_name]
                        for index in range(1, 8)
                    },
                },
            }

        def execute_plan(self, plan_result):
            self.executed_raw_plans.append(plan_result["raw_plan"])
            return {
                "success": True,
                "status": "executed",
                "planning": {"summary": plan_result["summary"]},
                "final_state": {"joint_states": {"panda_joint1": 31.0}},
            }

    client = MixedPlanClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "ready"},
                {"action": "move_named", "target_name": "extended"},
                {"action": "move_named", "target_name": "transport"},
                {"action": "open_gripper", "width": 0.06},
                {"action": "close_gripper"},
                {"action": "move_named", "target_name": "home"},
            ]
        }
    )

    assert result["success"] is True
    assert result["executed_steps"] == 6
    assert "1 段连续 named target 已合并为 trajectory" in result["summary"]
    assert len(client.executed_raw_plans) == 2
    assert len(client.executed_raw_plans[0]["joint_goals"]) == 3
    assert client.executed_raw_plans[0]["joint_goals"][0]["panda_joint7"] == 7.0
    assert client.executed_raw_plans[0]["joint_goals"][1]["panda_joint7"] == 17.0
    assert client.executed_raw_plans[0]["joint_goals"][2]["panda_joint7"] == 27.0
    assert "joint_goal" in client.executed_raw_plans[1]
    assert client.executed_raw_plans[1]["joint_goal"]["panda_joint7"] == 37.0
    assert client.gripper_widths == [(0.06, 0.0), (0.0, 0.0)]


def test_execute_plan_runs_gripper_steps(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "open_gripper", "width": 0.06, "max_effort": 1.0},
                {"action": "close_gripper"},
                {"action": "set_gripper_width", "width": 0.03},
                {"action": "get_gripper_state"},
            ]
        }
    )

    assert result["success"] is True
    assert result["executed_steps"] == 4
    assert client.gripper_widths == [(0.06, 1.0), (0.0, 0.0), (0.03, 0.0)]
    assert result["latest_state"]["gripper"]["estimated_width"] == 0.07


def test_execute_plan_rejects_unknown_and_invalid_arguments_before_movement(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    unknown = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "extended"},
                {"action": "ros2_topic_pub", "topic": "/panda_arm_controller/joint_trajectory"},
            ]
        }
    )
    invalid_width = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "extended"},
                {"action": "set_gripper_width", "width": 2.0},
            ]
        }
    )
    invalid_frame = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_pose", "x": 0.4, "y": 0.0, "z": 0.4, "frame_id": ""},
            ]
        }
    )

    assert unknown["success"] is False
    assert unknown["status"] == "unsupported_action"
    assert invalid_width["success"] is False
    assert invalid_width["status"] == "invalid_plan"
    assert invalid_width["requested_width"] == 2.0
    assert invalid_frame["success"] is False
    assert "frame_id" in invalid_frame["error"]
    assert client.executed_plans == []
    assert client.gripper_widths == []


def test_execute_plan_rejects_parallel_and_too_many_steps(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    parallel = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "home", "parallel": True},
            ]
        }
    )
    too_many = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "get_joint_states"}
                for _index in range(arm_tools.MAX_ARM_PLAN_STEPS + 1)
            ]
        }
    )

    assert parallel["success"] is False
    assert parallel["status"] == "unsupported_plan"
    assert "并行动作" in parallel["error"]
    assert too_many["success"] is False
    assert too_many["max_steps"] == arm_tools.MAX_ARM_PLAN_STEPS
    assert client.executed_plans == []


def test_execute_plan_stops_after_failure_and_skips_remaining(monkeypatch):
    class FailingSecondTargetClient(FakeMoveItClient):
        def plan_to_named_target(self, planning_group, target_name):
            if target_name == "bad":
                return {"success": False, "error": "planning failed on bad target"}
            return super().plan_to_named_target(planning_group, target_name)

    client = FailingSecondTargetClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"action": "move_named", "target_name": "extended"},
                {"action": "move_named", "target_name": "bad"},
                {"action": "move_named", "target_name": "home"},
            ]
        }
    )

    assert result["success"] is False
    assert result["executed_steps"] == 2
    assert result["skipped_steps"] == 1
    assert client.executed_named_targets == ["extended"]
    assert result["steps"][1]["success"] is False
    assert result["steps"][1]["error"] == "planning failed on bad target"
    assert result["steps"][2]["status"] == "skipped"


def test_execute_plan_result_is_json_serializable_for_terminal_streaming(monkeypatch):
    client = FakeMoveItClient()
    monkeypatch.setattr(arm_tools, "_CLIENT_FACTORY", lambda: client)

    result = arm_tools.arm_execute_plan.invoke(
        {
            "steps": [
                {"id": "state", "label": "读取关节状态", "action": "get_joint_states"},
                {"id": "gripper", "label": "读取夹爪状态", "action": "get_gripper_state"},
            ]
        }
    )

    encoded = json.dumps(result, ensure_ascii=False)

    assert result["success"] is True
    assert "\"steps\"" in encoded
    assert "读取关节状态" in encoded
