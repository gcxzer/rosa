"""NavAgent public API with lazy agent/dependency loading."""

from typing import Any

__all__ = ["NavAgent"]


def __getattr__(name: str) -> Any:
    if name == "NavAgent":
        from .agent import NavAgent

        return NavAgent
    raise AttributeError(name)
