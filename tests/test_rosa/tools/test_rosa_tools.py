#  Copyright (c) 2024. Jet Propulsion Laboratory. All rights reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import unittest

from langchain_core.tools import tool

from src.rosa.tools import ROSATools, inject_blacklist


@tool
def sample_tool(blacklist=None):
    """返回黑名单的示例工具。"""
    return blacklist


class TestROSATools(unittest.TestCase):
    def test_initializes_with_ros_version_2(self):
        tools = ROSATools(ros_version=2)
        self.assertEqual(tools._ROSATools__ros_version, 2)

    def test_raises_value_error_for_invalid_ros_version(self):
        with self.assertRaisesRegex(ValueError, "仅支持 ROS2"):
            ROSATools(ros_version=1)

    def test_adds_default_ros2_tools(self):
        tools = ROSATools(ros_version=2)
        tool_names = {tool.name for tool in tools.get_tools()}
        self.assertIn("ros2_node_list", tool_names)
        self.assertIn("ros2_topic_list", tool_names)
        self.assertIn("ros2_service_list", tool_names)
        self.assertIn("wait", tool_names)
        self.assertIn("add", tool_names)
        self.assertIn("read_log", tool_names)

    def test_injects_blacklist_into_tool_function(self):
        def sample_tool(blacklist=None):
            return blacklist

        decorated_tool = inject_blacklist(["item1", "item2"])(sample_tool)
        self.assertEqual(decorated_tool(), ["item1", "item2"])

    def test_blacklist_gets_concatenated(self):
        decorated_tool = inject_blacklist(["item1", "item2"])(sample_tool)
        self.assertEqual(
            decorated_tool({"blacklist": ["item3"]}),
            ["item1", "item2", "item3"],
        )


class TestROSA2Tools(unittest.TestCase):
    def test_ros2_tools(self):
        tools = ROSATools(ros_version=2)
        tool_names = {tool.name for tool in tools.get_tools()}
        self.assertIn("ros2_doctor", tool_names)
        self.assertIn("ros2_param_list", tool_names)


if __name__ == "__main__":
    unittest.main()
