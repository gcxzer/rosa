#!/usr/bin/env python3
"""Generate a navigation-ready Stretch MJCF without editing the upstream model."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from xml.etree import ElementTree

import yaml


def build_model(source: Path, beams: int, initial_pose: dict[str, float]) -> bytes:
    if beams < 180:
        raise ValueError("LiDAR requires at least 180 beams")
    tree = ElementTree.parse(source)
    root = tree.getroot()

    compiler = root.find("compiler")
    if compiler is None:
        raise ValueError("upstream model has no compiler element")
    # MuJoCo resolves asset directories relative to the top-level scene, not the
    # included file.  The ROSA scene lives one directory above this model.
    compiler.set("meshdir", "hello_robot_stretch_3/assets")
    compiler.set("texturedir", "hello_robot_stretch_3/assets")

    base = root.find("./worldbody/body[@name='base_link']")
    if base is None:
        raise ValueError("upstream model has no base_link body")
    freejoint = base.find("freejoint")
    if freejoint is None:
        raise ValueError("upstream base_link has no freejoint")
    freejoint.set("name", "base_free_joint")

    tendon_actuator = root.find("./actuator/position[@name='arm']")
    if tendon_actuator is None or tendon_actuator.get("tendon") != "extend":
        raise ValueError("upstream Stretch tendon actuator changed")
    tendon_actuator.set("name", "joint_arm_l0")

    wheel_velocity = root.find("./default/default[@class='stretch']/default[@class='wheel']/velocity")
    if wheel_velocity is None:
        raise ValueError("upstream model has no wheel velocity defaults")
    # ros2_control commands joint velocity in rad/s. Menagerie's actuator gear
    # of three interprets that value in actuator space and divides wheel speed
    # by three, so use a direct transmission for the navigation controller.
    wheel_velocity.set("gear", "1")
    wheel_velocity.set("ctrlrange", "-8 8")
    wheel_joint = root.find("./default/default[@class='stretch']/default[@class='wheel']/joint")
    if wheel_joint is None:
        raise ValueError("upstream model has no wheel joint defaults")
    # The Menagerie damping is sized for its actuator-space transmission. With
    # direct joint-velocity commands it creates a 25% steady-state speed error.
    wheel_joint.set("damping", "2")
    wheel_joint.set("frictionloss", "0.1")
    wheel_joint.set("armature", "0.01")
    wheel_joint.set("stiffness", "0")

    # Navigation only consumes the planar LiDAR. Removing Menagerie's five RGB
    # and depth camera definitions prevents mujoco_ros2_control from starting
    # unnecessary rendering/publishing loops in headless mode.
    camera_count = 0
    for parent in root.iter():
        for camera in list(parent.findall("camera")):
            parent.remove(camera)
            camera_count += 1
    if camera_count == 0:
        raise ValueError("upstream model has no cameras to remove")

    stow = root.find("./keyframe/key[@name='stow']")
    if stow is None:
        raise ValueError("upstream model has no stow keyframe")
    yaw = float(initial_pose["yaw"])
    qpos = [
        float(initial_pose["x"]),
        float(initial_pose["y"]),
        0.0,
        math.cos(yaw / 2.0),
        0.0,
        0.0,
        math.sin(yaw / 2.0),
        0.0,
        0.0,
        0.23,
        0.0,
        0.0,
        0.0,
        0.0,
        3.14,
        -0.4,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    stow.set("qpos", " ".join(f"{value:.12g}" for value in qpos))

    # Put the rangefinder sites on Menagerie's existing laser body. MuJoCo
    # rangefinders ignore geometry on their own body; using a separate body at
    # the same pose would make every ray immediately hit the laser housing.
    lidar_body = base.find("body[@name='laser']")
    if lidar_body is None:
        raise ValueError("upstream model has no laser body")
    if lidar_body.find("site[@name='lidar_ray_000']") is not None:
        raise ValueError("upstream laser body already defines navigation LiDAR sites")

    sensor = root.find("sensor")
    if sensor is None:
        sensor = ElementTree.SubElement(root, "sensor")
    angle_increment = 2.0 * math.pi / beams
    for index in range(beams):
        angle = -math.pi + index * angle_increment
        # MuJoCo rangefinders cast along a site's *negative* Z axis. Point the
        # site Z axis opposite the LaserScan angle so array index and reported
        # angle describe the physical ray seen by Nav2.
        ray_axis = (-math.cos(angle), -math.sin(angle), 0.0)
        site_name = f"lidar_ray_{index:03d}"
        ElementTree.SubElement(
            lidar_body,
            "site",
            {
                "name": site_name,
                "size": "0.001",
                "zaxis": f"{ray_axis[0]:.12g} {ray_axis[1]:.12g} 0",
                "group": "4",
            },
        )
        ElementTree.SubElement(
            sensor,
            "rangefinder",
            {"name": f"lidar-{index:03d}", "site": site_name, "cutoff": "8.0", "noise": "0.005"},
        )

    ElementTree.indent(tree, space="  ")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def main(argv: list[str] | None = None) -> int:
    package_root = Path(__file__).resolve().parents[1]
    model_dir = package_root / "mujoco/hello_robot_stretch_3"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=model_dir / "stretch.xml")
    parser.add_argument("--output", type=Path, default=model_dir / "stretch_nav.xml")
    parser.add_argument("--layout", type=Path, default=package_root / "config/office_lab_layout.yaml")
    parser.add_argument("--beams", type=int, default=360)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        layout = yaml.safe_load(args.layout.resolve().read_text(encoding="utf-8"))
        initial_pose = layout["robot"]["initial_pose"]
        content = build_model(args.source.resolve(), args.beams, initial_pose)
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError, ElementTree.ParseError) as exc:
        print(f"Stretch navigation model generation failed: {exc}", file=sys.stderr)
        return 2

    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_bytes() != content:
            print(f"stale generated resource: {output}", file=sys.stderr)
            return 1
        print("Stretch navigation MJCF is current")
        return 0

    output.write_bytes(content)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
