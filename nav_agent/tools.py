"""LangChain tools for persistent MuJoCo/Nav2 navigation."""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.tools import tool

from .places import PlaceCatalog
from .runtime import get_navigation_client, pose_from_xy_yaw


_CLIENT_PROVIDER: Callable[[], Any] = get_navigation_client
_CATALOG: Optional[PlaceCatalog] = None
_CATALOG_FACTORY: Callable[[], PlaceCatalog] = PlaceCatalog


def _client() -> Any:
    return _CLIENT_PROVIDER()


def _catalog() -> PlaceCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _CATALOG_FACTORY()
    return _CATALOG


def _readiness_error(require_readiness: bool) -> Optional[dict[str, Any]]:
    if not require_readiness:
        return None
    readiness = _client().check_readiness()
    if readiness.get("success"):
        return None
    return {
        "success": False,
        "status": "not_ready",
        "error": "navigation stack is not ready; no motion goal was submitted",
        "readiness": readiness,
    }


def _pose(x: float, y: float, yaw: float, frame_id: str) -> dict[str, Any] | dict[str, Any]:
    try:
        return pose_from_xy_yaw(x, y, yaw, frame_id)
    except (TypeError, ValueError) as exc:
        return {"success": False, "status": "rejected", "error": str(exc)}


@tool
def nav_check_readiness() -> dict:
    """Check simulation time, controllers, topics, TF, map/localization lifecycle, and Nav2 actions.

    Call this before starting any motion. A non-empty ``missing`` list means a
    navigation-start tool must not be called until the stack is repaired.
    """
    return _client().check_readiness()


@tool
def nav_get_current_pose() -> dict:
    """Return the current localized ``map`` to ``base_link`` pose, quaternion, yaw, and timestamp.

    This is read-only. Missing, timed-out, or stale TF is returned as a
    structured error; the tool never guesses a pose.
    """
    return _client().get_current_pose()


@tool
def nav_list_destinations() -> dict:
    """List configured semantic destinations, canonical IDs, bilingual aliases, and map poses.

    Listing destinations never initiates motion.
    """
    try:
        return _catalog().list_destinations()
    except ValueError as exc:
        return {"success": False, "status": "invalid_catalog", "error": str(exc)}


@tool
def nav_resolve_destination(name: str) -> dict:
    """Resolve one configured Chinese or English destination name without moving.

    Unknown or ambiguous names return candidates/known destinations and never
    invent coordinates.
    """
    try:
        return _catalog().resolve(name)
    except ValueError as exc:
        return {"success": False, "status": "invalid_catalog", "error": str(exc)}


@tool
def nav_start_pose_goal(
    x: float,
    y: float,
    yaw: float = 0.0,
    frame_id: str = "map",
    require_readiness: bool = True,
    task_timeout: Optional[float] = None,
) -> dict:
    """Start one asynchronous map-frame pose goal and immediately return its stable goal ID.

    ``x``/``y`` are metres and ``yaw`` is radians. Readiness is required by
    default. Only one pose or waypoint task may be active at once; use status
    and cancellation tools for the returned ID.
    """
    error = _readiness_error(require_readiness)
    if error:
        return error
    target = _pose(x, y, yaw, frame_id)
    if target.get("success") is False:
        return target
    try:
        validation = _catalog().validate_pose(float(x), float(y))
    except ValueError as exc:
        return {"success": False, "status": "invalid_catalog", "target": target, "error": str(exc)}
    if not validation.get("success"):
        return {
            "success": False,
            "status": "rejected",
            "target": target,
            "validation": validation,
            "error": "goal is not in a free map cell",
        }
    return _client().start_pose_goal(target, task_timeout=task_timeout)


@tool
def nav_start_named_goal(
    name: str,
    require_readiness: bool = True,
    task_timeout: Optional[float] = None,
) -> dict:
    """Resolve and start an asynchronous goal to one configured semantic destination.

    The result retains the canonical place ID, display name, and resolved pose.
    Unknown or ambiguous names submit no goal. Readiness is required by default.
    """
    error = _readiness_error(require_readiness)
    if error:
        return error
    try:
        resolution = _catalog().resolve(name)
    except ValueError as exc:
        return {"success": False, "status": "invalid_catalog", "error": str(exc)}
    if not resolution.get("success"):
        return resolution
    place = resolution["place"]
    return _client().start_pose_goal(
        place["pose"],
        metadata={
            "semantic_destination": {
                "id": place["id"],
                "display_name": place["display_name"],
                "resolved_pose": place["pose"],
            }
        },
        task_timeout=task_timeout,
    )


@tool
def nav_get_goal_status(goal_id: str = "") -> dict:
    """Poll an accepted pose/waypoint goal without submitting new motion.

    Returns normalized accepted, executing, succeeded, failed, cancelled,
    timeout, or unknown state plus feedback, original target, and terminal result.
    Omit ``goal_id`` to inspect the current active task.
    """
    return _client().get_status(goal_id or None)


@tool
def nav_cancel_goal(goal_id: str = "") -> dict:
    """Idempotently cancel an active NavAgent goal, or return an existing terminal state.

    Omit ``goal_id`` to cancel the current active task. Calling this for a
    succeeded, failed, or already-cancelled task never creates a new operation.
    """
    return _client().cancel(goal_id or None)


@tool
def nav_start_waypoint_mission(
    waypoints: list[dict[str, float]],
    failure_policy: str = "stop",
    require_readiness: bool = True,
    task_timeout: Optional[float] = None,
) -> dict:
    """Start one asynchronous ordered coordinate waypoint mission.

    Each item must contain finite map-frame ``x`` and ``y`` values and may
    contain ``yaw``. At least two free-map poses are required. ``failure_policy``
    is ``stop`` or ``continue`` and is retained in mission status metadata.
    """
    error = _readiness_error(require_readiness)
    if error:
        return error
    if not isinstance(waypoints, list):
        return {"success": False, "status": "rejected", "error": "waypoints must be a list"}
    poses = []
    for index, waypoint in enumerate(waypoints):
        if not isinstance(waypoint, dict) or "x" not in waypoint or "y" not in waypoint:
            return {"success": False, "status": "rejected", "invalid_waypoint": index, "error": "each waypoint needs x and y"}
        pose = _pose(waypoint["x"], waypoint["y"], waypoint.get("yaw", 0.0), "map")
        if pose.get("success") is False:
            return {**pose, "invalid_waypoint": index}
        try:
            validation = _catalog().validate_pose(float(waypoint["x"]), float(waypoint["y"]))
        except (TypeError, ValueError) as exc:
            return {"success": False, "status": "rejected", "invalid_waypoint": index, "error": str(exc)}
        if not validation.get("success"):
            return {
                "success": False,
                "status": "rejected",
                "invalid_waypoint": index,
                "validation": validation,
                "error": "all waypoints must occupy free map cells",
            }
        poses.append(pose)
    return _client().start_waypoints(
        poses,
        failure_policy=failure_policy,
        metadata={"coordinate_waypoints": list(waypoints)},
        task_timeout=task_timeout,
    )


@tool
def nav_start_semantic_mission(
    destinations: list[str],
    failure_policy: str = "stop",
    require_readiness: bool = True,
    task_timeout: Optional[float] = None,
) -> dict:
    """Atomically resolve and start an ordered mission through named destinations.

    Every Chinese/English stop is resolved and map-validated before Nav2 sees
    any waypoint. If one stop is unknown, ambiguous, occupied, or out of bounds,
    the entire mission is rejected and no motion is submitted.
    """
    error = _readiness_error(require_readiness)
    if error:
        return error
    try:
        mission = _catalog().resolve_mission(destinations)
    except ValueError as exc:
        return {"success": False, "status": "invalid_catalog", "error": str(exc)}
    if not mission.get("success"):
        return mission
    places = mission["stops"]
    return _client().start_waypoints(
        mission["poses"],
        failure_policy=failure_policy,
        metadata={
            "semantic_stops": [
                {
                    "id": place["id"],
                    "display_name": place["display_name"],
                    "resolved_pose": place["pose"],
                }
                for place in places
            ]
        },
        task_timeout=task_timeout,
    )
