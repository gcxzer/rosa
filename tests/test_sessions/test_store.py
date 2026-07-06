from sessions import ROSASessionStore


def test_session_store_persists_index_and_transcript(tmp_path):
    store = ROSASessionStore(tmp_path / "sessions")

    session = store.create_session(title="画图测试", agent="turtle", model="gpt-5.5")
    store.append_message(session.metadata.session_id, role="user", content="画一个圆")
    store.append_message(session.metadata.session_id, role="assistant", content="已经画好了")

    loaded = ROSASessionStore(tmp_path / "sessions").require_session(session.metadata.session_id)

    assert loaded.metadata.title == "画图测试"
    assert loaded.metadata.agent == "turtle"
    assert loaded.metadata.model == "gpt-5.5"
    assert loaded.metadata.message_count == 2
    assert [message["role"] for message in loaded.messages] == ["user", "assistant"]
    assert [message["content"] for message in loaded.messages] == ["画一个圆", "已经画好了"]


def test_session_store_returns_langchain_message_history(tmp_path):
    store = ROSASessionStore(tmp_path / "sessions")
    session = store.create_session(title="恢复测试")
    store.append_message(session.metadata.session_id, role="user", content="第一轮问题")
    store.append_message(session.metadata.session_id, role="assistant", content="第一轮回答")

    history = store.transcript_messages_for_langchain(session.metadata.session_id)

    assert history == [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
    ]
