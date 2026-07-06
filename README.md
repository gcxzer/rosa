# ROSA ROS2-only 中文 fork

这是一个基于 [NASA JPL ROSA](https://github.com/nasa-jpl/rosa) 的个人 fork。这个分支不再追求完整保留上游原版 README 中的全部介绍、演示和历史功能，而是把代码库整理成一个更适合继续研究、阅读和二次开发的 ROS2-only 版本。

上游项目对应论文：

- [ROSA: A Modular Agentic AI Framework for ROS-based Robot Systems](https://arxiv.org/abs/2410.06472)

## 当前定位

这个 fork 目前聚焦三件事：

- 只保留 ROS2 工具链。
- 将项目文档、代码注释、docstring、系统 prompt 和工具输出整理成中文。
- 清理旧依赖路径和弃用 warning，让后续开发更干净。

如果需要查看上游原始功能、历史演示、Wiki 或完整项目背景，请直接看 [NASA JPL ROSA 原仓库](https://github.com/nasa-jpl/rosa)。

## 已完成改动

- 删除 ROS1 工具实现：`src/rosa/tools/ros1.py`。
- 删除 ROS1 相关测试：`tests/test_rosa/tools/test_ros1.py`。
- 删除基于 `catkin`、`rospy`、`roslaunch` 的旧版 TurtleAgent 示例。
- `ROSA` 和 `ROSATools` 现在只接受 `ros_version=2`。
- 系统 prompt 已更新为 ROS2 工具名，例如 `ros2_node_list`、`ros2_topic_list`、`ros2_service_list`、`ros2_param_*`。
- Dockerfile 已改为 ROS2 Humble 环境。
- CI 已删除 Noetic/ROS1 job，只保留 ROS2 Humble 测试方向。
- LangChain 工具导入迁移到 `langchain_core.tools`。
- 修复 `BaseTool` 旧式调用导致的弃用 warning。
- 将主要文档、注释、docstring、prompt 和用户可见输出改为中文。

## 快速开始

环境要求：

- Python 3.9+
- ROS2 Humble、Iron、Jazzy 或更高版本
- `uv`

安装依赖并运行测试：

```bash
uv run --with pytest pytest -q -W error
```

语法检查：

```bash
uv run python -m compileall src tests
```

当前验证结果：

```text
80 passed
```

其中 `-W error` 会把 warning 当作失败处理，因此当前测试路径下没有残留的 LangChain warning。

## 基本用法

```python
from rosa import ROSA

llm = get_your_llm_here()
agent = ROSA(ros_version=2, llm=llm)
agent.invoke("列出当前系统中的 ROS2 topic")
```

## ROS2 TurtleSim 环境

仓库中保留了一个 Docker 脚本，用于启动 ROS2 TurtleSim 环境，方便验证 ROS2 node、topic、service 和 parameter 工具。

```bash
./demo.sh
```

当前 fork 已删除旧版 TurtleAgent 示例；`demo.sh` 只负责提供 ROS2 TurtleSim 运行环境。

## 目录说明

- `src/rosa/rosa.py`：ROSA agent 主体逻辑。
- `src/rosa/prompts.py`：系统 prompt 和机器人专属 prompt 拼接逻辑。
- `src/rosa/tools/ros2.py`：ROS2 CLI 工具封装。
- `src/rosa/tools/calculation.py`：数学和几何计算工具。
- `src/rosa/tools/log.py`：日志读取工具。
- `src/rosa/tools/system.py`：系统辅助工具。
- `tests/test_rosa/tools/test_ros2.py`：ROS2 工具测试。
- `tests/test_rosa/tools/test_rosa_tools.py`：工具集合初始化和黑名单注入测试。

## 和上游的关系

这个仓库保留上游许可证和论文引用，但当前代码目标已经和上游原版不同：

- 上游：同时支持 ROS1 和 ROS2，并包含历史 TurtleAgent demo。
- 本 fork：删除 ROS1，保留并整理 ROS2 agent 工具链。

后续如果需要从上游同步改动，应重点检查：

- LangChain API 是否变化。
- ROS2 CLI 输出格式是否变化。
- 上游是否新增值得迁移的 ROS2 工具。
- 本 fork 删除 ROS1 后，是否需要手动调整 merge conflict。

## 许可证

本项目继承上游 ROSA 的许可证。详情见 [LICENSE](LICENSE)。
