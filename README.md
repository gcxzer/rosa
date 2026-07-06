# ROSA fork

这个项目基于 [NASA JPL ROSA](https://github.com/nasa-jpl/rosa) 改造，适配langchain 1.0+, 删除了ros1。完整改动记录见 [CHANGES.md](CHANGES.md)。

## 能做什么

- 读取 ROS2 node、topic、service、parameter 和日志信息。
- 用自然语言和 ROS2 系统多轮对话，并保留本地 session。
- 通过 Codex + LangChain v1 tool-calling 调用机器人工具。
- 使用流式输出观察模型回答、工具开始和工具结束事件。
- 以 turtlesim 作为默认 demo，验证移动、传送、画线、画矩形、画圆等控制链路。

## 环境要求

- Python 3.10+
- `uv`
- ROS2 Humble、Iron、Jazzy 或更高版本
- 本机已经登录 Codex，并存在 `~/.codex/auth.json`

运行前先在当前 shell 里 source ROS2 环境，例如：

```bash
source /opt/ros/humble/setup.zsh
```

## 快速启动

启动 agent：

```bash
uv run python main.py --agent turtle
```

不传 prompt 时，默认会先发送 `你好，你是谁？`，然后继续进入多轮对话。

也可以启动时直接给第一条消息：

```bash
uv run python main.py --agent turtle "当前 ROS2 系统里有哪些 node、topic 和 service？"
```

第一条消息发送完成后，程序不会退出，会继续等待下一轮输入。

## 可选 Demo

如果要验证运动控制和绘图工具，可以另开一个终端启动 turtlesim：

```bash
ros2 run turtlesim turtlesim_node
```

然后在 agent 里输入：

```text
检查当前 ROS2 graph，确认 turtlesim 是否正在运行。
```

```text
把 turtle1 传送到 (3, 3)，然后画一个边长为 2 的正方形。
```

turtlesim 只是当前自带的可运行目标；真实机器人接入时，核心改动通常是新增对应机器人平台的工具包和 prompt。


## 模型参数

默认模型是 `gpt-5.5`：

```bash
uv run python main.py --agent turtle --model gpt-5.5
```

可以设置 reasoning effort：

```bash
uv run python main.py --agent turtle --thinking high "分析当前 ROS2 graph"
```

`--thinking none` 表示不显式覆盖模型默认配置。当前可选值：

```text
none, low, medium, high, xhigh
```

## Session

本地对话会保存到 `.rosa/sessions/`：

- `sessions.json` 保存 session 索引。
- 每个 session 对应一个 JSONL transcript。
- 每轮对话都会把历史 user/assistant 消息交回 agent，因此下一轮能接上上下文。

启动时终端会打印新 session id，例如：

```text
新建 session：20260706_120000_abcd1234 - TurtleAgent chat
```

下次可以用这个 id 继续同一个对话：

```bash
uv run python main.py --agent turtle --session-id 20260706_120000_abcd1234
```

## 接入其他机器人

当前 TurtleAgent 的运行方式可以作为其他 ROS2 机器人 agent 的模板：

- 在 `turtle_agent/tools.py` 这种位置定义平台专用 LangChain tools。
- 在 `turtle_agent/prompts.py` 这种位置描述机器人能力、约束和安全边界。
- 继承 `ROSA`，把默认 ROS2 工具和平台专用工具一起注册给 agent。
- 继续复用 `src/codex/` 的 Codex ChatModel 和 `src/sessions/` 的本地 session 管理。

## 代码位置

- `main.py`：当前本地命令行入口。
- `turtle_agent/agent.py`：TurtleAgent 类。
- `turtle_agent/prompts.py`：当前 agent 的系统 prompt。
- `turtle_agent/tools.py`：当前 agent 的平台专用工具。
- `src/rosa/rosa.py`：底层 ROSA agent runtime。
- `src/codex/`：Codex Responses API 到 LangChain `BaseChatModel` 的适配层。
- `src/sessions/store.py`：本地 session 索引和 JSONL transcript 存储。
- `src/sessions/runner.py`：执行一轮 session 对话，集中处理流式事件打印和 transcript 写入。
- `src/tools/`：通用 ROS2、系统、日志和计算工具。
- `tests/`：单元测试。

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
111 passed
```

## 许可证

本项目继承 ROSA 的许可证。详情见 [LICENSE](LICENSE)。
