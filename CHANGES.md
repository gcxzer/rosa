# ROSA Fork 改动记录

这个文件记录当前 fork 相对原始 NASA JPL ROSA 项目的主要改动。

## 当前目标

当前 fork 专注于 ROS2 + LangChain v1 + Codex 模型适配，删除旧版 ROS1 和旧版 LangChain 写法，减少历史兼容逻辑，让代码更容易读、改和测试。

## 主要改动

### 只保留 ROS2

- 删除 ROS1 相关工具和说明。
- `ROSA` 和 `ROSATools` 的 `ros_version` 只接受 `2`。
- 如果传入非 ROS2 版本，会直接抛出错误，避免代码里继续保留无效分支。

### 升级 LangChain Agent 写法

- 使用 LangChain v1 的 `create_agent` 创建 agent。
- 使用 LangGraph 的 `InMemorySaver` 管理短期对话上下文。
- 删除旧版 `AgentExecutor`、`create_tool_calling_agent`、`MessagesPlaceholder` 等旧接口。
- 删除旧的 `chat_history` 手动维护逻辑，改为通过 LangGraph `thread_id` 隔离会话。
- 删除旧的 `accumulate_chat_history`、`return_intermediate_steps`、`show_token_usage` 等历史兼容参数。

### 接入 Codex ChatModel

- 在 `src/codex/` 下新增 Codex 适配代码。
- `CodexChatModel` 继承 LangChain 的 `BaseChatModel`。
- 通过 OpenAI Responses API 调用 Codex 模型。
- 通过本机 Codex 登录态读取认证信息，不要求用户在代码里手动传 API key。
- `bind_tools()` 会把 LangChain 工具转换成 Responses API 的 function tools。
- 删除原来多 provider 相关的兼容代码，只保留当前需要的 Codex 路径。

### 重写 TurtleAgent

- 新增根目录 `turtle_agent/`，用当前 ROSA runtime 重写 turtlesim 专用 agent。
- `turtle_agent/` 和 `src/` 同级，表示它是面向 turtlesim 的示例/应用层 agent，而不是 ROSA 核心库代码。
- 新版 `TurtleAgent` 不再依赖 ROS1、`rospy`、catkin package 或旧版 LangChain `AgentExecutor`。
- `TurtleAgent` 继承当前 `ROSA`，默认使用 `CodexChatModel`，并额外加载 turtlesim 专用工具包。
- 新增 `turtle_agent/prompts.py`，描述 turtlesim 的坐标系、边界、绘图工作流和工具使用规则。
- 新增 `turtle_agent/tools.py`，通过 ROS2 CLI 提供 turtle 控制和绘图工具：
  - pose 查询、spawn、kill、clear、reset、背景色设置
  - 画笔控制、绝对传送、相对传送、cmd_vel 发布、停止运动
  - 线段、折线、矩形、圆、圆弧绘制
  - 矩形边界计算和矩形重叠检查
- 删除单独的 `turtle_main.py`，统一使用 `main.py` 作为本地入口。
- `main.py` 新增 `--agent` 参数；当前只有 `turtle` 一个可选 agent。
- 新增 `tests/test_turtle_agent/`，在不启动真实 ROS2/turtlesim 的情况下验证 agent 绑定、prompt 和 ROS2 CLI 命令生成。

### 重构工具和 Prompt 目录

- 将工具从 `src/rosa/tools/` 移到 `src/tools/`。
- 将系统 prompt 从 `src/rosa/prompts.py` 移到 `src/prompts/system.py`。
- `src/rosa/__init__.py` 只保留轻量导出，主要逻辑不放在 `__init__` 里。

### 精简工具加载逻辑

- 默认工具由 `ROSATools` 初始化时加载：
  - `src/tools/calculation.py`
  - `src/tools/log.py`
  - `src/tools/system.py`
  - `src/tools/ros2.py`
- `add_tools()` 只添加调用者直接传入的 LangChain 工具对象。
- `add_packages()` 只扫描调用者传入的模块或 package，并添加其中公开的 LangChain 工具对象。
- 删除 `add_packages(..., blacklist=...)` 和 `__iterative_add(..., blacklist=...)` 中没有实际用途的冗余参数。
- `blacklist` 现在只通过 `ROSATools(..., blacklist=...)` 传入，并在 `__add_tool()` 中统一注入到需要该参数的工具函数里。

### 流式输出

- `ROSA.astream()` 保留为异步生成器。
- 对外输出统一事件字典：
  - `token`
  - `tool_start`
  - `tool_end`
  - `final`
  - `error`
- 本地同步调试脚本 `main.py` 不直接堆 event loop 样板代码，而是调用 `src/codex/streaming.py` 里的同步消费函数。
- `main.py` 支持通过 `--model` 指定模型，通过 `--thinking` 指定 reasoning effort。

### 删除旧打包入口

- 删除 `setup.py`。
- 以 `pyproject.toml` 作为项目依赖和打包配置入口。

### 删除旧 Docker Demo

- 删除旧的 `demo.sh` 和 `Dockerfile`。
- 当前 fork 不再维护旧式 Docker turtlesim demo；TurtleAgent 统一通过 `main.py --agent turtle` 运行。
- 如果需要真实控制 turtlesim，请在本机或外部 ROS2 环境中先启动 turtlesim，并确保运行 `main.py` 的 shell 可以访问同一个 ROS2 graph。

### 清理忽略文件

- `.gitignore` 增加本地开发、缓存和 OpenSpec/Codex 相关忽略项。
- `.codex/`、`openspec/`、`uv.lock` 等本地或规划文件不进入版本管理。
- 删除误提交的 `.env`。
- 删除旧 GitHub Actions workflow：
  - 旧 CI 仍使用 `unittest discover`，不能覆盖当前 pytest 风格测试。
  - 旧 PyPI 发布 workflow 仍面向 `jpl-rosa`，不适配当前 fork。
- 清理本地生成产物和系统缓存，例如 `src/jpl_rosa.egg-info/` 和 `.DS_Store`。

## 当前验证状态

最近一次验证命令：

```bash
uv run python -m compileall main.py src turtle_agent tests
uv run --with pytest pytest -q -W error
uv run python main.py --help
```

测试结果：

```text
106 passed
```

也验证过 `main.py` 可以向模型发送一条简单消息并收到响应。
