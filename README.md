# ROSA fork

这是一个基于 [NASA JPL ROSA](https://github.com/nasa-jpl/rosa) 的个人 fork。一个更适合继续研究、阅读和二次开发的 ROS2-only 版本。

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

当前 fork 已删除旧版 ROS1 TurtleAgent 示例；新的 TurtleAgent 使用 `main.py --agent turtle` 运行。

## 许可证

本项目继承 ROSA 的许可证。详情见 [LICENSE](LICENSE)。
