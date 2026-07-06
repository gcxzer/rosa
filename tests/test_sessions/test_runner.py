import asyncio

from sessions import ROSASessionStore, run_session_prompt


class FakeStreamingAgent:
    """测试用 agent：只模拟 ROSA.astream() 的事件格式，不触发真实模型请求。"""

    async def astream(self, prompt):
        # 先确认调用方把用户输入原样传给 agent，再模拟一段流式 token 和最终结果。
        assert prompt == "你好"
        yield {"type": "token", "content": "我是"}
        yield {"type": "token", "content": "ROSA"}
        yield {"type": "final", "content": "我是ROSA"}


def test_run_session_prompt_prints_stream_and_persists_transcript(tmp_path, capsys):
    store = ROSASessionStore(tmp_path / "sessions")
    session = store.create_session(title="runner 测试")

    asyncio.run(
        run_session_prompt(
            agent=FakeStreamingAgent(),
            session_store=store,
            session_id=session.metadata.session_id,
            prompt="你好",
        )
    )

    loaded = store.require_session(session.metadata.session_id)

    assert [message["role"] for message in loaded.messages] == ["user", "assistant"]
    assert [message["content"] for message in loaded.messages] == ["你好", "我是ROSA"]
    assert capsys.readouterr().out == "我是ROSA\n"
