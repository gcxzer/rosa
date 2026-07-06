"""ROSA 本地 session 存储。

这里参考 Paper_Notes 的 session 管理方式，但保留 ROSA 当前需要的最小能力：

- `sessions.json` 保存 session 索引和元数据。
- 每个 session 用一个 JSONL 文件保存 transcript。
- transcript 只保存可恢复对话所需的 user/assistant 文本消息。

下一次启动 CLI 时，可以通过 `--session-id` 读取 transcript，再把历史消息交给 ROSA。
ROSA 会在每一轮调用时显式带上这些 user/assistant 历史消息，不再依赖 LangGraph
checkpointer 维护对话上下文。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _now_iso(now: Optional[datetime] = None) -> str:
    """生成稳定的本地时间字符串，供 session metadata 和 transcript 共用。"""
    return (now or datetime.now().astimezone()).isoformat(timespec="seconds")


def _date_bucket(now: Optional[datetime] = None) -> str:
    """按日期分桶 transcript，避免所有 JSONL 都堆在一个目录里。"""
    return (now or datetime.now().astimezone()).strftime("%d_%m_%Y")


def _new_session_id(now: Optional[datetime] = None) -> str:
    """生成可读且低碰撞的 session id，格式和 Paper_Notes 类似。"""
    value = now or datetime.now().astimezone()
    return f"{value.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """原子写 JSON，避免进程中断时留下半截索引文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _dump_jsonl_message(message: dict[str, Any]) -> str:
    """把一条 transcript message 序列化成 JSONL 行。"""
    return json.dumps(message, ensure_ascii=False)


@dataclass(slots=True)
class ROSASessionMetadata:
    """一个 ROSA session 的索引信息。"""

    session_id: str
    title: str
    agent: str
    created_at: str
    updated_at: str
    date_bucket: str
    provider: str = "codex"
    model: str = ""
    message_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "agent": self.agent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "date_bucket": self.date_bucket,
            "provider": self.provider,
            "model": self.model,
            "message_count": self.message_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ROSASessionMetadata":
        created_at = str(data.get("created_at") or _now_iso())
        return cls(
            session_id=str(data["session_id"]),
            title=str(data.get("title") or "New chat"),
            agent=str(data.get("agent") or "turtle"),
            created_at=created_at,
            updated_at=str(data.get("updated_at") or created_at),
            date_bucket=str(data.get("date_bucket") or _date_bucket()),
            provider=str(data.get("provider") or "codex"),
            model=str(data.get("model") or ""),
            message_count=int(data.get("message_count") or 0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class ROSASession:
    """完整 session：metadata + transcript messages。"""

    metadata: ROSASessionMetadata
    messages: list[dict[str, Any]]


class SessionNotFoundError(KeyError):
    """请求的 session id 不存在。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"ROSA session not found: {session_id}")
        self.session_id = session_id


class ROSASessionStore:
    """基于文件系统的 ROSA session store。"""

    def __init__(self, sessions_root: str | Path | None = None):
        self.sessions_root = Path(sessions_root or ".rosa/sessions")
        self.index_path = self.sessions_root / "sessions.json"
        self._lock = threading.Lock()
        self._loaded = False
        self._sessions: dict[str, ROSASessionMetadata] = {}

    def create_session(
        self,
        *,
        title: str = "New chat",
        agent: str = "turtle",
        provider: str = "codex",
        model: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> ROSASession:
        """创建一个空 session，并写入索引和空 transcript。"""
        now = datetime.now().astimezone()
        session_id = _new_session_id(now)
        created_at = _now_iso(now)
        session_metadata = ROSASessionMetadata(
            session_id=session_id,
            title=title or "New chat",
            agent=agent,
            created_at=created_at,
            updated_at=created_at,
            date_bucket=_date_bucket(now),
            provider=provider,
            model=model,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            self._ensure_loaded_locked()
            self._sessions[session_id] = session_metadata
            self._write_transcript_locked(session_metadata, [])
            self._save_index_locked()

        return ROSASession(metadata=session_metadata, messages=[])

    def get_session(self, session_id: str) -> Optional[ROSASession]:
        """读取 session；不存在时返回 None。"""
        with self._lock:
            self._ensure_loaded_locked()
            metadata = self._sessions.get(session_id)
            if metadata is None:
                return None
            return ROSASession(
                metadata=metadata,
                messages=self._read_transcript_locked(metadata),
            )

    def require_session(self, session_id: str) -> ROSASession:
        """读取 session；不存在时抛出明确错误。"""
        session = self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def list_sessions(self, *, agent: Optional[str] = None) -> list[ROSASessionMetadata]:
        """按更新时间倒序列出 session。"""
        with self._lock:
            self._ensure_loaded_locked()
            sessions = list(self._sessions.values())
            if agent:
                sessions = [session for session in sessions if session.agent == agent]
            return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: Any,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ROSASession:
        """向 transcript 追加一条消息，并更新 session 索引。"""
        role = str(role or "").strip()
        if role not in {"user", "assistant"}:
            raise ValueError("ROSA transcript role 只能是 user 或 assistant。")

        with self._lock:
            self._ensure_loaded_locked()
            session_metadata = self._sessions.get(session_id)
            if session_metadata is None:
                raise SessionNotFoundError(session_id)

            messages = self._read_transcript_locked(session_metadata)
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": _now_iso(),
                    "metadata": dict(metadata or {}),
                }
            )
            self._write_transcript_locked(session_metadata, messages)
            session_metadata.message_count = len(messages)
            session_metadata.updated_at = _now_iso()
            self._save_index_locked()
            return ROSASession(metadata=session_metadata, messages=messages)

    def transcript_messages_for_langchain(self, session_id: str) -> list[dict[str, str]]:
        """把本地 transcript 转成 LangChain `messages` state 能接收的最小形状。"""
        session = self.require_session(session_id)
        messages: list[dict[str, str]] = []
        for message in session.messages:
            role = str(message.get("role") or "")
            content = message.get("content", "")
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                messages.append({"role": role, "content": content})
        return messages

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._sessions = self._load_index()
        self._loaded = True

    def _load_index(self) -> dict[str, ROSASessionMetadata]:
        if not self.index_path.exists():
            return {}
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        raw_sessions = data.get("sessions", {})
        if not isinstance(raw_sessions, dict):
            return {}
        return {
            str(session_id): ROSASessionMetadata.from_dict(
                {**dict(payload), "session_id": session_id}
            )
            for session_id, payload in raw_sessions.items()
            if isinstance(payload, dict)
        }

    def _save_index_locked(self) -> None:
        _atomic_write_json(
            self.index_path,
            {
                "version": 1,
                "sessions": {
                    session_id: metadata.to_dict()
                    for session_id, metadata in sorted(self._sessions.items())
                },
            },
        )

    def _transcript_path(self, metadata: ROSASessionMetadata) -> Path:
        return self.sessions_root / metadata.date_bucket / f"{metadata.session_id}.jsonl"

    def _read_transcript_locked(self, metadata: ROSASessionMetadata) -> list[dict[str, Any]]:
        path = self._transcript_path(metadata)
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid ROSA transcript at {path}:{line_number}") from error
            if isinstance(loaded, dict):
                messages.append(loaded)
        return messages

    def _write_transcript_locked(
        self,
        metadata: ROSASessionMetadata,
        messages: list[dict[str, Any]],
    ) -> None:
        path = self._transcript_path(metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(_dump_jsonl_message(message) + "\n" for message in messages)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
