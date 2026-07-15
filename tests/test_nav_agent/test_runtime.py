from __future__ import annotations

import math
import sys

from nav_agent.runtime import NavigationRuntimeClient, pose_from_xy_yaw


class FakeAdapter:
    def __init__(self) -> None:
        self.ready = True
        self.pose = {
            "success": True,
            "position": {"x": 1.0, "y": 2.0, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": math.sin(0.25), "w": math.cos(0.25)},
            "timestamp": 12.0,
            "age": 0.1,
            "localization_state": "active",
        }
        self.accept = True
        self.handles: dict[int, dict] = {}
        self.submitted = []
        self.cancelled = []
        self.closed = False

    def check_readiness(self, timeout):
        del timeout
        return {
            "success": self.ready,
            "missing": [] if self.ready else ["active amcl lifecycle state"],
            "detected": {"topics": ["/clock", "/scan"]},
        }

    def lookup_pose(self, **kwargs):
        del kwargs
        return dict(self.pose)

    def submit_pose(self, pose, *, timeout):
        del timeout
        self.submitted.append(("pose", pose))
        if not self.accept:
            return {"accepted": False, "reason": "planner rejected it"}
        handle = len(self.handles) + 1
        self.handles[handle] = {"state": "executing", "feedback": {"distance_remaining": 1.2}}
        return {"accepted": True, "handle": handle}

    def submit_waypoints(self, poses, *, timeout):
        del timeout
        self.submitted.append(("waypoints", list(poses)))
        handle = len(self.handles) + 1
        self.handles[handle] = {"state": "executing", "feedback": {"current_waypoint": 0}}
        return {"accepted": True, "handle": handle}

    def poll(self, handle):
        return dict(self.handles[handle])

    def cancel(self, handle, *, timeout):
        del timeout
        self.cancelled.append(handle)
        self.handles[handle] = {"state": "cancelled", "result": {"cancelled": True}}
        return {"cancelled": True, "result": {"cancelled": True}}

    def close(self):
        self.closed = True


def test_import_and_lazy_construction_do_not_require_ros() -> None:
    before = set(sys.modules)
    client = NavigationRuntimeClient(adapter_factory=lambda: FakeAdapter())

    assert client._adapter is None
    assert "rclpy" not in set(sys.modules) - before
    assert client.check_readiness()["success"] is True
    assert isinstance(client._adapter, FakeAdapter)


def test_readiness_and_pose_are_normalized_and_bounded() -> None:
    adapter = FakeAdapter()
    client = NavigationRuntimeClient(adapter=adapter, pose_stale_after=1.0)

    pose = client.get_current_pose()
    assert pose["success"] is True
    assert pose["frame_id"] == "map"
    assert pose["yaw"] == 0.5
    assert math.isclose(sum(value * value for value in pose["orientation"].values()), 1.0)

    adapter.pose["age"] = 2.0
    stale = client.get_current_pose()
    assert stale["success"] is False
    assert stale["status"] == "stale"

    adapter.pose = {"success": False, "status": "timeout", "error": "no transform"}
    missing = client.get_current_pose()
    assert missing["status"] == "timeout"
    assert missing["frame_id"] == "map"


def test_single_goal_busy_poll_terminal_history_and_rejection() -> None:
    adapter = FakeAdapter()
    client = NavigationRuntimeClient(adapter=adapter, history_limit=1)
    target = pose_from_xy_yaw(1, 0, 0)

    accepted = client.start_pose_goal(target, metadata={"original": "one"})
    assert accepted["success"] is True
    assert accepted["status"] == "accepted"
    assert accepted["goal_id"].startswith("nav-")

    busy = client.start_pose_goal(pose_from_xy_yaw(2, 0, 0))
    assert busy["status"] == "busy"
    assert busy["active_goal_id"] == accepted["goal_id"]

    executing = client.get_status(accepted["goal_id"])
    assert executing["status"] == "executing"
    assert executing["feedback"]["distance_remaining"] == 1.2
    handle = client._tasks[accepted["goal_id"]].backend_handle
    adapter.handles[handle] = {"state": "succeeded", "result": {"code": 0}}
    succeeded = client.get_status(accepted["goal_id"])
    assert succeeded["status"] == "succeeded"
    assert succeeded["target"] == target
    assert succeeded["metadata"] == {"original": "one"}

    second = client.start_pose_goal(pose_from_xy_yaw(2, 0, 0))
    second_handle = client._tasks[second["goal_id"]].backend_handle
    adapter.handles[second_handle] = {"state": "failed", "result": {"error": "blocked"}}
    assert client.get_status(second["goal_id"])["status"] == "failed"
    assert client.get_status(accepted["goal_id"])["status"] == "unknown"

    adapter.accept = False
    rejected = client.start_pose_goal(target)
    assert rejected == {
        "success": False,
        "status": "rejected",
        "target": target,
        "error": "planner rejected it",
    }


def test_waypoint_order_failure_metadata_cancellation_timeout_and_cleanup() -> None:
    adapter = FakeAdapter()
    client = NavigationRuntimeClient(adapter=adapter)
    poses = [pose_from_xy_yaw(0, 0, 0), pose_from_xy_yaw(1, 0, 0)]

    mission = client.start_waypoints(poses, failure_policy="continue", metadata={"mission": "lab"})
    assert adapter.submitted[-1] == ("waypoints", poses)
    assert mission["metadata"]["failure_policy"] == "continue"
    handle = client._tasks[mission["goal_id"]].backend_handle
    adapter.handles[handle] = {
        "state": "failed",
        "feedback": {"current_waypoint": 1},
        "result": {"failed_waypoints": [1], "completed_waypoints": 1},
    }
    failed = client.get_status(mission["goal_id"])
    assert failed["status"] == "failed"
    assert failed["result"]["failed_waypoints"] == [1]

    active = client.start_pose_goal(poses[0])
    cancelled = client.cancel(active["goal_id"])
    assert cancelled["status"] == "cancelled"
    assert client.cancel(active["goal_id"])["status"] == "cancelled"
    assert adapter.cancelled.count(client._tasks[active["goal_id"]].backend_handle) == 1

    timed = client.start_pose_goal(poses[0], task_timeout=-1)
    assert client.get_status(timed["goal_id"])["status"] == "timeout"

    final_active = client.start_pose_goal(poses[0])
    final_handle = client._tasks[final_active["goal_id"]].backend_handle
    client.close()
    assert final_handle in adapter.cancelled
    assert adapter.closed is True


def test_invalid_waypoint_contracts_are_rejected() -> None:
    client = NavigationRuntimeClient(adapter=FakeAdapter())
    assert client.start_waypoints([pose_from_xy_yaw(0, 0, 0)])["status"] == "rejected"
    assert client.start_waypoints(
        [pose_from_xy_yaw(0, 0, 0), pose_from_xy_yaw(1, 0, 0)],
        failure_policy="invent",
    )["status"] == "rejected"
