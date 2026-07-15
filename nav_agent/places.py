"""Validated semantic destinations backed by the committed occupancy map."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import yaml
from PIL import Image

from .runtime import normalize_angle, pose_from_xy_yaw


CANONICAL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_place_name(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def default_catalog_path() -> Path:
    configured = os.environ.get("ROSA_NAV_PLACE_CATALOG")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        from ament_index_python.packages import get_package_share_directory

        installed = Path(get_package_share_directory("rosa_nav_bringup")) / "config/semantic_places.yaml"
        if installed.is_file():
            return installed
    except (ImportError, LookupError):
        pass
    return (
        Path(__file__).resolve().parents[1]
        / "resources/nav_agent/rosa_nav_bringup/config/semantic_places.yaml"
    )


class PlaceCatalog:
    """Load, validate, list, and resolve named map poses."""

    def __init__(
        self,
        catalog_path: Optional[str | Path] = None,
        *,
        map_yaml_path: Optional[str | Path] = None,
        strict_aliases: bool = True,
    ) -> None:
        self.catalog_path = Path(catalog_path or default_catalog_path()).resolve()
        if not self.catalog_path.is_file():
            raise ValueError(f"semantic place catalog does not exist: {self.catalog_path}")
        data = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("places"), list):
            raise ValueError("semantic place catalog must contain a places list")
        self.frame_id = data.get("frame_id")
        if self.frame_id != "map":
            raise ValueError("semantic place catalog frame_id must be 'map'")
        configured_map = map_yaml_path or data.get("map_yaml")
        if not configured_map:
            raise ValueError("semantic place catalog must reference map_yaml")
        candidate = Path(configured_map)
        self.map_yaml_path = (candidate if candidate.is_absolute() else self.catalog_path.parent / candidate).resolve()
        self._load_map()
        self._places: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._index: dict[str, list[dict[str, Any]]] = {}
        self._load_places(data["places"], strict_aliases=strict_aliases)

    def _load_map(self) -> None:
        if not self.map_yaml_path.is_file():
            raise ValueError(f"map YAML does not exist: {self.map_yaml_path}")
        metadata = yaml.safe_load(self.map_yaml_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("map YAML must be a mapping")
        try:
            self.resolution = float(metadata["resolution"])
            origin = metadata["origin"]
            self.origin_x, self.origin_y = float(origin[0]), float(origin[1])
            self.occupied_threshold = float(metadata["occupied_thresh"])
            self.free_threshold = float(metadata["free_thresh"])
            self.negate = bool(int(metadata.get("negate", 0)))
            image_value = metadata["image"]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"invalid map metadata: {exc}") from exc
        if self.resolution <= 0 or not all(
            math.isfinite(value)
            for value in (
                self.resolution,
                self.origin_x,
                self.origin_y,
                self.occupied_threshold,
                self.free_threshold,
            )
        ):
            raise ValueError("map metadata contains non-finite values or a non-positive resolution")
        image_path = Path(image_value)
        self.image_path = (image_path if image_path.is_absolute() else self.map_yaml_path.parent / image_path).resolve()
        if not self.image_path.is_file():
            raise ValueError(f"map image does not exist: {self.image_path}")
        with Image.open(self.image_path) as image:
            grayscale = image.convert("L")
            self.width, self.height = grayscale.size
            self._pixels = tuple(grayscale.tobytes())

    def _load_places(self, entries: list[Any], *, strict_aliases: bool) -> None:
        if not entries:
            raise ValueError("semantic place catalog is empty")
        for index, raw in enumerate(entries):
            if not isinstance(raw, dict):
                raise ValueError(f"place at index {index} must be a mapping")
            place_id = raw.get("id")
            display_name = raw.get("display_name")
            aliases = raw.get("aliases")
            pose = raw.get("pose")
            if not isinstance(place_id, str) or not CANONICAL_ID.fullmatch(place_id):
                raise ValueError(f"place at index {index} has an invalid canonical kebab-case id")
            if place_id in self._by_id:
                raise ValueError(f"duplicate place id: {place_id}")
            if not isinstance(display_name, str) or not display_name.strip():
                raise ValueError(f"place {place_id} has no display name")
            if not isinstance(aliases, list) or not aliases or not all(
                isinstance(alias, str) and normalize_place_name(alias) for alias in aliases
            ):
                raise ValueError(f"place {place_id} must define non-empty string aliases")
            if not isinstance(pose, dict):
                raise ValueError(f"place {place_id} has no map pose")
            try:
                x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"place {place_id} has an invalid map pose") from exc
            if not all(math.isfinite(value) for value in (x, y, yaw)):
                raise ValueError(f"place {place_id} pose must contain finite values")
            validation = self.validate_pose(x, y)
            if not validation["success"]:
                raise ValueError(f"place {place_id} is not navigable: {validation['error']}")
            normalized_aliases = []
            for alias in [place_id, display_name, *aliases]:
                normalized = normalize_place_name(alias)
                if normalized and normalized not in normalized_aliases:
                    normalized_aliases.append(normalized)
            place = {
                "id": place_id,
                "display_name": display_name.strip(),
                "aliases": list(aliases),
                "pose": pose_from_xy_yaw(x, y, normalize_angle(yaw)),
            }
            self._places.append(place)
            self._by_id[place_id] = place
            for alias in normalized_aliases:
                existing = self._index.setdefault(alias, [])
                if strict_aliases and existing and existing[0]["id"] != place_id:
                    raise ValueError(
                        f"alias {alias!r} is shared by {existing[0]['id']} and {place_id}"
                    )
                existing.append(place)

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        column = math.floor((float(x) - self.origin_x) / self.resolution)
        row_from_bottom = math.floor((float(y) - self.origin_y) / self.resolution)
        return self.height - 1 - row_from_bottom, column

    def validate_pose(self, x: float, y: float) -> dict[str, Any]:
        if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in (x, y)):
            return {"success": False, "status": "invalid", "error": "pose coordinates must be finite"}
        row, column = self.world_to_cell(float(x), float(y))
        if row < 0 or row >= self.height or column < 0 or column >= self.width:
            return {
                "success": False,
                "status": "out_of_bounds",
                "cell": {"row": row, "column": column},
                "error": "pose lies outside the occupancy map",
            }
        pixel = self._pixels[row * self.width + column] / 255.0
        occupancy = pixel if self.negate else 1.0 - pixel
        if occupancy > self.free_threshold:
            status = "occupied" if occupancy >= self.occupied_threshold else "unknown"
            return {
                "success": False,
                "status": status,
                "cell": {"row": row, "column": column},
                "occupancy": occupancy,
                "error": f"pose lies in a {status} map cell",
            }
        return {
            "success": True,
            "status": "free",
            "cell": {"row": row, "column": column},
            "occupancy": occupancy,
        }

    def list_destinations(self) -> dict[str, Any]:
        return {
            "success": True,
            "frame_id": self.frame_id,
            "destinations": [
                {
                    "id": place["id"],
                    "display_name": place["display_name"],
                    "aliases": list(place["aliases"]),
                    "pose": place["pose"],
                }
                for place in self._places
            ],
        }

    def resolve(self, name: str) -> dict[str, Any]:
        normalized = normalize_place_name(name)
        candidates = self._index.get(normalized, []) if normalized else []
        if len(candidates) == 1:
            place = candidates[0]
            return {"success": True, "status": "resolved", "query": name, "place": dict(place)}
        known = [place["id"] for place in self._places]
        if len(candidates) > 1:
            return {
                "success": False,
                "status": "ambiguous",
                "query": name,
                "candidates": [place["id"] for place in candidates],
                "known_destinations": known,
                "error": "destination name is ambiguous; clarification is required",
            }
        return {
            "success": False,
            "status": "unknown",
            "query": name,
            "known_destinations": known,
            "error": "destination is not configured; no coordinate was invented",
        }

    def resolve_mission(self, names: list[str]) -> dict[str, Any]:
        if not isinstance(names, list) or len(names) < 2:
            return {"success": False, "status": "rejected", "error": "a semantic mission requires at least two stops"}
        resolved: list[dict[str, Any]] = []
        for index, name in enumerate(names):
            result = self.resolve(name)
            if not result.get("success"):
                return {
                    "success": False,
                    "status": "rejected",
                    "invalid_stop": {"index": index, "name": name, "resolution": result},
                    "error": "semantic mission was rejected atomically; no waypoint was submitted",
                }
            resolved.append(result["place"])
        return {
            "success": True,
            "status": "resolved",
            "stops": resolved,
            "poses": [place["pose"] for place in resolved],
        }
