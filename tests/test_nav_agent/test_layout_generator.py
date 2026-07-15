from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "resources/nav_agent/rosa_nav_bringup"
SCRIPT_PATH = PACKAGE_ROOT / "scripts/generate_office_lab.py"
SPEC = importlib.util.spec_from_file_location("rosa_generate_office_lab", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
layout_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout_generator)


def test_committed_layout_outputs_are_current() -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    outputs = layout_generator.render_outputs(layout)

    assert layout_generator.check_outputs(PACKAGE_ROOT, outputs) == []
    assert outputs == layout_generator.render_outputs(copy.deepcopy(layout))
    assert b"storage_shelf" in outputs["world"]


def test_check_reports_every_missing_generated_output(tmp_path: Path) -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    outputs = layout_generator.render_outputs(layout)

    assert set(layout_generator.check_outputs(tmp_path, outputs)) == {
        tmp_path / relative for relative in layout_generator.OUTPUT_PATHS.values()
    }


def test_world_to_cell_and_map_bounds() -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    _, _, pixels = layout_generator.render_occupancy(layout)

    assert layout_generator.world_to_cell(layout, -5.0, -4.0) == (0, 0)
    assert layout_generator.world_to_cell(layout, 0.0, 0.0) == (100, 80)
    assert layout_generator.cell_is_occupied(layout, pixels, -4.95, 0.0)
    assert layout_generator.cell_is_occupied(layout, pixels, 5.1, 0.0)
    assert not layout_generator.cell_is_occupied(layout, pixels, 0.0, -2.8)


def test_every_semantic_place_is_in_a_free_cell() -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    _, _, pixels = layout_generator.render_occupancy(layout)

    for place in layout["places"]:
        pose = place["pose"]
        assert not layout_generator.cell_is_occupied(layout, pixels, pose["x"], pose["y"]), place["id"]


def test_duplicate_alias_is_rejected() -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    layout["places"][1]["aliases"].append("前台")

    with pytest.raises(layout_generator.LayoutError, match="belongs to both"):
        layout_generator.validate_layout(layout)


def test_occupied_semantic_place_is_rejected() -> None:
    layout = layout_generator.load_layout(PACKAGE_ROOT / "config/office_lab_layout.yaml")
    layout["places"][0]["pose"] = {"x": -4.95, "y": 0.0, "yaw": 0.0}

    with pytest.raises(layout_generator.LayoutError, match="outside the map or occupied"):
        layout_generator.render_outputs(layout)
