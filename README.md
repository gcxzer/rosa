# ROSA

本项目最初 fork 自 [NASA JPL ROSA](https://github.com/nasa-jpl/rosa)，当前版本在原项目基础上改造成 ROS 2 + Codex + LangChain v1 的机器人 Agent 框架。

仓库包含三个使用同一套 ROSA runtime、工具和 session 机制的实现：

- `TurtleAgent`：面向 `turtlesim` 的轻量示例。
- `ArmAgent`：面向 Franka Emika Panda，使用 MoveIt 2 和 MuJoCo 执行机械臂 pose goal。
- `NavAgent`：面向 Stretch 3 移动底盘，使用 MuJoCo、`mujoco_ros2_control`、AMCL 和 Nav2 在静态办公/实验室场景中导航。

NavAgent 全程使用 MuJoCo，不启动也不依赖 Gazebo。

## Demo

ArmAgent 在 MuJoCo 中执行连续机械臂动作：

![ArmAgent MuJoCo demo](assets/arm-agent-mujoco-demo.gif)

## Requirements

框架基础依赖：

- Python 3.10+
- `uv`
- ROS 2 Jazzy 或 Humble
- 本机已登录 Codex，并存在 `~/.codex/auth.json`

ArmAgent 额外需要 MoveIt 2 和 `mujoco_ros2_control`。

NavAgent 当前支持 ROS 2 Jazzy，需要 `colcon`、`rosdep`、`mujoco_ros2_control`、`ros2_control`、Nav2、RViz（仅可视化时需要）以及资源加载脚本列出的 Jazzy runtime 包。首次加载会运行 `rosdep`，并逐项报告缺失包。

## Install

```bash
uv sync
```

## Run TurtleAgent

先启动 turtlesim：

```bash
ros2 run turtlesim turtlesim_node
```

另开一个终端启动 agent：

```bash
uv run python main.py --agent turtle
```

也可以直接传入第一条消息：

```bash
uv run python main.py --agent turtle "把 turtle1 传送到 (3, 3)，然后画一个边长为 2 的正方形。"
```

## Run ArmAgent

先启动 MuJoCo + MoveIt 2 机械臂仿真。默认打开 MuJoCo Simulate，不打开 RViz：

```bash
source scripts/load_arm_ros2_resources.sh
ros2 launch moveit_resources_panda_moveit_config arm_mujoco.launch.py
```

这个 launch 会同时启动常驻的 `rosa_arm_moveit_server`。如果想打开 RViz 或只做无界面仿真：

```bash
ros2 launch moveit_resources_panda_moveit_config arm_mujoco.launch.py rviz:=true
ros2 launch moveit_resources_panda_moveit_config arm_mujoco.launch.py headless:=true
```

另开一个终端运行 ArmAgent：

```bash
source scripts/load_arm_ros2_resources.sh
uv run python main.py --agent arm "检查机械臂运行栈是否就绪"
```

## Run NavAgent

先启动 MuJoCo + Nav2 移动导航仿真。默认打开 MuJoCo Simulate，不打开 RViz：

```bash
source scripts/load_nav_ros2_resources.sh
ros2 launch rosa_nav_bringup nav_mujoco.launch.py
```

这个 launch 会同时启动 Stretch 3、差速控制器、360° LiDAR、静态地图、AMCL 和 Nav2。如果想打开 RViz 或只做无界面仿真：

```bash
ros2 launch rosa_nav_bringup nav_mujoco.launch.py rviz:=true
ros2 launch rosa_nav_bringup nav_mujoco.launch.py headless:=true
```

另开一个终端运行 NavAgent：

```bash
source scripts/load_nav_ros2_resources.sh
uv run python main.py --agent nav "检查导航运行栈是否就绪"
```

## NavAgent Reference

NavAgent 使用异步 Nav2 action：启动导航后返回稳定的 `goal_id`，随后可以查询状态或取消。进程内同一时间只允许一个活动任务；取消操作是幂等的。示例：

```bash
uv run python main.py --agent nav "导航到充电站，并持续查询状态直到结束"
uv run python main.py --agent nav "依次从前台去巡检点，再去充电区"
uv run python main.py --agent nav "取消当前导航任务"
```

## Project Structure

```text
src/
  rosa/        ROSA 核心 runtime
  codex/       Codex ChatModel 的 LangChain 适配
  tools/       通用 ROS 2、系统、日志和计算工具
  sessions/    本地多轮 session 存储
  prompts/     通用机器人系统 prompt

turtle_agent/  基于 ROSA 的 turtlesim 实现
arm_agent/     基于 ROSA 的机械臂实现
nav_agent/     基于 ROSA 的 MuJoCo/Nav2 导航实现

resources/
  arm_agent/   Panda MoveIt/MuJoCo 资源
  nav_agent/   Stretch 3、office/lab、Nav2、地图和 RViz 资源

scripts/
  load_arm_ros2_resources.sh
  load_nav_ros2_resources.sh
```

## Useful Commands

运行不依赖已加载 ROS 环境的单元测试：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --with pytest python -m pytest -q tests --ignore=tests/integration
```

查看统一入口参数：

```bash
uv run python main.py --help
```

## License

见 [LICENSE](LICENSE)。Vendored Stretch 3 模型的来源、固定 revision 和上游许可证见 `resources/nav_agent/rosa_nav_bringup/mujoco/SOURCE.md` 与相邻许可证文件。
