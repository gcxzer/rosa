#!/usr/bin/env bash

# Source ROS 2 Jazzy and build the repository-owned NavAgent resource package.
# Recommended usage: source scripts/load_nav_ros2_resources.sh

if [ -n "${ZSH_VERSION:-}" ]; then
  eval 'SCRIPT_PATH="${(%):-%x}"'
else
  SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_SRC="${REPO_ROOT}/resources/nav_agent/rosa_nav_bringup"

SCRIPT_SOURCED=0
if [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]:-$0}" != "$0" ]; then SCRIPT_SOURCED=1; fi
if [ -n "${ZSH_VERSION:-}" ]; then
  case "${ZSH_EVAL_CONTEXT:-}" in *:file*) SCRIPT_SOURCED=1 ;; esac
fi

echo "== NavAgent ROS2 resource loading =="
echo "repo: ${REPO_ROOT}"
if [ "${SCRIPT_SOURCED}" != "1" ]; then
  echo "Tip: source this script so the ROS overlay remains in the current shell."
fi

for required in \
  package.xml CMakeLists.txt \
  description/stretch_nav.urdf.xacro \
  config/controllers.yaml config/office_lab_layout.yaml config/nav2_params.yaml config/semantic_places.yaml \
  launch/nav_mujoco.launch.py \
  maps/office_lab.yaml maps/office_lab.pgm \
  mujoco/office_lab_scene.xml mujoco/generated/office_lab_world.xml \
  mujoco/hello_robot_stretch_3/stretch.xml mujoco/hello_robot_stretch_3/stretch_nav.xml
do
  if [ ! -e "${PACKAGE_SRC}/${required}" ]; then
    echo "Missing resource: ${PACKAGE_SRC}/${required}"
    return 1 2>/dev/null || exit 1
  fi
done

if ! python3 "${PACKAGE_SRC}/scripts/generate_office_lab.py" --check; then return 1 2>/dev/null || exit 1; fi
if ! python3 "${PACKAGE_SRC}/scripts/generate_stretch_nav.py" --check; then return 1 2>/dev/null || exit 1; fi

ROS_DISTRO="${ROS_DISTRO:-jazzy}"
ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
if [ -n "${ZSH_VERSION:-}" ] && [ -f "/opt/ros/${ROS_DISTRO}/setup.zsh" ]; then
  ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.zsh"
fi
if [ ! -f "${ROS_SETUP}" ]; then
  echo "ROS 2 setup not found: ${ROS_SETUP}"
  return 1 2>/dev/null || exit 1
fi
# shellcheck disable=SC1090
source "${ROS_SETUP}"

if ! command -v ros2 >/dev/null 2>&1 || ! command -v colcon >/dev/null 2>&1; then
  echo "ros2 and colcon are required"
  return 1 2>/dev/null || exit 1
fi

NAV_AGENT_WS="${NAV_AGENT_WS:-${REPO_ROOT}/.rosa/nav_ros2_ws}"
NAV_AGENT_WS="$(mkdir -p "${NAV_AGENT_WS}/src" && CDPATH= cd -- "${NAV_AGENT_WS}" && pwd)"
rm -rf "${NAV_AGENT_WS}/src/rosa_nav_bringup"
ln -sfn "${PACKAGE_SRC}" "${NAV_AGENT_WS}/src/rosa_nav_bringup"
echo "workspace: ${NAV_AGENT_WS}"

if [ "${NAV_AGENT_SKIP_ROSDEP:-0}" != "1" ] && command -v rosdep >/dev/null 2>&1; then
  rosdep install --from-paths "${NAV_AGENT_WS}/src" --ignore-src -r -y
  status=$?
  if [ "${status}" -ne 0 ]; then return "${status}" 2>/dev/null || exit "${status}"; fi
fi

INSTALLED_PACKAGE="${NAV_AGENT_WS}/install/rosa_nav_bringup/share/rosa_nav_bringup/package.xml"
NEED_BUILD=0
if [ "${NAV_AGENT_FORCE_BUILD:-0}" = "1" ] || [ ! -f "${INSTALLED_PACKAGE}" ]; then
  NEED_BUILD=1
elif find "${PACKAGE_SRC}" -type f -newer "${INSTALLED_PACKAGE}" -print -quit | grep -q .; then
  NEED_BUILD=1
fi

if [ "${NEED_BUILD}" = "1" ]; then
  (
    cd "${NAV_AGENT_WS}" || exit 1
    colcon build --symlink-install --packages-select rosa_nav_bringup
  )
  status=$?
  if [ "${status}" -ne 0 ]; then return "${status}" 2>/dev/null || exit "${status}"; fi
else
  echo "rosa_nav_bringup is already current"
fi

WS_SETUP="${NAV_AGENT_WS}/install/setup.bash"
if [ -n "${ZSH_VERSION:-}" ] && [ -f "${NAV_AGENT_WS}/install/setup.zsh" ]; then
  WS_SETUP="${NAV_AGENT_WS}/install/setup.zsh"
fi
if [ ! -f "${WS_SETUP}" ]; then
  echo "workspace setup not found: ${WS_SETUP}"
  return 1 2>/dev/null || exit 1
fi
# shellcheck disable=SC1090
source "${WS_SETUP}"

MISSING_RUNTIME=""
for package_name in \
  rosa_nav_bringup controller_manager diff_drive_controller joint_state_broadcaster \
  mujoco_ros2_control mujoco_ros2_control_msgs robot_state_publisher tf2_ros xacro \
  nav2_amcl nav2_map_server nav2_controller nav2_planner nav2_navfn_planner \
  nav2_regulated_pure_pursuit_controller nav2_behaviors nav2_bt_navigator \
  nav2_waypoint_follower nav2_velocity_smoother nav2_collision_monitor nav2_lifecycle_manager \
  nav2_simple_commander
do
  if ros2 pkg prefix "${package_name}" >/dev/null 2>&1; then
    echo "OK   ${package_name}"
  else
    echo "MISS ${package_name}"
    MISSING_RUNTIME="${MISSING_RUNTIME} ${package_name}"
  fi
done

if [ -n "${MISSING_RUNTIME}" ]; then
  echo "Missing runtime packages:${MISSING_RUNTIME}"
  return 1 2>/dev/null || exit 1
fi

echo "NavAgent ROS 2 resources loaded."
echo "Launch with: ros2 launch rosa_nav_bringup nav_mujoco.launch.py"
