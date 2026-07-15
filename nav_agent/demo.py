"""Run the reproducible NavAgent semantic waypoint demo against a live stack."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .places import PlaceCatalog
from .runtime import NavigationRuntimeClient


def _print(event: str, payload: dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destinations",
        nargs="*",
        default=["reception", "inspection", "charging"],
        help="ordered configured destination names or aliases",
    )
    parser.add_argument("--ready-timeout", type=float, default=90.0)
    parser.add_argument("--mission-timeout", type=float, default=240.0)
    parser.add_argument("--poll-period", type=float, default=0.5)
    args = parser.parse_args()

    client = NavigationRuntimeClient(operation_timeout=8.0, pose_stale_after=3.0)
    goal_id: str | None = None
    try:
        deadline = time.monotonic() + args.ready_timeout
        readiness: dict[str, Any] = {}
        while time.monotonic() < deadline:
            readiness = client.check_readiness()
            if readiness.get("success"):
                break
            time.sleep(0.5)
        if not readiness.get("success"):
            _print("not_ready", readiness)
            return 2
        _print("ready", {"status": readiness["status"], "missing": readiness["missing"]})

        mission = PlaceCatalog().resolve_mission(args.destinations)
        if not mission.get("success"):
            _print("rejected", mission)
            return 2
        stops = [
            {
                "id": place["id"],
                "display_name": place["display_name"],
                "resolved_pose": place["pose"],
            }
            for place in mission["stops"]
        ]
        started = client.start_waypoints(
            mission["poses"],
            failure_policy="stop",
            metadata={"semantic_stops": stops, "demo": "reception-inspection-charging"},
            task_timeout=args.mission_timeout,
        )
        if not started.get("success"):
            _print("rejected", started)
            return 1
        goal_id = started["goal_id"]
        _print("accepted", {"goal_id": goal_id, "stops": stops})

        previous_progress: tuple[Any, ...] | None = None
        while True:
            status = client.get_status(goal_id)
            feedback = status.get("feedback") or {}
            progress = (
                status.get("status"),
                feedback.get("current_waypoint"),
                round(float(feedback.get("distance_remaining", -1.0)), 2),
            )
            if progress != previous_progress:
                _print(
                    "status",
                    {
                        "goal_id": goal_id,
                        "status": status.get("status"),
                        "feedback": feedback,
                    },
                )
                previous_progress = progress
            if status.get("terminal"):
                _print("finished", status)
                return 0 if status.get("status") == "succeeded" else 1
            time.sleep(max(0.05, args.poll_period))
    except KeyboardInterrupt:
        if goal_id is not None:
            _print("cancelled", client.cancel(goal_id))
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
