from pathlib import Path
from xml.etree import ElementTree

import mujoco


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
    assert (panda_moveit_config / "launch" / "demo.launch.py").exists()

    # URDF 描述 link/joint，SRDF 描述 MoveIt planning group 和 named target。
    # 这两个 XML 能 parse，说明资源至少可以进入后续 ROS2/xacro/MoveIt 检查阶段。
    urdf_root = ElementTree.parse(panda_description / "urdf" / "panda.urdf").getroot()
    srdf_root = ElementTree.parse(panda_moveit_config / "config" / "panda.srdf").getroot()
    urdf_joint_names = {joint.attrib.get("name", "") for joint in urdf_root.findall("joint")}
    srdf_groups = {group.attrib.get("name", "") for group in srdf_root.findall("group")}

    assert {f"panda_joint{index}" for index in range(1, 8)}.issubset(urdf_joint_names)
    assert "panda_arm" in srdf_groups
    assert "hand" in srdf_groups
