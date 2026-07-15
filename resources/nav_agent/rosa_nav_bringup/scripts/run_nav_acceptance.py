#!/usr/bin/env python3
"""Entry point for opt-in ROSA NavAgent headless acceptance checks."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    try:
        from ament_index_python.packages import get_package_share_directory
    except ImportError as exc:
        print(f"ROS 2 environment is unavailable: {exc}", file=sys.stderr)
        return 2
    test_script = os.path.join(
        get_package_share_directory("rosa_nav_bringup"), "test", "headless_nav_acceptance.py"
    )
    env = dict(os.environ)
    env["ROSA_NAV_INTEGRATION"] = "1"
    return subprocess.call([sys.executable, test_script], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
