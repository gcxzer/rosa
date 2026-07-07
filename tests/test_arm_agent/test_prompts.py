from arm_agent.prompts import ARM_SYSTEM_PROMPTS


def test_arm_prompt_covers_direct_move_frames_failure_handling_and_no_gazebo():
    prompt_text = str(ARM_SYSTEM_PROMPTS)

    assert "直接调用 arm_move_to_named_target" in prompt_text
    assert "不要再要求用户先拿 plan_id" in prompt_text
    assert "坐标系" in prompt_text
    assert "失败" in prompt_text
    assert "关节限制" in prompt_text
    assert "arm_open_gripper" in prompt_text
    assert "夹爪" in prompt_text
    assert "不使用 Gazebo" in prompt_text
