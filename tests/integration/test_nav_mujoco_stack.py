"""Opt-in MuJoCo/Nav2 acceptance suite.

Run after sourcing ``scripts/load_nav_ros2_resources.sh``:

    ROSA_NAV_INTEGRATION=1 python3 -m pytest -q tests/integration/test_nav_mujoco_stack.py

Set ``ROSA_NAV_EXTERNAL_STACK=1`` when the launch is already running.
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


if os.environ.get("ROSA_NAV_INTEGRATION") != "1":
    pytest.skip("set ROSA_NAV_INTEGRATION=1 for MuJoCo/Nav2 tests", allow_module_level=True)

import rclpy
from geometry_msgs.msg import TwistStamped
from nav_msgs.msg import Odometry
from rclpy.parameter import Parameter
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage

from nav_agent.places import PlaceCatalog
from nav_agent.runtime import NavigationRuntimeClient, pose_from_xy_yaw


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "resources/nav_agent/rosa_nav_bringup"


def _wait_until(predicate, timeout: float, period: float = 0.1):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(period)
    raise AssertionError(f"condition did not become true in {timeout}s; last={last!r}")


@pytest.fixture(scope="session", autouse=True)
def headless_stack():
    process = None
    if os.environ.get("ROSA_NAV_EXTERNAL_STACK") != "1":
        process = subprocess.Popen(
            [
                "ros2",
                "launch",
                "rosa_nav_bringup",
                "nav_mujoco.launch.py",
                "headless:=true",
                "nav2:=true",
                "rviz:=false",
                "log_level:=warn",
            ],
            cwd=ROOT,
            start_new_session=True,
        )
    client = NavigationRuntimeClient(operation_timeout=8.0, pose_stale_after=3.0)

    def ready():
        result = client.check_readiness()
        return result if result.get("success") else None

    readiness = _wait_until(ready, 90.0, 0.5)
    yield {"client": client, "readiness": readiness}
    client.close()
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)


def _reset() -> None:
    result = subprocess.run(
        ["ros2", "run", "rosa_nav_bringup", "reset_nav_sim.py", "--timeout", "8"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AMCL initial pose re-established" in result.stdout


class Probe:
    def __init__(self):
        self._owns_rclpy = not rclpy.ok()
        if self._owns_rclpy:
            rclpy.init()
        self.node = rclpy.create_node(
            "rosa_nav_acceptance_probe",
            parameter_overrides=[Parameter("use_sim_time", Parameter.Type.BOOL, True)],
        )
        self.clocks: list[float] = []
        self.truth: list[Odometry] = []
        self.odom: list[Odometry] = []
        self.scans: list[LaserScan] = []
        self.tf_edges: dict[tuple[str, str], int] = {}
        self.node.create_subscription(
            Clock,
            "/clock",
            lambda message: self.clocks.append(message.clock.sec + message.clock.nanosec * 1e-9),
            100,
        )
        self.node.create_subscription(Odometry, "/ground_truth/odom", self.truth.append, 20)
        self.node.create_subscription(Odometry, "/odom", self.odom.append, 20)
        self.node.create_subscription(LaserScan, "/scan", self.scans.append, 20)

        def tf_callback(message):
            for transform in message.transforms:
                edge = (transform.header.frame_id, transform.child_frame_id)
                self.tf_edges[edge] = self.tf_edges.get(edge, 0) + 1

        self.node.create_subscription(TFMessage, "/tf", tf_callback, 100)
        self.command = self.node.create_publisher(
            TwistStamped, "/diff_drive_controller/cmd_vel", 10
        )

    def spin_for(self, wall_seconds: float):
        deadline = time.monotonic() + wall_seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.02)

    def wait_data(self):
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not (
            self.clocks and self.truth and self.odom and self.scans
        ):
            rclpy.spin_once(self.node, timeout_sec=0.05)
        assert self.clocks and self.truth and self.odom and self.scans

    def drive(self, *, linear=0.0, angular=0.0, sim_seconds: float):
        self.wait_data()
        start = self.clocks[-1]
        while self.clocks[-1] - start < sim_seconds:
            message = TwistStamped()
            message.header.frame_id = "base_footprint"
            message.header.stamp = self.node.get_clock().now().to_msg()
            message.twist.linear.x = linear
            message.twist.angular.z = angular
            self.command.publish(message)
            rclpy.spin_once(self.node, timeout_sec=0.01)
        settle_start = self.clocks[-1]
        while self.clocks[-1] - settle_start < 0.8:
            message = TwistStamped()
            message.header.frame_id = "base_footprint"
            message.header.stamp = self.node.get_clock().now().to_msg()
            self.command.publish(message)
            rclpy.spin_once(self.node, timeout_sec=0.01)

    def close(self):
        self.node.destroy_node()
        if self._owns_rclpy and rclpy.ok():
            rclpy.shutdown()


def _xy_yaw(message: Odometry):
    position = message.pose.pose.position
    q = message.pose.pose.orientation
    yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
    return position.x, position.y, yaw


def _wait_terminal(client, goal_id: str, timeout: float):
    observed = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get_status(goal_id)
        observed.append(status["status"])
        if status.get("terminal"):
            return status, observed
        time.sleep(0.25)
    raise AssertionError(f"goal {goal_id} did not terminate: {observed[-5:]}")


def test_headless_stack_readiness_and_no_gazebo(headless_stack):
    readiness = headless_stack["readiness"]
    assert readiness["success"] is True
    assert readiness["missing"] == []
    detected = readiness["detected"]
    assert detected["controllers"] == {
        "diff_drive_controller": "active",
        "joint_state_broadcaster": "active",
    }
    assert all(detected["transforms"].values())
    assert all(detected["actions"].values())
    nodes = subprocess.check_output(["ros2", "node", "list"], text=True).lower()
    assert "gazebo" not in nodes


def test_drive_lidar_tf_odom_reset_and_layout_contracts():
    _reset()
    probe = Probe()
    try:
        probe.wait_data()
        wall_start = time.monotonic()
        clock_start = probe.clocks[-1]
        probe.spin_for(3.0)
        rtf = (probe.clocks[-1] - clock_start) / (time.monotonic() - wall_start)
        scan = probe.scans[-1]
        assert len(scan.ranges) == 360
        assert math.isclose(scan.angle_max - scan.angle_min + scan.angle_increment, 2 * math.pi, abs_tol=0.02)
        scan_stamps = [item.header.stamp.sec + item.header.stamp.nanosec * 1e-9 for item in probe.scans[-20:]]
        scan_rate = (len(scan_stamps) - 1) / (scan_stamps[-1] - scan_stamps[0])
        assert scan_rate >= 9.5
        assert abs(scan.ranges[90] - 1.0) <= 0.10
        # Keep enough throughput for Nav2 while allowing for a loaded ARM64 CI
        # host. The exact measured value is recorded by the acceptance runner.
        assert rtf >= 0.65

        before_truth = _xy_yaw(probe.truth[-1])
        before_odom = _xy_yaw(probe.odom[-1])
        assert math.hypot(before_truth[0], before_truth[1] + 2.8) <= 0.05
        assert abs(math.atan2(math.sin(before_truth[2]), math.cos(before_truth[2]))) <= 0.05
        probe.drive(linear=0.2, sim_seconds=5.0)
        after_truth = _xy_yaw(probe.truth[-1])
        after_odom = _xy_yaw(probe.odom[-1])
        truth_dx = after_truth[0] - before_truth[0]
        truth_dy = after_truth[1] - before_truth[1]
        truth_distance = math.hypot(truth_dx, truth_dy)
        lateral_drift = abs(
            -math.sin(before_truth[2]) * truth_dx
            + math.cos(before_truth[2]) * truth_dy
        )
        odom_distance = math.hypot(after_odom[0] - before_odom[0], after_odom[1] - before_odom[1])
        assert abs(truth_distance - 1.0) <= 0.10, truth_distance
        assert lateral_drift <= 0.05, lateral_drift
        assert abs(truth_distance - odom_distance) <= 0.10, (truth_distance, odom_distance)

        _reset()
        probe.spin_for(1.0)
        before = _xy_yaw(probe.truth[-1])
        probe.drive(angular=0.5, sim_seconds=math.pi)
        after = _xy_yaw(probe.truth[-1])
        heading = math.atan2(math.sin(after[2] - before[2]), math.cos(after[2] - before[2]))
        assert abs(heading - math.pi / 2.0) <= 0.10

        probe.spin_for(2.0)
        for edge in (("odom", "base_footprint"), ("base_link", "link_left_wheel"), ("base_link", "link_right_wheel")):
            assert probe.tf_edges.get(edge, 0) > 0
        assert ("odom", "base_link") not in probe.tf_edges
        assert probe.truth[-1].header.frame_id == "odom"
        assert probe.truth[-1].child_frame_id == "base_link"
        assert probe.odom[-1].child_frame_id == "base_footprint"
    finally:
        probe.close()
    _reset()
    result = subprocess.run(
        [str(PACKAGE / "scripts/generate_office_lab.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_end_to_end_goal_failure_cancel_waypoints_and_semantics(headless_stack):
    client = headless_stack["client"]
    catalog = PlaceCatalog(PACKAGE / "config/semantic_places.yaml")
    _reset()

    coordinate = client.start_pose_goal(pose_from_xy_yaw(0.8, -2.8, 0.0))
    coordinate_final, coordinate_states = _wait_terminal(client, coordinate["goal_id"], 35)
    assert "executing" in coordinate_states
    assert coordinate_final["status"] == "succeeded"

    _reset()
    reception = catalog.resolve("前台")["place"]
    named = client.start_pose_goal(
        reception["pose"],
        metadata={"semantic_destination": {"id": reception["id"], "display_name": reception["display_name"], "resolved_pose": reception["pose"]}},
    )
    named_final, _ = _wait_terminal(client, named["goal_id"], 55)
    assert named_final["status"] == "succeeded"
    assert named_final["metadata"]["semantic_destination"]["id"] == "reception"

    _reset()
    occupied = client.start_pose_goal(pose_from_xy_yaw(-2.8, -1.35, 0.0))
    occupied_final, _ = _wait_terminal(client, occupied["goal_id"], 45)
    assert occupied_final["status"] == "failed"

    _reset()
    cancellable = client.start_pose_goal(catalog.resolve("inspection")["place"]["pose"])
    time.sleep(2.0)
    assert client.get_status(cancellable["goal_id"])["status"] == "executing"
    cancelled = client.cancel(cancellable["goal_id"])
    assert cancelled["status"] == "cancelled"
    assert client.cancel(cancellable["goal_id"])["status"] == "cancelled"

    _reset()
    waypoint_poses = [pose_from_xy_yaw(0.8, -2.8, 0.0), pose_from_xy_yaw(0.0, -2.8, math.pi)]
    mission = client.start_waypoints(waypoint_poses, failure_policy="stop")
    mission_final, _ = _wait_terminal(client, mission["goal_id"], 55)
    assert mission_final["status"] == "succeeded"
    assert mission_final["target"] == waypoint_poses
    assert mission_final["result"]["completed_waypoints"] == 2

    rejected = catalog.resolve_mission(["reception", "not configured", "charging"])
    assert rejected["status"] == "rejected"
    assert rejected["invalid_stop"]["index"] == 1
