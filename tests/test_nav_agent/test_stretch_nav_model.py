from __future__ import annotations

import importlib.util
from pathlib import Path
from xml.etree import ElementTree


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "resources/nav_agent/rosa_nav_bringup"
SCRIPT_PATH = PACKAGE_ROOT / "scripts/generate_stretch_nav.py"
SPEC = importlib.util.spec_from_file_location("rosa_generate_stretch_nav", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
model_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model_generator)


def test_committed_navigation_model_is_current() -> None:
    source = PACKAGE_ROOT / "mujoco/hello_robot_stretch_3/stretch.xml"
    output = PACKAGE_ROOT / "mujoco/hello_robot_stretch_3/stretch_nav.xml"
    layout = __import__("yaml").safe_load((PACKAGE_ROOT / "config/office_lab_layout.yaml").read_text())

    assert output.read_bytes() == model_generator.build_model(source, 360, layout["robot"]["initial_pose"])


def test_navigation_model_has_expected_control_and_lidar_contract() -> None:
    path = PACKAGE_ROOT / "mujoco/hello_robot_stretch_3/stretch_nav.xml"
    root = ElementTree.parse(path).getroot()

    assert root.find("./worldbody/body[@name='base_link']/freejoint").get("name") == "base_free_joint"
    assert root.find("./actuator/position[@name='joint_arm_l0']").get("tendon") == "extend"
    wheel_velocity = root.find("./default/default[@class='stretch']/default[@class='wheel']/velocity")
    assert wheel_velocity.get("gear") == "1"
    assert wheel_velocity.get("ctrlrange") == "-8 8"
    wheel_joint = root.find("./default/default[@class='stretch']/default[@class='wheel']/joint")
    assert wheel_joint.get("damping") == "2"
    assert wheel_joint.get("frictionloss") == "0.1"
    assert len(root.findall("./sensor/rangefinder")) == 360
    assert root.find("./sensor/rangefinder[@name='lidar-000']") is not None
    assert root.find("./sensor/rangefinder[@name='lidar-359']") is not None
    assert root.find("./worldbody/body[@name='base_link']/body[@name='laser']/site[@name='lidar_ray_000']") is not None
    assert root.find("./worldbody/body[@name='base_link']/body[@name='laser']/site[@name='lidar_ray_000']").get("zaxis") == "1 1.22464679915e-16 0"
    assert root.find("./worldbody/body[@name='base_link']/body[@name='lidar_link']") is None
    assert root.find(".//camera") is None

    stow = root.find("./keyframe/key[@name='stow']")
    qpos = [float(value) for value in stow.get("qpos").split()]
    assert qpos[:3] == [0.0, -2.8, 0.0]
    assert qpos[9] == 0.23
