from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nav_agent.places import PlaceCatalog, normalize_place_name


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "resources/nav_agent/rosa_nav_bringup"


def _catalog_data():
    return yaml.safe_load((PACKAGE / "config/semantic_places.yaml").read_text(encoding="utf-8"))


def _temporary_catalog(tmp_path, mutate=None):
    data = _catalog_data()
    data["map_yaml"] = str((PACKAGE / "maps/office_lab.yaml").resolve())
    if mutate:
        mutate(data)
    path = tmp_path / "places.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_list_and_bilingual_normalized_resolution() -> None:
    catalog = PlaceCatalog(PACKAGE / "config/semantic_places.yaml")

    listed = catalog.list_destinations()
    assert [item["id"] for item in listed["destinations"]] == [
        "reception",
        "storage",
        "inspection",
        "charging",
    ]
    assert catalog.resolve("  CHARGING   STATION ")["place"]["id"] == "charging"
    assert catalog.resolve("充电站")["place"]["id"] == "charging"
    assert normalize_place_name("  ＣＨＡＲＧＩＮＧ  ") == "charging"


def test_unknown_and_ambiguous_names_never_invent_coordinates(tmp_path) -> None:
    catalog = PlaceCatalog(PACKAGE / "config/semantic_places.yaml")
    unknown = catalog.resolve("lunch room")
    assert unknown["status"] == "unknown"
    assert "pose" not in unknown
    assert "charging" in unknown["known_destinations"]

    path = _temporary_catalog(
        tmp_path,
        lambda data: data["places"][1]["aliases"].append("desk")
        or data["places"][0]["aliases"].append("desk"),
    )
    ambiguous_catalog = PlaceCatalog(path, strict_aliases=False)
    ambiguous = ambiguous_catalog.resolve("desk")
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["candidates"] == ["reception", "storage"]
    assert "pose" not in ambiguous


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda data: data["places"][0].pop("id"), "canonical"),
        (lambda data: data["places"][0].update(id="Reception Desk"), "canonical"),
        (lambda data: data["places"][0]["pose"].update(x=float("nan")), "finite"),
        (lambda data: data["places"][0]["pose"].update(x=99.0), "outside"),
        (lambda data: data["places"][0]["pose"].update(x=-2.8, y=-1.35), "occupied"),
        (lambda data: data["places"][1]["aliases"].append("前台"), "shared"),
    ],
)
def test_invalid_catalog_entries_fail_before_use(tmp_path, mutation, match) -> None:
    with pytest.raises(ValueError, match=match):
        PlaceCatalog(_temporary_catalog(tmp_path, mutation))


def test_map_validation_and_yaw_normalization() -> None:
    catalog = PlaceCatalog(PACKAGE / "config/semantic_places.yaml")

    assert catalog.validate_pose(0.0, -2.8)["status"] == "free"
    assert catalog.validate_pose(99.0, 0.0)["status"] == "out_of_bounds"
    assert catalog.validate_pose(-2.8, -1.35)["status"] == "occupied"
    charging = catalog.resolve("charging")["place"]
    assert -3.141592653589793 <= charging["pose"]["yaw"] < 3.141592653589793


def test_semantic_mission_is_resolved_atomically_and_in_order() -> None:
    catalog = PlaceCatalog(PACKAGE / "config/semantic_places.yaml")

    mission = catalog.resolve_mission(["前台", "inspection", "充电站"])
    assert mission["success"] is True
    assert [place["id"] for place in mission["stops"]] == ["reception", "inspection", "charging"]

    rejected = catalog.resolve_mission(["reception", "moon base", "charging"])
    assert rejected["status"] == "rejected"
    assert rejected["invalid_stop"]["index"] == 1
    assert "poses" not in rejected
