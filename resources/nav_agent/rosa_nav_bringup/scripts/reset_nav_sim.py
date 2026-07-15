#!/usr/bin/env python3
"""Reset MuJoCo to the stow keyframe and restore AMCL's initial pose."""

from __future__ import annotations

import argparse
import math
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=-2.8)
    parser.add_argument("--yaw", type=float, default=0.0)
    args = parser.parse_args()

    try:
        import rclpy
        from geometry_msgs.msg import PoseWithCovarianceStamped, TwistStamped
        from mujoco_ros2_control_msgs.srv import ResetWorld
        from rclpy.parameter import Parameter
        from std_srvs.srv import Empty
    except ImportError as exc:
        print(f"ROS 2 Python packages are unavailable: {exc}", file=sys.stderr)
        return 2

    rclpy.init()
    node = rclpy.create_node(
        "rosa_nav_reset",
        parameter_overrides=[Parameter("use_sim_time", Parameter.Type.BOOL, True)],
    )
    try:
        nav_stop = node.create_publisher(TwistStamped, "/cmd_vel_nav", 10)
        controller_stop = node.create_publisher(
            TwistStamped, "/diff_drive_controller/cmd_vel", 10
        )
        stop_deadline = time.monotonic() + min(args.timeout, 2.0)
        while time.monotonic() < stop_deadline and (
            nav_stop.get_subscription_count() == 0
            or controller_stop.get_subscription_count() == 0
        ):
            rclpy.spin_once(node, timeout_sec=0.05)
        for _ in range(10):
            stop = TwistStamped()
            stop.header.frame_id = "base_footprint"
            stop.header.stamp = node.get_clock().now().to_msg()
            nav_stop.publish(stop)
            controller_stop.publish(stop)
            rclpy.spin_once(node, timeout_sec=0.05)

        client = node.create_client(ResetWorld, "/mujoco_ros2_control_node/reset_world")
        if not client.wait_for_service(timeout_sec=args.timeout):
            print("MuJoCo reset service is unavailable", file=sys.stderr)
            return 1
        request = ResetWorld.Request()
        request.keyframe = "stow"
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=args.timeout)
        if not future.done() or future.result() is None or not future.result().success:
            print("MuJoCo reset failed", file=sys.stderr)
            return 1

        # Let wheel feedback and odom observe the reset before AMCL receives its
        # new map-frame anchor. This keeps map->odom consistent after wheel
        # joint positions jump back to the keyframe values.
        settle_deadline = time.monotonic() + 0.5
        while time.monotonic() < settle_deadline:
            rclpy.spin_once(node, timeout_sec=0.05)

        publisher = node.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        localized = []
        node.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", localized.append, 10
        )
        discovery_deadline = time.monotonic() + min(args.timeout, 3.0)
        while time.monotonic() < discovery_deadline and publisher.get_subscription_count() == 0:
            rclpy.spin_once(node, timeout_sec=0.05)
        message = PoseWithCovarianceStamped()
        message.header.frame_id = "map"
        # A zero stamp asks AMCL/TF for the latest transform and avoids a small
        # future-extrapolation race between /clock and the odom broadcaster.
        message.pose.pose.position.x = args.x
        message.pose.pose.position.y = args.y
        message.pose.pose.orientation.z = math.sin(args.yaw / 2.0)
        message.pose.pose.orientation.w = math.cos(args.yaw / 2.0)
        message.pose.covariance[0] = 0.04
        message.pose.covariance[7] = 0.04
        message.pose.covariance[35] = 0.02
        for _ in range(20):
            publisher.publish(message)
            rclpy.spin_once(node, timeout_sec=0.05)

        update = node.create_client(Empty, "/request_nomotion_update")
        if update.wait_for_service(timeout_sec=min(args.timeout, 1.0)):
            update_future = update.call_async(Empty.Request())
            rclpy.spin_until_future_complete(node, update_future, timeout_sec=min(args.timeout, 1.0))

        verify_deadline = time.monotonic() + min(args.timeout, 3.0)
        while time.monotonic() < verify_deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
            if localized:
                pose = localized[-1].pose.pose
                if math.hypot(pose.position.x - args.x, pose.position.y - args.y) <= 0.35:
                    print(future.result().message)
                    print("AMCL initial pose re-established.")
                    return 0
        print("MuJoCo reset succeeded, but AMCL did not confirm the initial pose", file=sys.stderr)
        return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
