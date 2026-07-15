"""Persistent navigation task state independent of ROS imports.

The runtime owns the user-visible goal lifecycle.  A small injectable adapter
owns ROS/Nav2 communication, which keeps importing and unit-testing NavAgent
possible on machines that have no sourced ROS installation.
"""

from __future__ import annotations

import atexit
import math
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


TERMINAL_STATES = {"succeeded", "failed", "cancelled", "timeout", "unknown"}
ACTIVE_STATES = {"accepted", "executing"}


def normalize_angle(angle: float) -> float:
    """Normalize radians to [-pi, pi)."""
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def pose_from_xy_yaw(x: float, y: float, yaw: float, frame_id: str = "map") -> dict[str, Any]:
    values = (x, y, yaw)
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values):
        raise ValueError("pose x, y, and yaw must be finite numbers")
    if frame_id != "map":
        raise ValueError("navigation goals must use the map frame")
    yaw = normalize_angle(float(yaw))
    return {
        "frame_id": "map",
        "position": {"x": float(x), "y": float(y), "z": 0.0},
        "orientation": {
            "x": 0.0,
            "y": 0.0,
            "z": math.sin(yaw / 2.0),
            "w": math.cos(yaw / 2.0),
        },
        "yaw": yaw,
    }


@dataclass
class _Task:
    goal_id: str
    kind: str
    target: Any
    backend_handle: Any
    status: str = "accepted"
    feedback: dict[str, Any] = field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.time)
    timeout: Optional[float] = None

    def public(self) -> dict[str, Any]:
        return {
            "success": True,
            "goal_id": self.goal_id,
            "kind": self.kind,
            "status": self.status,
            "target": self.target,
            "metadata": self.metadata,
            "feedback": self.feedback,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal": self.status in TERMINAL_STATES,
        }


class NavigationRuntimeClient:
    """Manage one asynchronous Nav2 task and a bounded terminal history."""

    def __init__(
        self,
        *,
        adapter: Any = None,
        adapter_factory: Optional[Callable[[], Any]] = None,
        operation_timeout: float = 3.0,
        pose_stale_after: float = 2.0,
        history_limit: int = 32,
    ) -> None:
        self._adapter = adapter
        self._adapter_factory = adapter_factory
        self.operation_timeout = float(operation_timeout)
        self.pose_stale_after = float(pose_stale_after)
        self.history_limit = max(1, int(history_limit))
        self._tasks: OrderedDict[str, _Task] = OrderedDict()
        self._active_goal_id: Optional[str] = None
        self._lock = threading.RLock()
        self._closed = False

    @property
    def adapter(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("navigation runtime is closed")
            if self._adapter is None:
                if self._adapter_factory is not None:
                    self._adapter = self._adapter_factory()
                else:
                    # Importing this module never imports rclpy.  ROS is loaded
                    # only when an operation first needs the real adapter.
                    from .ros_adapter import ROS2NavigationAdapter

                    self._adapter = ROS2NavigationAdapter(timeout=self.operation_timeout)
            return self._adapter

    def check_readiness(self, timeout: Optional[float] = None) -> dict[str, Any]:
        try:
            result = self.adapter.check_readiness(timeout or self.operation_timeout)
        except Exception as exc:  # runtime boundary must always remain structured
            return {
                "success": False,
                "status": "runtime_error",
                "missing": ["ROS 2 navigation runtime"],
                "detected": {},
                "error": str(exc),
            }
        missing = list(result.get("missing") or [])
        return {
            **result,
            "success": bool(result.get("success")) and not missing,
            "missing": missing,
            "status": "ready" if bool(result.get("success")) and not missing else "not_ready",
        }

    def get_current_pose(self, timeout: Optional[float] = None) -> dict[str, Any]:
        try:
            result = self.adapter.lookup_pose(
                target_frame="map",
                source_frame="base_link",
                timeout=timeout or self.operation_timeout,
            )
        except Exception as exc:
            return {
                "success": False,
                "status": "runtime_error",
                "frame_id": "map",
                "error": str(exc),
            }
        if not result.get("success"):
            return {
                "success": False,
                "status": result.get("status", "timeout"),
                "frame_id": "map",
                "error": result.get("error", "map to base_link transform is unavailable"),
                **{key: value for key, value in result.items() if key not in {"success", "status", "error"}},
            }

        quaternion = dict(result.get("orientation") or {})
        try:
            qx, qy, qz, qw = (float(quaternion[key]) for key in ("x", "y", "z", "w"))
        except (KeyError, TypeError, ValueError):
            return {"success": False, "status": "invalid_transform", "error": "TF returned an invalid quaternion"}
        norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
        if not math.isfinite(norm) or norm <= 1e-12:
            return {"success": False, "status": "invalid_transform", "error": "TF returned a zero quaternion"}
        qx, qy, qz, qw = (value / norm for value in (qx, qy, qz, qw))
        yaw = normalize_angle(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))
        age = result.get("age")
        if isinstance(age, (int, float)) and age > self.pose_stale_after:
            return {
                "success": False,
                "status": "stale",
                "frame_id": "map",
                "timestamp": result.get("timestamp"),
                "age": float(age),
                "error": f"map to base_link transform is stale ({float(age):.3f}s)",
            }
        return {
            "success": True,
            "status": "localized",
            "localization_state": result.get("localization_state", "active"),
            "frame_id": "map",
            "child_frame_id": "base_link",
            "timestamp": result.get("timestamp"),
            "age": age,
            "position": dict(result.get("position") or {}),
            "orientation": {"x": qx, "y": qy, "z": qz, "w": qw},
            "yaw": yaw,
        }

    def start_pose_goal(
        self,
        pose: dict[str, Any],
        *,
        metadata: Optional[dict[str, Any]] = None,
        task_timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        return self._start("pose", pose, [pose], metadata or {}, task_timeout)

    def start_waypoints(
        self,
        poses: list[dict[str, Any]],
        *,
        metadata: Optional[dict[str, Any]] = None,
        failure_policy: str = "stop",
        task_timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        if len(poses) < 2:
            return {
                "success": False,
                "status": "rejected",
                "target": poses,
                "error": "a waypoint mission requires at least two poses",
            }
        if failure_policy not in {"stop", "continue"}:
            return {
                "success": False,
                "status": "rejected",
                "target": poses,
                "error": "failure_policy must be 'stop' or 'continue'",
            }
        mission_metadata = {**(metadata or {}), "failure_policy": failure_policy, "waypoint_count": len(poses)}
        return self._start("waypoints", poses, poses, mission_metadata, task_timeout)

    def _start(
        self,
        kind: str,
        target: Any,
        poses: list[dict[str, Any]],
        metadata: dict[str, Any],
        task_timeout: Optional[float],
    ) -> dict[str, Any]:
        with self._lock:
            active = self._active_task_locked(refresh=True)
            if active is not None:
                return {
                    "success": False,
                    "status": "busy",
                    "target": target,
                    "active_goal_id": active.goal_id,
                    "error": "another navigation task is active; cancel it before starting a new task",
                }
            try:
                submission = (
                    self.adapter.submit_pose(poses[0], timeout=self.operation_timeout)
                    if kind == "pose"
                    else self.adapter.submit_waypoints(poses, timeout=self.operation_timeout)
                )
            except Exception as exc:
                return {
                    "success": False,
                    "status": "runtime_error",
                    "target": target,
                    "error": str(exc),
                }
            if not submission.get("accepted"):
                return {
                    "success": False,
                    "status": "rejected",
                    "target": target,
                    "error": submission.get("reason", "Nav2 rejected the goal"),
                }
            goal_id = f"nav-{uuid.uuid4().hex[:12]}"
            task = _Task(
                goal_id=goal_id,
                kind=kind,
                target=target,
                backend_handle=submission.get("handle"),
                metadata=metadata,
                timeout=float(task_timeout) if task_timeout is not None else None,
            )
            self._tasks[goal_id] = task
            self._active_goal_id = goal_id
            return task.public()

    def get_status(self, goal_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            selected_id = goal_id or self._active_goal_id
            if not selected_id:
                return {"success": False, "status": "unknown", "error": "no navigation goal was specified or active"}
            task = self._tasks.get(selected_id)
            if task is None:
                return {
                    "success": False,
                    "status": "unknown",
                    "goal_id": selected_id,
                    "error": "navigation goal is not present in the bounded runtime history",
                }
            self._refresh_locked(task)
            return task.public()

    def cancel(self, goal_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            selected_id = goal_id or self._active_goal_id
            if not selected_id:
                return {"success": False, "status": "unknown", "error": "no navigation goal was specified or active"}
            task = self._tasks.get(selected_id)
            if task is None:
                return {"success": False, "status": "unknown", "goal_id": selected_id, "error": "goal is unknown"}
            self._refresh_locked(task)
            if task.status in TERMINAL_STATES:
                return task.public()
            try:
                cancellation = self.adapter.cancel(task.backend_handle, timeout=self.operation_timeout)
            except Exception as exc:
                return {**task.public(), "success": False, "status": "runtime_error", "error": str(exc)}
            if not cancellation.get("cancelled"):
                return {
                    **task.public(),
                    "success": False,
                    "error": cancellation.get("reason", "Nav2 did not acknowledge cancellation"),
                }
            self._finish_locked(task, "cancelled", cancellation.get("result") or {"reason": "cancel acknowledged"})
            return task.public()

    def _active_task_locked(self, *, refresh: bool) -> Optional[_Task]:
        if not self._active_goal_id:
            return None
        task = self._tasks.get(self._active_goal_id)
        if task is None:
            self._active_goal_id = None
            return None
        if refresh:
            self._refresh_locked(task)
        return task if task.status in ACTIVE_STATES else None

    def _refresh_locked(self, task: _Task) -> None:
        if task.status in TERMINAL_STATES:
            return
        if task.timeout is not None and time.monotonic() - task.started_monotonic > task.timeout:
            try:
                self.adapter.cancel(task.backend_handle, timeout=self.operation_timeout)
            except Exception:
                pass
            self._finish_locked(task, "timeout", {"error": "navigation task exceeded its configured timeout"})
            return
        try:
            update = self.adapter.poll(task.backend_handle)
        except Exception as exc:
            self._finish_locked(task, "unknown", {"error": str(exc), "kind": "runtime_error"})
            return
        state = str(update.get("state", task.status)).lower()
        aliases = {
            "active": "executing",
            "running": "executing",
            "accepted": "accepted",
            "executing": "executing",
            "success": "succeeded",
            "succeeded": "succeeded",
            "aborted": "failed",
            "failed": "failed",
            "canceled": "cancelled",
            "cancelled": "cancelled",
            "timeout": "timeout",
        }
        state = aliases.get(state, "unknown")
        task.feedback = dict(update.get("feedback") or task.feedback)
        task.updated_at = time.time()
        if state in TERMINAL_STATES:
            self._finish_locked(task, state, dict(update.get("result") or {}))
        else:
            task.status = state

    def _finish_locked(self, task: _Task, status: str, result: dict[str, Any]) -> None:
        task.status = status
        task.result = result
        task.updated_at = time.time()
        if self._active_goal_id == task.goal_id:
            self._active_goal_id = None
        terminal_ids = [goal_id for goal_id, item in self._tasks.items() if item.status in TERMINAL_STATES]
        while len(terminal_ids) > self.history_limit:
            self._tasks.pop(terminal_ids.pop(0), None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            active = self._active_task_locked(refresh=False)
            if active is not None:
                try:
                    self.adapter.cancel(active.backend_handle, timeout=self.operation_timeout)
                    self._finish_locked(active, "cancelled", {"reason": "runtime shutdown"})
                except Exception:
                    pass
            if self._adapter is not None:
                try:
                    self._adapter.close()
                except Exception:
                    pass
            self._closed = True

    def __enter__(self) -> "NavigationRuntimeClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


_CLIENT: Optional[NavigationRuntimeClient] = None
_CLIENT_FACTORY: Callable[[], NavigationRuntimeClient] = NavigationRuntimeClient
_CLIENT_LOCK = threading.Lock()


def get_navigation_client() -> NavigationRuntimeClient:
    """Return the process-local lazy singleton used by every NavAgent tool."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = _CLIENT_FACTORY()
        return _CLIENT


def reset_navigation_client() -> None:
    """Close and forget the singleton; primarily useful for tests and clean reloads."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.close()
        _CLIENT = None


atexit.register(reset_navigation_client)
