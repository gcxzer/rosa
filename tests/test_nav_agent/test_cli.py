from __future__ import annotations

import asyncio
import builtins
import json
import sys

import main as main_module


class FakeAgent:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = None
        self.history = None
        self.__class__.instances.append(self)

    def use_session(self, session_id, history):
        self.session_id = session_id
        self.history = history


def test_cli_selects_nav_and_records_session_metadata(tmp_path, monkeypatch) -> None:
    FakeAgent.instances.clear()
    prompts = []

    async def fake_run_session_prompt(**kwargs):
        prompts.append(kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "NavAgent", FakeAgent)
    monkeypatch.setattr(main_module, "run_session_prompt", fake_run_session_prompt)
    monkeypatch.setattr(sys, "argv", ["main.py", "--agent", "nav", "检查导航"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    asyncio.run(main_module.main())

    assert len(FakeAgent.instances) == 1
    assert FakeAgent.instances[0].kwargs["streaming"] is True
    assert prompts[0]["prompt"] == "检查导航"
    index = json.loads((tmp_path / ".rosa/sessions/sessions.json").read_text(encoding="utf-8"))
    metadata = next(iter(index["sessions"].values()))
    assert metadata["agent"] == "nav"
    assert metadata["model"] == "gpt-5.5"


def test_importing_cli_for_existing_agents_does_not_import_ros() -> None:
    assert "rclpy" not in sys.modules
    assert "nav2_simple_commander" not in sys.modules
