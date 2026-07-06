"""Codex OAuth 认证信息读取。

这个文件参考 Paper Notes 的做法：不要求用户提供 `OPENAI_API_KEY`，而是复用
Codex CLI / Codex 桌面环境已经写入本机的 `~/.codex/auth.json`。这里读取到的是
ChatGPT Codex 后端可用的 OAuth token，然后交给 OpenAI Python SDK 的 Responses
client 使用。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_AUTH_PATH_ENV = "ROSA_CODEX_AUTH_PATH"
CODEX_CLIENT_HEADERS = {
    "User-Agent": "codex_cli_rs/0.0.0 (ROSA)",
    "originator": "codex_cli_rs",
}


@dataclass(frozen=True, slots=True)
class CodexCredentials:
    """本地 Codex OAuth 文件中和请求相关的最小凭据集合。"""

    access_token: str = ""
    account_id: str = ""
    base_url: str = DEFAULT_CODEX_BASE_URL


def runtime_codex_credentials(*, auth_path: Any = None) -> CodexCredentials:
    """读取本机 Codex OAuth 凭据。

    这里刻意只读取已有登录状态，不在模型生成时启动浏览器登录流程。这样 ROSA 的
    行为更像普通 LangChain provider：缺凭据就明确报错，让用户先在 Codex 环境登录。
    """
    if auth_path:
        path = Path(str(auth_path)).expanduser()
    else:
        override = os.environ.get(CODEX_AUTH_PATH_ENV, "").strip()
        path = Path(override).expanduser() if override else Path.home() / ".codex" / "auth.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    access_token = str(
        tokens.get("access_token")
        or payload.get("accessToken")
        or payload.get("access_token")
        or ""
    )
    account_id = str(
        tokens.get("account_id")
        or payload.get("accountId")
        or payload.get("account_id")
        or chatgpt_account_id(access_token)
        or ""
    )
    base_url = str(
        tokens.get("base_url")
        or payload.get("baseUrl")
        or payload.get("base_url")
        or DEFAULT_CODEX_BASE_URL
    )
    return CodexCredentials(
        access_token=access_token,
        account_id=account_id,
        base_url=base_url.rstrip("/") or DEFAULT_CODEX_BASE_URL,
    )


def codex_default_headers(credentials: CodexCredentials) -> dict[str, str]:
    """生成 ChatGPT Codex 后端需要的默认请求头。"""
    headers = dict(CODEX_CLIENT_HEADERS)
    account_id = credentials.account_id or chatgpt_account_id(credentials.access_token)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def chatgpt_account_id(token: str) -> str:
    """从 OAuth access token 的 JWT claims 中尽量提取 ChatGPT account id。"""
    parts = token.split(".")
    if len(parts) < 2:
        claims: dict[str, Any] = {}
    else:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        try:
            import base64

            decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
            loaded_claims = json.loads(decoded.decode("utf-8"))
        except Exception:
            loaded_claims = {}
        claims = loaded_claims if isinstance(loaded_claims, dict) else {}

    for key in ("https://api.openai.com/auth", "chatgpt_account_id", "account_id"):
        value = claims.get(key)
        if isinstance(value, dict):
            account_id = value.get("chatgpt_account_id") or value.get("account_id")
            if account_id:
                return str(account_id)
        if isinstance(value, str) and value:
            return value
    return ""
