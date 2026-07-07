#!/usr/bin/env bash

# ArmAgent ROS2 资源加载脚本。
#
# 推荐用法：
#
#   source scripts/load_arm_ros2_resources.sh
#
# 不建议直接 `bash scripts/load_arm_ros2_resources.sh`，因为直接执行只能在子进程里 source
# ROS2 和 workspace，脚本结束后当前终端不会保留环境变量。用 source 执行后，当前终端会拿到：
#
# - `/opt/ros/$ROS_DISTRO` 的 ROS2 环境。
# - 本仓库 vendored Panda MoveIt packages 构建出的 ament overlay。
# - `moveit_resources_panda_description` 和 `moveit_resources_panda_moveit_config` 这两个 ROS2 package。
#
# 可选环境变量：
#
#   ROS_DISTRO=jazzy
#     选择要加载的 ROS2 发行版；未设置时默认 jazzy。
#
#   ARM_AGENT_WS=/path/to/ws
#     选择本地 colcon workspace；未设置时默认 `.rosa/arm_ros2_ws`，该目录已被 .gitignore 忽略。
#
#   ARM_AGENT_SKIP_ROSDEP=1
#     跳过 rosdep install。适合你已经装好依赖、只想重新 source/build 的情况。
#
#   ARM_AGENT_FORCE_BUILD=1
#     即使 workspace 已经 build 过，也强制重新 colcon build。

# 这个脚本既可能被 bash source，也可能被 zsh source。为了找到脚本自己的真实路径：
# - bash 下使用 BASH_SOURCE。
# - zsh 下用 zsh 的 `%x` 展开；放在 eval 里，避免 bash 解析 zsh 专用语法。
if [ -n "${ZSH_VERSION:-}" ]; then
  eval 'SCRIPT_PATH="${(%):-%x}"'
else
  SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
RESOURCE_ROOT="${REPO_ROOT}/resources/arm_agent"
MOVEIT_RESOURCE_ROOT="${RESOURCE_ROOT}/moveit_resources"
PANDA_DESCRIPTION_SRC="${MOVEIT_RESOURCE_ROOT}/panda_description"
PANDA_MOVEIT_CONFIG_SRC="${MOVEIT_RESOURCE_ROOT}/panda_moveit_config"
MUJOCO_SCENE="${RESOURCE_ROOT}/mujoco_menagerie/franka_emika_panda/scene_moveit.xml"
MUJOCO_PANDA="${RESOURCE_ROOT}/mujoco_menagerie/franka_emika_panda/panda_moveit.xml"

# 判断当前脚本是否被 source。后面遇到错误时：
# - source 执行：return，避免关掉用户终端。
# - 直接执行：exit，返回失败码给调用者。
SCRIPT_SOURCED=0
if [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]:-$0}" != "$0" ]; then
  SCRIPT_SOURCED=1
fi
if [ -n "${ZSH_VERSION:-}" ]; then
  case "${ZSH_EVAL_CONTEXT:-}" in
    *:file*) SCRIPT_SOURCED=1 ;;
  esac
fi

echo "== ArmAgent ROS2 资源加载 =="
echo "repo: ${REPO_ROOT}"

if [ "${SCRIPT_SOURCED}" != "1" ]; then
  echo
  echo "提示：当前是直接执行脚本。构建会继续，但 ROS2 环境不会保留到你的终端。"
  echo "建议改用：source scripts/load_arm_ros2_resources.sh"
fi

# 先检查仓库内真正需要的资源是否存在。这里只检查运行需要的最小集合，不做额外 validate 脚本。
MISSING_RESOURCE=0
for resource_path in \
  "${PANDA_DESCRIPTION_SRC}/package.xml" \
  "${PANDA_DESCRIPTION_SRC}/CMakeLists.txt" \
  "${PANDA_DESCRIPTION_SRC}/urdf/panda.urdf" \
  "${PANDA_DESCRIPTION_SRC}/urdf/panda.urdf.xacro" \
  "${PANDA_MOVEIT_CONFIG_SRC}/package.xml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/CMakeLists.txt" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/panda.srdf" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/panda.urdf.xacro" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/panda.ros2_control.xacro" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/panda_hand.ros2_control.xacro" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/kinematics.yaml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/joint_limits.yaml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/ompl_planning.yaml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/gripper_moveit_controllers.yaml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/config/ros2_controllers.yaml" \
  "${PANDA_MOVEIT_CONFIG_SRC}/launch/arm_mujoco.launch.py" \
  "${PANDA_MOVEIT_CONFIG_SRC}/launch/demo.launch.py" \
  "${PANDA_MOVEIT_CONFIG_SRC}/launch/moveit.rviz" \
  "${MUJOCO_SCENE}" \
  "${MUJOCO_PANDA}"
do
  if [ ! -e "${resource_path}" ]; then
    echo "缺少资源：${resource_path}"
    MISSING_RESOURCE=1
  fi
done

if [ "${MISSING_RESOURCE}" = "1" ]; then
  echo "资源不完整，停止加载。"
  if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
fi

ROS_DISTRO="${ROS_DISTRO:-jazzy}"
ROS_SETUP=""
if [ -n "${ZSH_VERSION:-}" ] && [ -f "/opt/ros/${ROS_DISTRO}/setup.zsh" ]; then
  ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.zsh"
elif [ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
  ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
elif [ -f "/opt/ros/${ROS_DISTRO}/setup.sh" ]; then
  ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.sh"
fi

if [ -z "${ROS_SETUP}" ]; then
  echo "找不到 ROS2 setup 文件：/opt/ros/${ROS_DISTRO}/setup.*"
  echo "请先安装 ROS2，或用 ROS_DISTRO=humble/jazzy 指定已有发行版。"
  if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
fi

echo
echo "== source ROS2 =="
echo "${ROS_SETUP}"
# shellcheck disable=SC1090
source "${ROS_SETUP}"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "source ${ROS_SETUP} 后仍然找不到 ros2 命令。"
  if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
fi

if ! command -v colcon >/dev/null 2>&1; then
  echo "找不到 colcon。Ubuntu 下通常需要安装：python3-colcon-common-extensions"
  if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
fi

ARM_AGENT_WS="${ARM_AGENT_WS:-${REPO_ROOT}/.rosa/arm_ros2_ws}"
ARM_AGENT_WS="$(mkdir -p "${ARM_AGENT_WS}" && CDPATH= cd -- "${ARM_AGENT_WS}" && pwd)"
mkdir -p "${ARM_AGENT_WS}/src"

echo
echo "== 准备本地 ROS2 workspace =="
echo "workspace: ${ARM_AGENT_WS}"

# 用 symlink 而不是复制，避免资源改动后 workspace 里还有一份旧文件。
# 这两个目录本身就是 ROS2 ament package，colcon 可以直接构建。
# 如果之前手动复制过同名目录，这里先删掉 workspace 里的旧入口；删除范围只限
# `.rosa/arm_ros2_ws/src/` 下这两个固定路径，不会碰仓库里的真实资源。
rm -rf "${ARM_AGENT_WS}/src/panda_description" "${ARM_AGENT_WS}/src/panda_moveit_config"
ln -sfn "${PANDA_DESCRIPTION_SRC}" "${ARM_AGENT_WS}/src/panda_description"
ln -sfn "${PANDA_MOVEIT_CONFIG_SRC}" "${ARM_AGENT_WS}/src/panda_moveit_config"
echo "linked: ${ARM_AGENT_WS}/src/panda_description -> ${PANDA_DESCRIPTION_SRC}"
echo "linked: ${ARM_AGENT_WS}/src/panda_moveit_config -> ${PANDA_MOVEIT_CONFIG_SRC}"

if [ "${ARM_AGENT_SKIP_ROSDEP:-0}" != "1" ]; then
  if command -v rosdep >/dev/null 2>&1; then
    echo
    echo "== rosdep install =="
    echo "只安装/确认 vendored Panda packages 的 ROS 依赖；不会修改仓库。"
    rosdep install --from-paths "${ARM_AGENT_WS}/src" --ignore-src -r -y
    ROSDEP_STATUS=$?
    if [ "${ROSDEP_STATUS}" -ne 0 ]; then
      echo "rosdep install 失败。你可以装好缺失包后重跑，或临时用 ARM_AGENT_SKIP_ROSDEP=1 跳过。"
      if [ "${SCRIPT_SOURCED}" = "1" ]; then return "${ROSDEP_STATUS}"; else exit "${ROSDEP_STATUS}"; fi
    fi
  else
    echo
    echo "跳过 rosdep：当前环境没有 rosdep 命令。"
  fi
else
  echo
  echo "跳过 rosdep：ARM_AGENT_SKIP_ROSDEP=1"
fi

NEED_BUILD=0
INSTALLED_PANDA_DESCRIPTION_PACKAGE="${ARM_AGENT_WS}/install/moveit_resources_panda_description/share/moveit_resources_panda_description/package.xml"
INSTALLED_PANDA_MOVEIT_CONFIG_PACKAGE="${ARM_AGENT_WS}/install/moveit_resources_panda_moveit_config/share/moveit_resources_panda_moveit_config/package.xml"
if [ "${ARM_AGENT_FORCE_BUILD:-0}" = "1" ]; then
  NEED_BUILD=1
elif [ ! -f "${INSTALLED_PANDA_DESCRIPTION_PACKAGE}" ] || [ ! -f "${INSTALLED_PANDA_MOVEIT_CONFIG_PACKAGE}" ]; then
  # 只检查 install/setup.bash 不够可靠：colcon 失败时也可能留下半成品 setup 文件。
  # 真正判断 workspace 是否可用，要看这两个 vendored ROS package 有没有被安装到 install space。
  NEED_BUILD=1
elif [ "${PANDA_DESCRIPTION_SRC}/package.xml" -nt "${INSTALLED_PANDA_DESCRIPTION_PACKAGE}" ]; then
  NEED_BUILD=1
elif [ "${PANDA_DESCRIPTION_SRC}/CMakeLists.txt" -nt "${INSTALLED_PANDA_DESCRIPTION_PACKAGE}" ]; then
  NEED_BUILD=1
elif [ "${PANDA_MOVEIT_CONFIG_SRC}/package.xml" -nt "${INSTALLED_PANDA_MOVEIT_CONFIG_PACKAGE}" ]; then
  NEED_BUILD=1
elif [ "${PANDA_MOVEIT_CONFIG_SRC}/CMakeLists.txt" -nt "${INSTALLED_PANDA_MOVEIT_CONFIG_PACKAGE}" ]; then
  NEED_BUILD=1
elif [ "${PANDA_MOVEIT_CONFIG_SRC}/launch/arm_mujoco.launch.py" -nt "${INSTALLED_PANDA_MOVEIT_CONFIG_PACKAGE}" ]; then
  NEED_BUILD=1
fi

if [ "${NEED_BUILD}" = "1" ]; then
  echo
  echo "== colcon build =="
  echo "构建 vendored Panda description 和 MoveIt config。"
  (
    cd "${ARM_AGENT_WS}" &&
      colcon build --symlink-install \
        --packages-select \
        moveit_resources_panda_description \
        moveit_resources_panda_moveit_config
  )
  BUILD_STATUS=$?
  if [ "${BUILD_STATUS}" -ne 0 ]; then
    echo "colcon build 失败。请先根据上面的错误补齐 ROS2/MoveIt 依赖。"
    if [ "${SCRIPT_SOURCED}" = "1" ]; then return "${BUILD_STATUS}"; else exit "${BUILD_STATUS}"; fi
  fi
else
  echo
  echo "== colcon build =="
  echo "workspace 已经构建过；如需强制重建，使用 ARM_AGENT_FORCE_BUILD=1。"
fi

WS_SETUP=""
if [ -n "${ZSH_VERSION:-}" ] && [ -f "${ARM_AGENT_WS}/install/setup.zsh" ]; then
  WS_SETUP="${ARM_AGENT_WS}/install/setup.zsh"
elif [ -f "${ARM_AGENT_WS}/install/setup.bash" ]; then
  WS_SETUP="${ARM_AGENT_WS}/install/setup.bash"
elif [ -f "${ARM_AGENT_WS}/install/setup.sh" ]; then
  WS_SETUP="${ARM_AGENT_WS}/install/setup.sh"
fi

if [ -z "${WS_SETUP}" ]; then
  echo "找不到 workspace setup 文件：${ARM_AGENT_WS}/install/setup.*"
  if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
fi

echo
echo "== source ArmAgent ROS2 workspace =="
echo "${WS_SETUP}"
# shellcheck disable=SC1090
source "${WS_SETUP}"

echo
echo "== package 检查 =="
for package_name in \
  moveit_resources_panda_description \
  moveit_resources_panda_moveit_config
do
  if ros2 pkg prefix "${package_name}" >/dev/null 2>&1; then
    echo "OK   ${package_name}: $(ros2 pkg prefix "${package_name}")"
  else
    echo "MISS ${package_name}"
    if [ "${SCRIPT_SOURCED}" = "1" ]; then return 1; else exit 1; fi
  fi
done

echo
echo "== 运行时包提示 =="
MISSING_RUNTIME=""
for package_name in \
  moveit_py \
  moveit_ros_move_group \
  moveit_kinematics \
  moveit_planners_ompl \
  moveit_ros_visualization \
  controller_manager \
  joint_state_broadcaster \
  joint_trajectory_controller \
  robot_state_publisher \
  tf2_ros \
  xacro \
  mujoco_ros2_control
do
  if ros2 pkg prefix "${package_name}" >/dev/null 2>&1; then
    echo "OK   ${package_name}"
  else
    echo "MISS ${package_name}"
    MISSING_RUNTIME="${MISSING_RUNTIME} ${package_name}"
  fi
done

if [ -n "${MISSING_RUNTIME}" ]; then
  echo
  echo "上面 MISS 的是 ArmAgent 真正运行 MoveIt / MuJoCo 控制链路时需要的系统 ROS2 包。"
  echo "这个脚本已经加载了仓库自带的 Panda description/config；缺失的运行时包需要用 apt 或你的 ROS workspace 安装。"
else
  echo
  echo "运行时包检查通过。"
fi

echo
echo "== 完成 =="
echo "当前终端已加载 ROS2 和 ArmAgent Panda MoveIt 资源。"
echo "可以先试：ros2 launch moveit_resources_panda_moveit_config arm_mujoco.launch.py"
