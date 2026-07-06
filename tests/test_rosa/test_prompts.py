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

from prompts.system import RobotSystemPrompts, render_system_prompt, system_prompts


def test_render_system_prompt_keeps_default_prompt_order():
    rendered = render_system_prompt()

    assert system_prompts[0][1] in rendered
    assert system_prompts[-1][1] in rendered
    assert rendered.index(system_prompts[0][1]) < rendered.index(system_prompts[-1][1])


def test_render_system_prompt_appends_robot_prompt_without_mutating_defaults():
    original_count = len(system_prompts)
    robot_prompt = RobotSystemPrompts(mission_and_objectives="只检查实验台 A。")

    rendered = render_system_prompt(robot_prompt)
    rendered_without_custom_prompt = render_system_prompt()

    assert "只检查实验台 A" in rendered
    assert "只检查实验台 A" not in rendered_without_custom_prompt
    assert len(system_prompts) == original_count
