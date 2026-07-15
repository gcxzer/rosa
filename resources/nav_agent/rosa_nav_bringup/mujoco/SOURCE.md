# Stretch 3 MuJoCo model provenance

- Upstream: https://github.com/google-deepmind/mujoco_menagerie
- Model directory: `hello_robot_stretch_3`
- Revision: `71f066ad0be9cd271f7ed58c030243ef157af9f4`
- Vendored on: 2026-07-15

The upstream model is kept unchanged in `hello_robot_stretch_3/stretch.xml`.
`scripts/generate_stretch_nav.py` creates `stretch_nav.xml` from that source with
the small compatibility additions required by `mujoco_ros2_control`: a named
free joint, a tendon-actuator name that maps to a joint, and a planar LiDAR made
from MuJoCo rangefinders.

See `hello_robot_stretch_3/LICENSE` for the upstream model license.
