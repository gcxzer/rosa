from __future__ import annotations

import pytest

from nav_agent import tools as nav_tools
from nav_agent.places import PlaceCatalog


class FakeNavigationClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.pose_goals = []
        self.missions = []
        self.status_queries = []
        self.cancellations = []

    def check_readiness(self):
        return {
            "success": self.ready,
            "missing": [] if self.ready else ["active amcl lifecycle state"],
            "detected": {"actions": {"/navigate_to_pose": self.ready}},
        }

    def get_current_pose(self):
        return {"success": True, "status": "localized", "frame_id": "map", "yaw": 0.0}

    def start_pose_goal(self, pose, **kwargs):
        self.pose_goals.append((pose, kwargs))
        return {
            "success": True,
            "status": "accepted",
            "goal_id": "nav-pose",
            "target": pose,
            "metadata": kwargs.get("metadata", {}),
        }

    def start_waypoints(self, poses, **kwargs):
        self.missions.append((list(poses), kwargs))
        return {
            "success": True,
            "status": "accepted",
            "goal_id": "nav-mission",
            "target": poses,
            "metadata": kwargs.get("metadata", {}),
        }

    def get_status(self, goal_id=None):
        self.status_queries.append(goal_id)
        return {"success": True, "status": "executing", "goal_id": goal_id or "nav-active"}

    def cancel(self, goal_id=None):
        self.cancellations.append(goal_id)
        return {"success": True, "status": "cancelled", "goal_id": goal_id or "nav-active"}


@pytest.fixture(autouse=True)
def reset_tool_providers(monkeypatch):
    client = FakeNavigationClient()
    monkeypatch.setattr(nav_tools, "_CLIENT_PROVIDER", lambda: client)
    monkeypatch.setattr(nav_tools, "_CATALOG", None)
    monkeypatch.setattr(nav_tools, "_CATALOG_FACTORY", PlaceCatalog)
    return client


def test_tools_expose_consistent_langchain_schemas() -> None:
    tools = [
        nav_tools.nav_check_readiness,
        nav_tools.nav_get_current_pose,
        nav_tools.nav_list_destinations,
        nav_tools.nav_resolve_destination,
        nav_tools.nav_start_pose_goal,
        nav_tools.nav_start_named_goal,
        nav_tools.nav_get_goal_status,
        nav_tools.nav_cancel_goal,
        nav_tools.nav_start_waypoint_mission,
        nav_tools.nav_start_semantic_mission,
    ]
    assert len({item.name for item in tools}) == len(tools)
    assert set(nav_tools.nav_start_pose_goal.args_schema.model_fields) >= {"x", "y", "yaw", "frame_id"}
    assert "waypoints" in nav_tools.nav_start_waypoint_mission.args_schema.model_fields
    assert "destinations" in nav_tools.nav_start_semantic_mission.args_schema.model_fields
    assert "never guesses" in nav_tools.nav_get_current_pose.description
    assert "no motion" in nav_tools.nav_start_semantic_mission.description


def test_readiness_refusal_prevents_all_motion(monkeypatch) -> None:
    client = FakeNavigationClient(ready=False)
    monkeypatch.setattr(nav_tools, "_CLIENT_PROVIDER", lambda: client)

    pose = nav_tools.nav_start_pose_goal.invoke({"x": 0.0, "y": -2.0})
    named = nav_tools.nav_start_named_goal.invoke({"name": "charging"})
    mission = nav_tools.nav_start_semantic_mission.invoke({"destinations": ["reception", "charging"]})

    assert {pose["status"], named["status"], mission["status"]} == {"not_ready"}
    assert client.pose_goals == []
    assert client.missions == []


def test_state_listing_resolution_status_and_cancel(reset_tool_providers) -> None:
    client = reset_tool_providers
    assert nav_tools.nav_check_readiness.invoke({})["success"] is True
    assert nav_tools.nav_get_current_pose.invoke({})["frame_id"] == "map"
    destinations = nav_tools.nav_list_destinations.invoke({})
    assert [item["id"] for item in destinations["destinations"]] == [
        "reception",
        "storage",
        "inspection",
        "charging",
    ]
    assert nav_tools.nav_resolve_destination.invoke({"name": "充电站"})["place"]["id"] == "charging"
    assert nav_tools.nav_get_goal_status.invoke({"goal_id": "nav-1"})["status"] == "executing"
    assert nav_tools.nav_cancel_goal.invoke({"goal_id": "nav-1"})["status"] == "cancelled"
    assert client.status_queries == ["nav-1"]
    assert client.cancellations == ["nav-1"]


def test_coordinate_and_named_goals_retain_original_semantics(reset_tool_providers) -> None:
    client = reset_tool_providers
    coordinate = nav_tools.nav_start_pose_goal.invoke({"x": 0.0, "y": -2.0, "yaw": 0.5})
    named = nav_tools.nav_start_named_goal.invoke({"name": "充电站"})

    assert coordinate["status"] == "accepted"
    assert coordinate["target"]["yaw"] == 0.5
    assert named["metadata"]["semantic_destination"]["id"] == "charging"
    assert named["metadata"]["semantic_destination"]["resolved_pose"] == named["target"]
    assert len(client.pose_goals) == 2


def test_occupied_coordinate_and_atomic_unknown_semantic_mission_submit_nothing(reset_tool_providers) -> None:
    client = reset_tool_providers

    occupied = nav_tools.nav_start_pose_goal.invoke({"x": -2.8, "y": -1.35})
    unknown = nav_tools.nav_start_semantic_mission.invoke(
        {"destinations": ["reception", "moon base", "charging"]}
    )

    assert occupied["status"] == "rejected"
    assert occupied["validation"]["status"] == "occupied"
    assert unknown["status"] == "rejected"
    assert unknown["invalid_stop"]["index"] == 1
    assert client.pose_goals == []
    assert client.missions == []


def test_coordinate_and_semantic_waypoints_preserve_order_and_metadata(reset_tool_providers) -> None:
    client = reset_tool_providers
    coordinate = nav_tools.nav_start_waypoint_mission.invoke(
        {
            "waypoints": [{"x": 0.0, "y": -2.0}, {"x": 1.0, "y": -2.0, "yaw": 1.0}],
            "failure_policy": "continue",
        }
    )
    semantic = nav_tools.nav_start_semantic_mission.invoke(
        {"destinations": ["前台", "inspection", "充电站"]}
    )

    assert coordinate["status"] == "accepted"
    assert [pose["position"]["x"] for pose in client.missions[0][0]] == [0.0, 1.0]
    assert client.missions[0][1]["failure_policy"] == "continue"
    assert semantic["status"] == "accepted"
    semantic_ids = [item["id"] for item in semantic["metadata"]["semantic_stops"]]
    assert semantic_ids == ["reception", "inspection", "charging"]
