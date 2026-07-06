# ROSA fork

这是一个基于 [NASA JPL ROSA](https://github.com/nasa-jpl/rosa) 的个人 fork。当前分支不再完整保留上游的历史功能，而是整理成一个更适合继续研究、阅读和二次开发的 ROS2-only 版本。

上游论文：

- [ROSA: A Modular Agentic AI Framework for ROS-based Robot Systems](https://arxiv.org/abs/2410.06472)

详细改造记录见 [CHANGES.md](CHANGES.md)。

## 当前定位

这个 fork 主要做三件事：

- 只保留 ROS2 工具链。
- 使用 LangChain v1 agent API 管理工具调用和对话状态。
- 用新架构重写 TurtleAgent，让 turtlesim demo 也走 ROS2-only + Codex + LangChain v1。

## 环境要求

- Python 3.10+
- `uv`
- ROS2 Humble、Iron、Jazzy 或更高版本
- 本机已经登录 Codex，并存在 `~/.codex/auth.json`

如果需要调用真实 ROS2 系统，请先在当前 shell 中 source 对应 ROS2 环境。

## 快速运行

运行本地统一入口：

```bash
uv run python main.py --agent turtle "列出 turtlesim 当前的 ROS2 node、topic 和 service"
```

指定模型和 thinking mode：

```bash
uv run python main.py --agent turtle --model gpt-5.5 --thinking high "当前有哪些 ROS2 node"
```

`--thinking none` 表示不显式传 reasoning effort。当前可选值：

```text
none, low, medium, high, xhigh
```

在真实绘图前，请先启动 turtlesim，并确保运行 `main.py` 的 shell 能访问同一个 ROS2 graph。

## 代码用法

```python
from codex import CodexChatModel
from rosa import ROSA

llm = CodexChatModel(model="gpt-5.5", thinking="high")
agent = ROSA(ros_version=2, llm=llm, streaming=True)

result = agent.invoke("列出当前系统中的 ROS2 topic")
print(result)
```

流式调用：

```python
async for event in agent.astream("当前有哪些 ROS2 node"):
    if event["type"] == "token":
        print(event["content"], end="", flush=True)
    elif event["type"] == "tool_start":
        print("[tool:start]", event["name"], event["input"])
    elif event["type"] == "tool_end":
        print("[tool:end]", event["name"], event["output"])
    elif event["type"] == "final":
        print(event["content"])
```

`ROSA.astream()` 对外只暴露稳定事件：

- `token`
- `tool_start`
- `tool_end`
- `final`
- `error`

## 工具扩展

`ROSA` 默认加载这些工具模块：

- `src/tools/calculation.py`
- `src/tools/log.py`
- `src/tools/system.py`
- `src/tools/ros2.py`

如果只想添加几个已经创建好的 LangChain tool，用 `tools`：

```python
agent = ROSA(
    ros_version=2,
    llm=llm,
    tools=[my_tool],
)
```

如果想扫描一个模块里的公开 LangChain tools，用 `tool_packages`：

```python
import my_tools

agent = ROSA(
    ros_version=2,
    llm=llm,
    tool_packages=[my_tools],
)
```

`blacklist` 只在初始化 `ROSA` / `ROSATools` 时传入，并统一注入到需要该参数的 ROS2 工具函数里。

## TurtleAgent

TurtleAgent 是基于当前 ROSA runtime 重写的 turtlesim 专用 agent：

- 代码入口是 `turtle_agent/agent.py`。
- 专用 prompt 在 `turtle_agent/prompts.py`。
- 专用工具在 `turtle_agent/tools.py`。
- 本地调试入口是统一的 `main.py --agent turtle`。

代码使用方式：

```python
from turtle_agent import TurtleAgent

agent = TurtleAgent(streaming=True)
agent.invoke("把 turtle1 传送到 (3, 3)，然后画一个边长为 2 的正方形")
```

TurtleAgent 额外提供这些 turtlesim 工具：

- `turtle_get_pose`
- `turtle_spawn`
- `turtle_kill`
- `turtlesim_clear`
- `turtlesim_reset`
- `turtlesim_set_background`
- `turtle_set_pen`
- `turtle_teleport_absolute`
- `turtle_teleport_relative`
- `turtle_publish_twist`
- `turtle_stop`
- `draw_line_segment`
- `draw_polyline`
- `draw_rectangle`
- `draw_circle`
- `draw_arc`
- `calculate_rectangle_bounds`
- `check_rectangles_overlap`

## 开发验证

语法检查：

```bash
uv run python -m compileall main.py src turtle_agent tests
```

运行测试，并把 warning 当作失败：

```bash
uv run --with pytest pytest -q -W error
```

当前验证结果：

```text
106 passed
```

## 目录说明

- `main.py`：本地统一流式测试入口，通过 `--agent` 指定 agent。当前只有 `turtle`。
- `src/rosa/rosa.py`：ROSA agent 主体逻辑。
- `src/codex/`：Codex Responses API 到 LangChain `BaseChatModel` 的适配层。
- `src/prompts/`：系统 prompt 和机器人 prompt 拼接逻辑。
- `src/tools/`：默认工具、ROS2 CLI 封装和工具注册逻辑。
- `turtle_agent/`：和 `src/` 同级的 ROS2 turtlesim agent 示例。
- `tests/`：单元测试。

## ROS2 TurtleSim 环境

仓库中保留了 Docker 脚本，用于启动 ROS2 TurtleSim 环境，方便验证 ROS2 node、topic、service 和 parameter 工具。

```bash
./demo.sh
```

当前 fork 已删除旧版 ROS1 TurtleAgent 示例；新的 TurtleAgent 使用 `main.py --agent turtle` 运行。

## 和上游的关系

这个仓库保留上游许可证和论文引用，但当前代码目标和上游原版不同：

- 上游：同时支持 ROS1 和 ROS2，并包含历史 TurtleAgent demo。
- 本 fork：删除 ROS1 和其他模型 provider，保留 ROS2 agent 工具链，默认面向 Codex Responses API。

如果需要查看上游原始功能、历史演示、Wiki 或完整项目背景，请直接看 [NASA JPL ROSA 原仓库](https://github.com/nasa-jpl/rosa)。

## 许可证

本项目继承上游 ROSA 的许可证。详情见 [LICENSE](LICENSE)。
