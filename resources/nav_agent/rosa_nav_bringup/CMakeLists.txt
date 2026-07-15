cmake_minimum_required(VERSION 3.16)
project(rosa_nav_bringup)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY config description launch maps mujoco rviz test
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/generate_office_lab.py
    scripts/generate_stretch_nav.py
    scripts/reset_nav_sim.py
    scripts/run_nav_acceptance.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
