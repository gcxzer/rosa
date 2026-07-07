cmake_minimum_required(VERSION 3.10.2)
project(moveit_resources_panda_moveit_config)
find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/MovePose.srv"
  DEPENDENCIES geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)

install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})
install(DIRECTORY config DESTINATION share/${PROJECT_NAME})
install(
  DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/../../mujoco_menagerie/franka_emika_panda"
  DESTINATION share/${PROJECT_NAME}/mujoco
)
install(
  PROGRAMS scripts/rosa_arm_moveit_server
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
