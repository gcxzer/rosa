#!/usr/bin/env python3
"""Launch Stretch 3 navigation in MuJoCo without Gazebo."""

from __future__ import annotations

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, Shutdown
from launch.conditions import IfCondition
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterFile, ParameterValue


def _nav2_node(package: str, executable: str, name: str, params_file, *, remappings=None):
    return Node(
        package=package,
        executable=executable,
        name=name,
        output="screen",
        condition=IfCondition(LaunchConfiguration("nav2")),
        parameters=[ParameterFile(params_file, allow_substs=True), {"use_sim_time": True}],
        remappings=remappings or [],
        arguments=["--ros-args", "--log-level", LaunchConfiguration("log_level")],
    )


def _launch_setup(context):
    package_share = get_package_share_directory("rosa_nav_bringup")
    description_file = os.path.join(package_share, "description", "stretch_nav.urdf.xacro")
    controllers_file = os.path.join(package_share, "config", "controllers.yaml")
    nav2_params_file = os.path.join(package_share, "config", "nav2_params.yaml")
    map_file = os.path.join(package_share, "maps", "office_lab.yaml")
    rviz_file = os.path.join(package_share, "rviz", "nav.rviz")
    mujoco_model = os.path.join(package_share, "mujoco", "office_lab_scene.xml")

    robot_description_content = Command(
        [
            PathJoinSubstitution([FindExecutable(name="xacro")]),
            " ",
            description_file,
            " headless:=",
            LaunchConfiguration("headless"),
            " sim_speed_factor:=",
            LaunchConfiguration("sim_speed_factor"),
            " mujoco_model:=",
            mujoco_model,
        ]
    )
    robot_description = {
        "robot_description": ParameterValue(robot_description_content, value_type=str)
    }
    common_params = ParameterFile(nav2_params_file, allow_substs=True)

    nodes = [
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="robot_state_publisher",
            output="both",
            parameters=[robot_description, {"use_sim_time": True}],
        ),
        Node(
            package="mujoco_ros2_control",
            executable="ros2_control_node",
            output="both",
            emulate_tty=True,
            parameters=[{"use_sim_time": True}, ParameterFile(controllers_file, allow_substs=True)],
            remappings=[
                ("robot_description", "/robot_description"),
                ("~/robot_description", "/robot_description"),
            ],
            on_exit=Shutdown(),
        ),
        Node(
            package="controller_manager",
            executable="spawner",
            arguments=[
                "joint_state_broadcaster",
                "--controller-manager",
                "/controller_manager",
                "--param-file",
                controllers_file,
            ],
            output="both",
        ),
        Node(
            package="controller_manager",
            executable="spawner",
            arguments=[
                "diff_drive_controller",
                "--controller-manager",
                "/controller_manager",
                "--param-file",
                controllers_file,
                "--controller-ros-args",
                "--ros-args --remap ~/odom:=/odom",
            ],
            output="both",
        ),
        Node(
            package="nav2_map_server",
            executable="map_server",
            name="map_server",
            output="screen",
            condition=IfCondition(LaunchConfiguration("nav2")),
            parameters=[common_params, {"use_sim_time": True, "yaml_filename": map_file}],
        ),
        _nav2_node("nav2_amcl", "amcl", "amcl", nav2_params_file),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_localization",
            output="screen",
            condition=IfCondition(LaunchConfiguration("nav2")),
            parameters=[
                {"use_sim_time": True, "autostart": LaunchConfiguration("autostart")},
                {"node_names": ["map_server", "amcl"]},
            ],
        ),
        _nav2_node(
            "nav2_controller",
            "controller_server",
            "controller_server",
            nav2_params_file,
            remappings=[("cmd_vel", "cmd_vel_nav")],
        ),
        _nav2_node("nav2_planner", "planner_server", "planner_server", nav2_params_file),
        _nav2_node("nav2_smoother", "smoother_server", "smoother_server", nav2_params_file),
        _nav2_node(
            "nav2_behaviors",
            "behavior_server",
            "behavior_server",
            nav2_params_file,
            remappings=[("cmd_vel", "cmd_vel_nav")],
        ),
        _nav2_node("nav2_bt_navigator", "bt_navigator", "bt_navigator", nav2_params_file),
        _nav2_node(
            "nav2_waypoint_follower",
            "waypoint_follower",
            "waypoint_follower",
            nav2_params_file,
        ),
        _nav2_node(
            "nav2_velocity_smoother",
            "velocity_smoother",
            "velocity_smoother",
            nav2_params_file,
            remappings=[("cmd_vel", "cmd_vel_nav")],
        ),
        _nav2_node(
            "nav2_collision_monitor",
            "collision_monitor",
            "collision_monitor",
            nav2_params_file,
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            output="screen",
            condition=IfCondition(LaunchConfiguration("nav2")),
            parameters=[
                {"use_sim_time": True, "autostart": LaunchConfiguration("autostart")},
                {
                    "node_names": [
                        "controller_server",
                        "planner_server",
                        "smoother_server",
                        "behavior_server",
                        "bt_navigator",
                        "waypoint_follower",
                        "velocity_smoother",
                        "collision_monitor",
                    ]
                },
            ],
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            output="log",
            condition=IfCondition(LaunchConfiguration("rviz")),
            arguments=["-d", rviz_file],
            parameters=[{"use_sim_time": True}],
        ),
    ]
    return nodes


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("headless", default_value="false"),
            DeclareLaunchArgument("rviz", default_value="false"),
            DeclareLaunchArgument("nav2", default_value="true"),
            DeclareLaunchArgument("autostart", default_value="true"),
            DeclareLaunchArgument("sim_speed_factor", default_value="1.0"),
            DeclareLaunchArgument("log_level", default_value="info"),
            OpaqueFunction(function=_launch_setup),
        ]
    )
