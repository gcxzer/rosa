import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterFile
from launch_ros.substitutions import FindPackageShare
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    package_name = "moveit_resources_panda_moveit_config"
    package_share = get_package_share_directory(package_name)

    # ros2_controllers.yaml 在 Jazzy 和 Humble 上夹爪 controller 名字不同。
    # 如果存在按 ROS_DISTRO 命名的覆盖文件，就优先使用覆盖文件；否则使用默认配置。
    controllers_path = os.path.join(package_share, "config", "ros2_controllers.yaml")
    ros_distro = os.environ.get("ROS_DISTRO", "")
    if ros_distro:
        stem, ext = os.path.splitext(controllers_path)
        distro_controllers_path = f"{stem}.{ros_distro}{ext}"
        if os.path.isfile(distro_controllers_path):
            controllers_path = distro_controllers_path

    # MuJoCo 资源在 colcon install 时会被安装到当前 package share 下。
    # 这样 launch 不依赖用户当前工作目录，agent、终端和 ros2 launch 都能找到同一个 MJCF。
    mujoco_model = PathJoinSubstitution(
        [
            FindPackageShare(package_name),
            "mujoco",
            "franka_emika_panda",
            "scene_moveit.xml",
        ]
    )

    rviz_config = PathJoinSubstitution(
        [
            FindPackageShare(package_name),
            "launch",
            LaunchConfiguration("rviz_config"),
        ]
    )

    # MoveIt 仍然读取 URDF/SRDF 做规划；只是 ros2_control hardware 从 mock_components
    # 切换成 mujoco。mujoco_model/headless/sim_speed_factor 会进入 URDF 的 <ros2_control>。
    moveit_config = (
        MoveItConfigsBuilder("moveit_resources_panda")
        .robot_description(
            file_path="config/panda.urdf.xacro",
            mappings={
                "ros2_control_hardware_type": "mujoco",
                "mujoco_model": mujoco_model,
                "headless": LaunchConfiguration("headless"),
                "sim_speed_factor": LaunchConfiguration("sim_speed_factor"),
            },
        )
        .robot_description_semantic(file_path="config/panda.srdf")
        .planning_scene_monitor(
            publish_robot_description=True,
            publish_robot_description_semantic=True,
        )
        .trajectory_execution(file_path="config/gripper_moveit_controllers.yaml")
        .planning_pipelines(
            pipelines=["ompl", "chomp", "pilz_industrial_motion_planner", "stomp"]
        )
        .to_moveit_configs()
    )

    move_group_node = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[moveit_config.to_dict(), {"use_sim_time": True}],
        arguments=["--ros-args", "--log-level", "info"],
    )

    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="log",
        arguments=["-d", rviz_config],
        parameters=[
            moveit_config.robot_description,
            moveit_config.robot_description_semantic,
            moveit_config.planning_pipelines,
            moveit_config.robot_description_kinematics,
            moveit_config.joint_limits,
            {"use_sim_time": True},
        ],
        condition=IfCondition(LaunchConfiguration("rviz")),
    )

    static_tf_node = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="static_transform_publisher",
        output="log",
        arguments=["0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "world", "panda_link0"],
    )

    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="both",
        parameters=[moveit_config.robot_description, {"use_sim_time": True}],
    )

    # mujoco_ros2_control 提供自己的 ros2_control_node。它会读取 /robot_description
    # 里的 MujocoSystemInterface 配置，打开 MuJoCo Simulate 窗口，并暴露 controller_manager。
    mujoco_control_node = Node(
        package="mujoco_ros2_control",
        executable="ros2_control_node",
        name="mujoco_ros2_control_node",
        emulate_tty=True,
        output="both",
        parameters=[
            {"use_sim_time": True},
            ParameterFile(controllers_path),
        ],
        remappings=(
            [("~/robot_description", "/robot_description")]
            if os.environ.get("ROS_DISTRO") == "humble"
            else []
        ),
        on_exit=Shutdown(),
    )

    joint_state_broadcaster_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[
            "joint_state_broadcaster",
            "--controller-manager",
            "/controller_manager",
            "--param-file",
            controllers_path,
        ],
        output="both",
    )

    panda_arm_controller_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[
            "panda_arm_controller",
            "--controller-manager",
            "/controller_manager",
            "--param-file",
            controllers_path,
        ],
        output="both",
    )

    panda_hand_controller_spawner = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[
            "panda_hand_controller",
            "--controller-manager",
            "/controller_manager",
            "--param-file",
            controllers_path,
        ],
        output="both",
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "headless",
                default_value="false",
                description="true 时不打开 MuJoCo Simulate 窗口，只运行仿真后端。",
            ),
            DeclareLaunchArgument(
                "rviz",
                default_value="true",
                description="true 时同时打开 RViz，方便查看 MoveIt planning scene。",
            ),
            DeclareLaunchArgument(
                "rviz_config",
                default_value="moveit.rviz",
                description="RViz 配置文件名，默认使用本 package 的 moveit.rviz。",
            ),
            DeclareLaunchArgument(
                "sim_speed_factor",
                default_value="1.0",
                description="传给 mujoco_ros2_control 的仿真速度系数。",
            ),
            rviz_node,
            static_tf_node,
            robot_state_publisher,
            mujoco_control_node,
            move_group_node,
            joint_state_broadcaster_spawner,
            panda_arm_controller_spawner,
            panda_hand_controller_spawner,
        ]
    )
