"""서버 측 Tool 화이트리스트.

Executor는 tool 이름만 넘기고, 여기 등록된 함수만 실행한다.
미등록 이름은 실행하지 않는다(importlib/getattr/eval 없음).

향후 후보(아직 등록하지 않음):
search_documents, compare_documents, extract_data,
convert_document
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_REGISTRY: dict[str, "ToolHandler"] = {}
_builtins_loaded = False


class UnknownToolError(ValueError):
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        label = tool_name or "(empty)"
        super().__init__(f"Tool is not registered: {label}")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    detail: str
    data: Any = None
    state_updates: Mapping[str, Any] | None = None


class ToolHandler(Protocol):
    def __call__(self, args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult: ...


def register_tool(name: str) -> Callable[[ToolHandler], ToolHandler]:
    if not name or not _TOOL_NAME.match(name):
        raise ValueError(f"Invalid tool name: {name!r}")

    def decorator(fn: ToolHandler) -> ToolHandler:
        if name in _REGISTRY and _REGISTRY[name] is not fn:
            raise ValueError(f"Tool already registered: {name}")
        _REGISTRY[name] = fn
        return fn

    return decorator


def _load_builtin_tools() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    from app.tools import handlers as _handlers  # noqa: F401

    _builtins_loaded = True


def is_registered(name: str) -> bool:
    _load_builtin_tools()
    return name in _REGISTRY


def registered_tool_names() -> tuple[str, ...]:
    _load_builtin_tools()
    return tuple(_REGISTRY.keys())


def invoke_tool(name: str | None, args: dict[str, Any] | None, state: Mapping[str, Any]) -> ToolResult:
    """등록된 Tool만 실행한다. 미등록 이름은 UnknownToolError."""
    _load_builtin_tools()
    tool_name = name if isinstance(name, str) else ""
    handler = _REGISTRY.get(tool_name)
    if handler is None:
        raise UnknownToolError(tool_name)
    payload = args if isinstance(args, dict) else {}
    return handler(payload, state)
