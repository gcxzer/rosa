"""Lazy ROS 2/Nav2 adapter for :mod:`nav_agent.runtime`.

All ROS imports deliberately live inside ``ROS2NavigationAdapter.__init__``.
"""

from __future__ import annotations

import math
import time
from typing import Any


class ROS2NavigationAdapter:
    def __init__(self, *, timeout: float = 3.0) -> None:
        import rclpy
        from action_msgs.msg import GoalStatus
        from controller_manager_msgs.srv import ListControllers
        from geometry_msgs.msg import PoseStamped
        from lifecycle_msgs.srv import GetState
        from nav2_msgs.action import FollowWaypoints, NavigateToPose
        from rclpy.action import ActionClient
        from rclpy.duration import Duration
        from rclpy.parameter import Parameter
        from rclpy.time import Time
        from tf2_ros import Buffer, TransformListener

        self.rclpy = rclpy
        self.GoalStatus = GoalStatus
        self.ListControllers = ListControllers
        self.PoseStamped = PoseStamped
        self.GetState = GetState
        self.FollowWaypoints = FollowWaypoints
        self.NavigateToPose = NavigateToPose
        self.Duration = Duration
        self.Time = Time
        self.timeout = float(timeout)
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node(
            "rosa_nav_runtime",
            parameter_overrides=[Parameter("use_sim_time", Parameter.Type.BOOL, True)],
        )
        self.tf_buffer = Buffer(node=self.node)
        self.tf_listener = TransformListener(self.tf_buffer, self.node, spin_thread=False)
        self.pose_client = ActionClient(self.node, NavigateToPose, "/navigate_to_pose")
        self.waypoint_client = ActionClient(self.node, FollowWaypoints, "/follow_waypoints")
        self._handles: dict[int, dict[str, Any]] = {}

    def check_readiness(self, timeout: float) -> dict[str, Any]:
        graph_deadline = time.monotonic() + timeout
        required_topics = {"/clock", "/joint_states", "/scan", "/odom", "/map"}
        topics: set[str] = set()
        # A newly-created DDS participant may need a few seconds to discover a
        # large Nav2 graph. Keep the wait bounded but do not classify the first
        # partial graph snapshot as a missing stack.
        while time.monotonic() < graph_deadline and not required_topics.issubset(topics):
            self._discover(min(0.2, max(0.0, graph_deadline - time.monotonic())))
            topics.update(name for name, _types in self.node.get_topic_names_and_types())

        controllers = self._controllers(timeout)
        lifecycle = {
            "map_server": self._lifecycle_state("map_server", timeout),
            "amcl": self._lifecycle_state("amcl", timeout),
        }
        actions = {
            "/navigate_to_pose": self.pose_client.wait_for_server(
                timeout_sec=min(timeout, 0.75)
            ),
            "/follow_waypoints": self.waypoint_client.wait_for_server(
                timeout_sec=min(timeout, 0.75)
            ),
        }
        transform_pairs = {
            "map->odom": ("map", "odom"),
            "odom->base_footprint": ("odom", "base_footprint"),
            "base_footprint->base_link": ("base_footprint", "base_link"),
            "base_link->laser": ("base_link", "laser"),
        }
        transforms = {name: False for name in transform_pairs}
        transform_deadline = time.monotonic() + timeout
        while time.monotonic() < transform_deadline and not all(transforms.values()):
            self.rclpy.spin_once(self.node, timeout_sec=0.03)
            for name, (target, source) in transform_pairs.items():
                if not transforms[name]:
                    transforms[name] = self._has_transform(target, source)
        now = self.node.get_clock().now().nanoseconds / 1e9
        missing: list[str] = []
        missing.extend(f"{topic} topic" for topic in sorted(required_topics - topics))
        for controller in ("joint_state_broadcaster", "diff_drive_controller"):
            if controllers.get(controller) != "active":
                missing.append(f"active {controller}")
        if now <= 0.0:
            missing.append("advancing simulation time")
        for name, state in lifecycle.items():
            if state != "active":
                missing.append(f"active {name} lifecycle state")
        missing.extend(f"{edge} transform" for edge, available in transforms.items() if not available)
        missing.extend(f"{name} action" for name, available in actions.items() if not available)
        return {
            "success": not missing,
            "missing": missing,
            "detected": {
                "topics": sorted(topics),
                "controllers": controllers,
                "lifecycle": lifecycle,
                "transforms": transforms,
                "actions": actions,
                "simulation_time": now,
            },
            "summary": "Navigation stack is ready." if not missing else "Navigation stack is incomplete.",
        }

    def lookup_pose(self, *, target_frame: str, source_frame: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        transform = None
        error = "transform unavailable"
        while time.monotonic() < deadline:
            self.rclpy.spin_once(self.node, timeout_sec=0.03)
            try:
                transform = self.tf_buffer.lookup_transform(target_frame, source_frame, self.Time())
                break
            except Exception as exc:
                error = str(exc)
        if transform is None:
            return {"success": False, "status": "timeout", "error": error}
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        stamp = transform.header.stamp.sec + transform.header.stamp.nanosec * 1e-9
        now = self.node.get_clock().now().nanoseconds / 1e9
        return {
            "success": True,
            "position": {"x": translation.x, "y": translation.y, "z": translation.z},
            "orientation": {"x": rotation.x, "y": rotation.y, "z": rotation.z, "w": rotation.w},
            "timestamp": stamp,
            "age": max(0.0, now - stamp),
            "localization_state": self._lifecycle_state("amcl", min(timeout, 2.0)),
        }

    def _pose_message(self, pose: dict[str, Any]) -> Any:
        message = self.PoseStamped()
        message.header.frame_id = "map"
        message.header.stamp = self.node.get_clock().now().to_msg()
        message.pose.position.x = float(pose["position"]["x"])
        message.pose.position.y = float(pose["position"]["y"])
        message.pose.position.z = float(pose["position"].get("z", 0.0))
        for key in ("x", "y", "z", "w"):
            setattr(message.pose.orientation, key, float(pose["orientation"][key]))
        return message

    def _submit(self, kind: str, poses: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
        client = self.pose_client if kind == "pose" else self.waypoint_client
        if not client.wait_for_server(timeout_sec=timeout):
            return {"accepted": False, "reason": f"{kind} action server unavailable"}
        if kind == "pose":
            goal = self.NavigateToPose.Goal()
            goal.pose = self._pose_message(poses[0])
        else:
            goal = self.FollowWaypoints.Goal()
            goal.poses = [self._pose_message(pose) for pose in poses]
        holder: dict[str, Any] = {"kind": kind, "feedback": {}, "poses": poses}

        def feedback_callback(message: Any) -> None:
            feedback = message.feedback
            if kind == "pose":
                holder["feedback"] = {
                    "distance_remaining": float(feedback.distance_remaining),
                    "navigation_time": feedback.navigation_time.sec + feedback.navigation_time.nanosec * 1e-9,
                    "estimated_time_remaining": (
                        feedback.estimated_time_remaining.sec
                        + feedback.estimated_time_remaining.nanosec * 1e-9
                    ),
                    "number_of_recoveries": int(feedback.number_of_recoveries),
                }
            else:
                index = int(feedback.current_waypoint)
                holder["feedback"] = {
                    "current_waypoint": index,
                    "completed_waypoints": max(0, index),
                    "waypoint_count": len(poses),
                }

        future = client.send_goal_async(goal, feedback_callback=feedback_callback)
        if not self._spin_until(future, timeout) or future.result() is None:
            return {"accepted": False, "reason": "timed out waiting for Nav2 goal acknowledgement"}
        goal_handle = future.result()
        if not goal_handle.accepted:
            return {"accepted": False, "reason": "Nav2 rejected the goal"}
        holder["goal_handle"] = goal_handle
        holder["result_future"] = goal_handle.get_result_async()
        handle = id(holder)
        self._handles[handle] = holder
        return {"accepted": True, "handle": handle}

    def submit_pose(self, pose: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        return self._submit("pose", [pose], timeout)

    def submit_waypoints(self, poses: list[dict[str, Any]], *, timeout: float) -> dict[str, Any]:
        return self._submit("waypoints", poses, timeout)

    def poll(self, handle: int) -> dict[str, Any]:
        holder = self._handles.get(handle)
        if holder is None:
            return {"state": "unknown", "result": {"error": "Nav2 handle is unknown"}}
        self.rclpy.spin_once(self.node, timeout_sec=0.0)
        future = holder["result_future"]
        if not future.done():
            return {"state": "executing", "feedback": dict(holder.get("feedback") or {})}
        wrapped = future.result()
        status = wrapped.status if wrapped is not None else self.GoalStatus.STATUS_UNKNOWN
        mapping = {
            self.GoalStatus.STATUS_SUCCEEDED: "succeeded",
            self.GoalStatus.STATUS_ABORTED: "failed",
            self.GoalStatus.STATUS_CANCELED: "cancelled",
        }
        state = mapping.get(status, "unknown")
        result: dict[str, Any] = {"nav2_status": int(status)}
        if holder["kind"] == "waypoints" and wrapped is not None:
            missed = getattr(wrapped.result, "missed_waypoints", [])
            result.update(
                {
                    "waypoint_count": len(holder["poses"]),
                    "completed_waypoints": len(holder["poses"]) - len(missed),
                    "failed_waypoints": [int(item.index) for item in missed],
                    "failures": [str(item.error_msg) for item in missed],
                }
            )
        return {"state": state, "feedback": dict(holder.get("feedback") or {}), "result": result}

    def cancel(self, handle: int, *, timeout: float) -> dict[str, Any]:
        holder = self._handles.get(handle)
        if holder is None:
            return {"cancelled": False, "reason": "Nav2 handle is unknown"}
        future = holder["goal_handle"].cancel_goal_async()
        if not self._spin_until(future, timeout) or future.result() is None:
            return {"cancelled": False, "reason": "cancellation acknowledgement timed out"}
        accepted = bool(future.result().goals_canceling)
        return {"cancelled": accepted, "result": {"nav2_cancel_acknowledged": accepted}}

    def close(self) -> None:
        for client in (self.pose_client, self.waypoint_client):
            client.destroy()
        self.node.destroy_node()

    def _spin_until(self, future: Any, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            self.rclpy.spin_once(self.node, timeout_sec=min(0.05, max(0.0, deadline - time.monotonic())))
        return future.done()

    def _discover(self, seconds: float = 0.25) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.rclpy.spin_once(self.node, timeout_sec=0.02)

    def _lifecycle_state(self, node_name: str, timeout: float) -> str:
        client = self.node.create_client(self.GetState, f"/{node_name}/get_state")
        try:
            if not client.wait_for_service(timeout_sec=min(timeout, 1.0)):
                return "unavailable"
            future = client.call_async(self.GetState.Request())
            if not self._spin_until(future, timeout):
                return "timeout"
            response = future.result()
            return response.current_state.label if response is not None else "error"
        finally:
            self.node.destroy_client(client)

    def _controllers(self, timeout: float) -> dict[str, str]:
        client = self.node.create_client(self.ListControllers, "/controller_manager/list_controllers")
        try:
            if not client.wait_for_service(timeout_sec=min(timeout, 0.75)):
                return {}
            future = client.call_async(self.ListControllers.Request())
            if not self._spin_until(future, timeout) or future.result() is None:
                return {}
            return {controller.name: controller.state for controller in future.result().controller}
        finally:
            self.node.destroy_client(client)

    def _has_transform(self, target: str, source: str) -> bool:
        try:
            return bool(self.tf_buffer.can_transform(
                target,
                source,
                self.Time(),
                timeout=self.Duration(seconds=0.15),
            ))
        except Exception:
            return False
