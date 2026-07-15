#!/usr/bin/env python3
"""Generate the MuJoCo world, ROS map, and semantic places from one layout."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import yaml


OUTPUT_PATHS = {
    "world": Path("mujoco/generated/office_lab_world.xml"),
    "pgm": Path("maps/office_lab.pgm"),
    "map": Path("maps/office_lab.yaml"),
    "places": Path("config/semantic_places.yaml"),
}


class LayoutError(ValueError):
    """Raised when the versioned layout is incomplete or inconsistent."""


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise LayoutError(f"{label} must be finite")
    return number


def _pair(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise LayoutError(f"{label} must contain two numbers")
    return _number(value[0], f"{label}[0]"), _number(value[1], f"{label}[1]")


def normalize_alias(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def load_layout(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LayoutError("layout root must be a mapping")
    validate_layout(data)
    return data


def validate_layout(layout: dict[str, Any]) -> None:
    if layout.get("schema_version") != 1:
        raise LayoutError("schema_version must be 1")

    map_data = layout.get("map")
    if not isinstance(map_data, dict):
        raise LayoutError("map must be a mapping")
    width = _number(map_data.get("width"), "map.width")
    height = _number(map_data.get("height"), "map.height")
    resolution = _number(map_data.get("resolution"), "map.resolution")
    if width <= 0 or height <= 0 or resolution <= 0:
        raise LayoutError("map dimensions and resolution must be positive")
    origin = map_data.get("origin")
    if not isinstance(origin, list) or len(origin) != 3:
        raise LayoutError("map.origin must contain x, y, yaw")
    for index, value in enumerate(origin):
        _number(value, f"map.origin[{index}]")

    seen_objects: set[str] = set()
    for group in ("walls", "obstacles"):
        entries = layout.get(group)
        if not isinstance(entries, list) or not entries:
            raise LayoutError(f"{group} must be a non-empty list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise LayoutError(f"{group} entries must be mappings")
            name = str(entry.get("name", "")).strip()
            if not name or name in seen_objects:
                raise LayoutError(f"invalid or duplicate object name: {name!r}")
            seen_objects.add(name)
            _pair(entry.get("center"), f"{name}.center")
            sx, sy = _pair(entry.get("size"), f"{name}.size")
            if sx <= 0 or sy <= 0 or _number(entry.get("height"), f"{name}.height") <= 0:
                raise LayoutError(f"{name} dimensions must be positive")

    places = layout.get("places")
    if not isinstance(places, list) or not places:
        raise LayoutError("places must be a non-empty list")
    ids: set[str] = set()
    aliases: dict[str, str] = {}
    for place in places:
        if not isinstance(place, dict):
            raise LayoutError("place entries must be mappings")
        place_id = str(place.get("id", "")).strip()
        if not place_id or place_id in ids:
            raise LayoutError(f"invalid or duplicate place id: {place_id!r}")
        if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in place_id):
            raise LayoutError(f"place id must be kebab-case: {place_id!r}")
        ids.add(place_id)
        if not str(place.get("display_name", "")).strip():
            raise LayoutError(f"{place_id} must have a display_name")
        pose = place.get("pose")
        if not isinstance(pose, dict):
            raise LayoutError(f"{place_id}.pose must be a mapping")
        for field in ("x", "y", "yaw"):
            _number(pose.get(field), f"{place_id}.pose.{field}")
        values = [place_id, *(place.get("aliases") or [])]
        if len(values) == 1:
            raise LayoutError(f"{place_id} must define at least one alias")
        for raw_alias in values:
            alias = normalize_alias(str(raw_alias))
            if not alias:
                raise LayoutError(f"{place_id} contains an empty alias")
            owner = aliases.get(alias)
            if owner is not None and owner != place_id:
                raise LayoutError(f"alias {raw_alias!r} belongs to both {owner} and {place_id}")
            aliases[alias] = place_id


def map_shape(layout: dict[str, Any]) -> tuple[int, int]:
    map_data = layout["map"]
    resolution = float(map_data["resolution"])
    width = round(float(map_data["width"]) / resolution)
    height = round(float(map_data["height"]) / resolution)
    if not math.isclose(width * resolution, float(map_data["width"]), abs_tol=1e-9):
        raise LayoutError("map width must be divisible by resolution")
    if not math.isclose(height * resolution, float(map_data["height"]), abs_tol=1e-9):
        raise LayoutError("map height must be divisible by resolution")
    return width, height


def world_to_cell(layout: dict[str, Any], x: float, y: float) -> tuple[int, int]:
    map_data = layout["map"]
    origin_x, origin_y, _ = map(float, map_data["origin"])
    resolution = float(map_data["resolution"])
    return math.floor((x - origin_x) / resolution), math.floor((y - origin_y) / resolution)


def render_occupancy(layout: dict[str, Any]) -> tuple[int, int, bytes]:
    width, height = map_shape(layout)
    pixels = bytearray([254]) * (width * height)

    for entry in [*layout["walls"], *layout["obstacles"]]:
        cx, cy = map(float, entry["center"])
        sx, sy = map(float, entry["size"])
        min_x, min_y = world_to_cell(layout, cx - sx / 2.0, cy - sy / 2.0)
        max_x, max_y = world_to_cell(layout, cx + sx / 2.0, cy + sy / 2.0)
        for grid_y in range(max(0, min_y), min(height, max_y + 1)):
            image_y = height - 1 - grid_y
            start = image_y * width + max(0, min_x)
            stop = image_y * width + min(width, max_x + 1)
            pixels[start:stop] = bytes([0]) * max(0, stop - start)
    return width, height, bytes(pixels)


def cell_is_occupied(layout: dict[str, Any], pixels: bytes, x: float, y: float) -> bool:
    width, height = map_shape(layout)
    grid_x, grid_y = world_to_cell(layout, x, y)
    if grid_x < 0 or grid_x >= width or grid_y < 0 or grid_y >= height:
        return True
    return pixels[(height - 1 - grid_y) * width + grid_x] < 128


def _fmt(value: float) -> str:
    return f"{float(value):.10g}"


def render_world(layout: dict[str, Any]) -> bytes:
    lines = [
        '<mujoco model="rosa office laboratory world">',
        "  <worldbody>",
        '    <light name="ceiling_light" pos="0 0 4" dir="0 0 -1" directional="true"/>',
        '    <geom name="floor" type="plane" size="0 0 0.05" rgba="0.72 0.74 0.76 1" friction="1.2 0.01 0.001"/>',
    ]
    for entry in [*layout["walls"], *layout["obstacles"]]:
        cx, cy = map(float, entry["center"])
        sx, sy = map(float, entry["size"])
        height = float(entry["height"])
        color = entry.get("color", [0.78, 0.80, 0.82, 1.0])
        rgba = " ".join(_fmt(float(value)) for value in color)
        lines.append(
            "    <geom"
            f" name={quoteattr(str(entry['name']))} type=\"box\""
            f" pos=\"{_fmt(cx)} {_fmt(cy)} {_fmt(height / 2.0)}\""
            f" size=\"{_fmt(sx / 2.0)} {_fmt(sy / 2.0)} {_fmt(height / 2.0)}\""
            f" rgba=\"{rgba}\" friction=\"0.9 0.01 0.001\"/>"
        )
    lines.extend(["  </worldbody>", "</mujoco>", ""])
    return "\n".join(lines).encode("utf-8")


def render_outputs(layout: dict[str, Any]) -> dict[str, bytes]:
    width, height, pixels = render_occupancy(layout)
    for place in layout["places"]:
        pose = place["pose"]
        if cell_is_occupied(layout, pixels, float(pose["x"]), float(pose["y"])):
            raise LayoutError(f"semantic place {place['id']!r} is outside the map or occupied")

    pgm = f"P5\n{width} {height}\n255\n".encode("ascii") + pixels
    map_data = layout["map"]
    map_yaml = {
        "image": "office_lab.pgm",
        "mode": "trinary",
        "resolution": float(map_data["resolution"]),
        "origin": [float(value) for value in map_data["origin"]],
        "negate": 0,
        "occupied_thresh": float(map_data["occupied_thresh"]),
        "free_thresh": float(map_data["free_thresh"]),
    }
    places_yaml = {
        "schema_version": 1,
        "frame_id": str(map_data.get("frame_id", "map")),
        "map_yaml": "../maps/office_lab.yaml",
        "places": layout["places"],
    }
    return {
        "world": render_world(layout),
        "pgm": pgm,
        "map": yaml.safe_dump(map_yaml, sort_keys=False, allow_unicode=True).encode("utf-8"),
        "places": yaml.safe_dump(places_yaml, sort_keys=False, allow_unicode=True).encode("utf-8"),
    }


def write_outputs(package_root: Path, outputs: dict[str, bytes]) -> None:
    for key, content in outputs.items():
        path = package_root / OUTPUT_PATHS[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def check_outputs(package_root: Path, outputs: dict[str, bytes]) -> list[Path]:
    stale: list[Path] = []
    for key, content in outputs.items():
        path = package_root / OUTPUT_PATHS[key]
        if not path.is_file() or path.read_bytes() != content:
            stale.append(path)
    return stale


def main(argv: list[str] | None = None) -> int:
    package_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=package_root)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = args.package_root.resolve()
    layout_path = (args.layout or root / "config/office_lab_layout.yaml").resolve()
    try:
        outputs = render_outputs(load_layout(layout_path))
    except (OSError, LayoutError, yaml.YAMLError) as exc:
        print(f"layout generation failed: {exc}", file=sys.stderr)
        return 2

    if args.check:
        stale = check_outputs(root, outputs)
        if stale:
            for path in stale:
                print(f"stale generated resource: {path}", file=sys.stderr)
            return 1
        print("office/laboratory generated resources are current")
        return 0

    write_outputs(root, outputs)
    for key in OUTPUT_PATHS:
        print(root / OUTPUT_PATHS[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
