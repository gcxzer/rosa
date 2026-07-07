# ROSA Fork 改动记录

这个文件记录当前 fork 相对原始 NASA JPL ROSA 项目的主要改动。

## 当前目标

当前 fork 专注于 ROS2 + LangChain v1 + Codex 模型适配，删除旧版 ROS1 和旧版 LangChain 写法，减少历史兼容逻辑，让代码更容易读、改和测试。

## 主要改动

### 新增 ArmAgent 机械臂第一阶段

- 新增 `scripts/load_arm_ros2_resources.sh`，一键 source ROS2、准备 `.rosa/arm_ros2_ws`、链接 vendored Panda MoveIt packages、运行 rosdep/colcon，并 source 构建后的 workspace。
- 在 `resources/arm_agent/moveit_resources/` vendored 官方 MoveIt Panda resources：
  - `panda_description/`：Panda URDF/Xacro 和 visual/collision meshes。
  - `panda_moveit_config/`：Panda SRDF、MoveIt planning config、controller config、RViz config 和 launch metadata。
- 新增 `scene_moveit.xml` / `panda_moveit.xml`，把 MuJoCo Menagerie 的 Panda joint 名从 `joint1` 对齐为 MoveIt 使用的 `panda_joint1`，方便后续接 `mujoco_ros2_control`。
- 新增 `arm_mujoco.launch.py`，使用 `mujoco_ros2_control/MujocoSystemInterface` 把 Panda MJCF 接入 ros2_control，并和 MoveIt2、RViz、controller spawner 一起启动。
- `panda.urdf.xacro` 新增 `mujoco` 硬件模式；MuJoCo 模式下 arm joint 和 hand joint 进入同一个 `MujocoSystem`，避免夹爪和机械臂被拆到两个仿真后端。
- 精简 `resources/arm_agent/`，删除资源目录下的额外 README、SOURCE、validate 脚本、viewer 脚本和没有被第一阶段引用的 MuJoCo / MoveIt 示例文件。
- 新增根目录 `arm_agent/`，作为和 `turtle_agent/` 同级的机械臂应用层 agent。
- `ArmAgent` 继承当前 `ROSA` runtime，默认使用 `CodexChatModel`，并额外注册机械臂专用工具。
- 新增 `arm_agent/prompts.py`，强调移动请求直接执行、pose 必须带坐标系、失败时停止后续动作、第一阶段不用 Gazebo。
- 新增 `arm_agent/tools.py`，提供：
  - `arm_check_readiness`
  - `arm_get_joint_states`
  - `arm_get_planning_groups`
  - `arm_get_named_targets`
  - `arm_get_end_effector_link`
  - `arm_get_end_effector_pose`
  - `arm_move_to_named_target`
  - `arm_move_to_joint_goal`
  - `arm_move_to_pose_goal`
  - `arm_stop`
- 新增 `arm_agent/moveit_client.py`，把 MoveIt2、TF、SRDF 和执行逻辑集中到 lazy runtime adapter 边界，普通单元测试不需要安装 ROS2、MoveIt2 或 MuJoCo。
- 移动工具内部调用 MoveIt2 规划 trajectory 并立即执行，不再暴露 `plan_id` 或单独 execute 工具。
- `main.py --agent` 新增 `arm`，不改变现有 `turtle` 路径。
- `clear` / `new` 会按当前 agent 创建新的 session，ArmAgent 使用 `ArmAgent chat` 默认标题。
- README 改为以 ArmAgent 为主，集中记录 ROS2 Jazzy + Panda MoveIt2 + MuJoCo Menagerie + `mujoco_ros2_control` 技术栈、资源加载、启动方式和测试 prompt。
- `pyproject.toml` 新增 `mujoco>=3.3,<4`，用于加载和验证仓库内置 MuJoCo scene。
- 新增 `resources/arm_agent/`，直接随 GitHub 仓库提供第一阶段需要的 MuJoCo Panda scene、mesh assets、MoveIt Panda URDF/SRDF/config 和第三方 license。
- 新增 `tests/test_arm_agent/`，覆盖导入、工具注册、prompt、安全计划缓存、mocked readiness、planning、execution 和 stop。
- 当前全量单元测试结果：`122 passed`。

### 增加本地 Session 管理

- 参考 Paper_Notes 的后端 session 管理方式，新增 `src/sessions/`。
- 本地 session 默认保存到 `.rosa/sessions/`。
- `sessions.json` 保存 session 索引；每个 session 对应一个按日期分桶的 JSONL transcript。
- 删除 `InMemorySaver` checkpointer，避免同时维护 LangGraph 内存和本地 transcript 两套上下文。
- `ROSA.use_session(session_id, messages)` 支持把 JSONL transcript 恢复成后续每轮输入历史。
- `main.py` 默认进入多轮对话，不再保留一次性调用模式，也不需要 `--chat` 参数。
- `main.py` 固定使用 `.rosa/sessions/` 保存本地 transcript，只保留 `--session-id` 用于继续已有 session。
- 不传 prompt 启动时，默认先发送 `你好，你是谁？`，然后继续进入多轮对话。
- 将单轮对话执行逻辑从 `main.py` 移到 `src/sessions/runner.py`，入口只保留参数解析和交互循环。

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

### 接入 Codex ChatModel

- 在 `src/codex/` 下新增 Codex 适配代码。
- `CodexChatModel` 继承 LangChain 的 `BaseChatModel`。
- 通过 OpenAI Responses API 调用 Codex 模型。
- 通过本机 Codex 登录态读取认证信息，不要求用户在代码里手动传 API key。
- `bind_tools()` 会把 LangChain 工具转换成 Responses API 的 function tools。
- 删除原来多 provider 相关的兼容代码，只保留当前需要的 Codex 路径。

### 升级 LangChain Agent 写法

- 使用 LangChain v1 的 `create_agent` 创建 agent。
- 使用显式 user/assistant 历史消息管理短期对话上下文。
- 删除旧版 `AgentExecutor`、`create_tool_calling_agent`、`MessagesPlaceholder` 等旧接口。
- 删除旧的 `chat_history` 兼容逻辑，改为通过 ROSA session transcript 恢复会话。
- 删除旧的 `accumulate_chat_history`、`return_intermediate_steps`、`show_token_usage` 等历史兼容参数。

### 只保留 ROS2

- 删除 ROS1 相关工具和说明。
- `ROSA` 和 `ROSATools` 的 `ros_version` 只接受 `2`。
- 如果传入非 ROS2 版本，会直接抛出错误，避免代码里继续保留无效分支。

## 当前验证状态

最近一次验证命令：

```bash
uv run python -m compileall main.py src turtle_agent tests
uv run --with pytest pytest -q -W error
uv run python main.py --help
```

测试结果：

```text
122 passed
```

也验证过 `main.py` 可以向模型发送一条简单消息并收到响应。
