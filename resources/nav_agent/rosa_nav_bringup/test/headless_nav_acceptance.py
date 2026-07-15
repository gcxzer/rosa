#!/usr/bin/env python3
"""Opt-in headless ROS integration entry point.

The full checks live in ``tests/integration/test_nav_mujoco_stack.py`` so they
can share the repository's pytest fixtures.  This installed entry point keeps
the ROS package self-describing and refuses to claim success unless integration
testing was explicitly enabled.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if os.environ.get("ROSA_NAV_INTEGRATION") != "1":
        print("Set ROSA_NAV_INTEGRATION=1 to run the MuJoCo/Nav2 acceptance suite.")
        return 2
    repo_root = Path(os.environ.get("ROSA_REPO_ROOT", Path.cwd())).resolve()
    test_file = repo_root / "tests/integration/test_nav_mujoco_stack.py"
    if not test_file.is_file():
        print(f"acceptance test not found: {test_file}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, "-m", "pytest", "-q", str(test_file)], cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
