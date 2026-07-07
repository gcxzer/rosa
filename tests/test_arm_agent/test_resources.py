from pathlib import Path
from xml.etree import ElementTree

import mujoco
import yaml


def test_bundled_mujoco_panda_scene_loads():
    """确认 GitHub 仓库内置的 Panda MJCF 和 mesh assets 没有缺文件。"""
    repo_root = Path(__file__).resolve().parents[2]
    model_root = repo_root / "resources" / "arm_agent" / "mujoco_menagerie" / "franka_emika_panda"
    scene_path = model_root / "scene_moveit.xml"

    # MuJoCo 会在加载 scene_moveit.xml 时继续加载 panda_moveit.xml 和 assets 下的 mesh。
    # scene_moveit.xml 是后续接 MoveIt/ros2_control 的唯一保留场景，因此测试只覆盖它。
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    moveit_joint_names = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
        for joint_id in range(model.njnt)
    }

    assert model.nq == 9
    assert model.nv == 9
    assert model.nu == 8
    assert model.ngeom > 0
    assert {f"panda_joint{index}" for index in range(1, 8)}.issubset(moveit_joint_names)


def test_bundled_mujoco_tendon_actuators_match_joint_names():
    """确认 MuJoCo tendon actuator 能被 mujoco_ros2_control 映射到 ROS joint。

    `mujoco_ros2_control` 对普通 joint actuator 可以通过 `joint="..."` 反查 joint；
    但 tendon actuator 没有这个属性，它会强制要求 actuator 的 `name` 等于某个 joint 名。
    如果这里保留上游 Menagerie 的 `actuator8`，运行时会报：
    `Tendon actuator 'actuator8' has no matching joint`，MuJoCo 窗口也会闪退。
    """
    repo_root = Path(__file__).resolve().parents[2]
    model_path = (
        repo_root
        / "resources"
        / "arm_agent"
        / "mujoco_menagerie"
        / "franka_emika_panda"
        / "panda_moveit.xml"
    )
    root = ElementTree.parse(model_path).getroot()
    joint_names = {joint.attrib["name"] for joint in root.findall(".//joint") if "name" in joint.attrib}
    tendon_actuator_names = {
        actuator.attrib["name"]
        for actuator in root.findall(".//actuator/general")
        if "tendon" in actuator.attrib and "name" in actuator.attrib
    }

    assert tendon_actuator_names
    assert tendon_actuator_names.issubset(joint_names)


def test_bundled_moveit_panda_resources_are_present_and_parseable():
    """确认 GitHub 仓库内置的 Panda MoveIt URDF/SRDF/config 没有缺文件。"""
    repo_root = Path(__file__).resolve().parents[2]
    resource_root = repo_root / "resources" / "arm_agent"
    moveit_root = resource_root / "moveit_resources"
    panda_description = moveit_root / "panda_description"
    panda_moveit_config = moveit_root / "panda_moveit_config"

    assert (panda_description / "package.xml").exists()
    assert (panda_description / "urdf" / "panda.urdf").exists()
    assert (panda_description / "urdf" / "panda.urdf.xacro").exists()
    assert (panda_moveit_config / "package.xml").exists()
    assert (panda_moveit_config / "config" / "panda.srdf").exists()
    assert (panda_moveit_config / "config" / "ros2_controllers.yaml").exists()
    assert (panda_moveit_config / "config" / "arm_moveit_py.yaml").exists()
    assert (panda_moveit_config / "launch" / "demo.launch.py").exists()

    # URDF 描述 link/joint，SRDF 描述 MoveIt planning group 和 named target。
    # 这两个 XML 能 parse，说明资源至少可以进入后续 ROS2/xacro/MoveIt 检查阶段。
    urdf_root = ElementTree.parse(panda_description / "urdf" / "panda.urdf").getroot()
    srdf_root = ElementTree.parse(panda_moveit_config / "config" / "panda.srdf").getroot()
    urdf_joint_names = {joint.attrib.get("name", "") for joint in urdf_root.findall("joint")}
    srdf_groups = {group.attrib.get("name", "") for group in srdf_root.findall("group")}
    srdf_group_states = {
        (group_state.attrib.get("group", ""), group_state.attrib.get("name", ""))
        for group_state in srdf_root.findall("group_state")
    }
    moveit_py_config = yaml.safe_load((panda_moveit_config / "config" / "arm_moveit_py.yaml").read_text())

    assert {f"panda_joint{index}" for index in range(1, 8)}.issubset(urdf_joint_names)
    assert "panda_arm" in srdf_groups
    assert "hand" in srdf_groups
    assert {
        ("panda_arm", "home"),
        ("panda_arm", "ready"),
        ("panda_arm", "extended"),
        ("panda_arm", "transport"),
    }.issubset(srdf_group_states)
    assert "ompl" in moveit_py_config["planning_pipelines"]["pipeline_names"]
    assert moveit_py_config["plan_request_params"]["planning_pipeline"] == "ompl"
