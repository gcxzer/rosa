from turtle_agent import tools as turtle_tools


def test_turtle_get_pose_parses_ros2_topic_echo(monkeypatch):
    def fake_run_ros2(args, timeout=10.0):
        assert args[:4] == ["ros2", "topic", "echo", "/turtle1/pose"]
        return (
            True,
            """
            x: 5.5
            y: 6.25
            theta: 1.57
            linear_velocity: 0.0
            angular_velocity: 0.0
            ---
            """,
        )

    monkeypatch.setattr(turtle_tools, "_run_ros2", fake_run_ros2)

    result = turtle_tools.turtle_get_pose.invoke({"name": "turtle1"})

    assert result["success"] is True
    assert result["pose"]["name"] == "turtle1"
    assert result["pose"]["x"] == 5.5
    assert result["pose"]["theta"] == 1.57


def test_turtle_teleport_absolute_hides_pen_and_calls_service(monkeypatch):
    calls = []

    def fake_run_ros2(args, timeout=10.0):
        del timeout
        calls.append(args)
        return True, "ok"

    monkeypatch.setattr(turtle_tools, "_run_ros2", fake_run_ros2)

    result = turtle_tools.turtle_teleport_absolute.invoke(
        {"name": "/turtle1", "x": 3.0, "y": 4.0, "theta": 1.57, "hide_pen": True}
    )

    assert result["success"] is True
    assert calls[0][3] == "/turtle1/set_pen"
    assert calls[1][3] == "/turtle1/teleport_absolute"
    assert calls[2][3] == "/turtle1/set_pen"
    assert '"x": 3.0' in calls[1][-1]
    assert '"theta": 1.57' in calls[1][-1]


def test_draw_line_segment_rejects_out_of_bounds_without_ros_call(monkeypatch):
    calls = []

    def fake_run_ros2(args, timeout=10.0):
        del timeout
        calls.append(args)
        return True, "ok"

    monkeypatch.setattr(turtle_tools, "_run_ros2", fake_run_ros2)

    result = turtle_tools.draw_line_segment.invoke(
        {"name": "turtle1", "x1": 1.0, "y1": 1.0, "x2": 12.0, "y2": 1.0}
    )

    assert result["success"] is False
    assert "超出 turtlesim 范围" in result["error"]
    assert calls == []


def test_turtlesim_set_background_sets_params_then_clears(monkeypatch):
    calls = []

    def fake_run_ros2(args, timeout=10.0):
        del timeout
        calls.append(args)
        return True, "ok"

    monkeypatch.setattr(turtle_tools, "_run_ros2", fake_run_ros2)

    result = turtle_tools.turtlesim_set_background.invoke(
        {"r": 173, "g": 216, "b": 230, "clear_after": True}
    )

    assert result["success"] is True
    assert calls[0] == ["ros2", "param", "set", "/turtlesim", "background_r", "173"]
    assert calls[1] == ["ros2", "param", "set", "/turtlesim", "background_g", "216"]
    assert calls[2] == ["ros2", "param", "set", "/turtlesim", "background_b", "230"]
    assert calls[3][3] == "/clear"
