"""The tool registry — a ``@tool`` decorator collecting async handlers plus
the two functions ``session.py`` needs: ``definitions()`` (OpenAI tool schemas)
and ``execute()`` (dispatch).

Handlers are ``async def handler(ctx: ToolContext, **kwargs) -> str``. Handler
exceptions are intentionally NOT caught here — ``agent/agent.py``'s
``_execute_tool_call`` is the single place that maps a tool failure to an
``"Error: ..."`` string the model can read, so the two never double-wrap. The
registry only produces friendly strings for its own dispatch failures (unknown
tool, malformed argument JSON).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_assistant.session import Session


@dataclass
class ToolContext:
    """What a tool handler is handed at call time. ``session`` is the live
    ``Session`` (used by ``set_timer`` to schedule/emit); tools that don't need
    it simply ignore it."""

    session: Session | None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[str]]


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *, name: str, description: str, parameters: dict
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Register an async tool handler and return it unchanged."""

    def decorator(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        _REGISTRY[name] = ToolSpec(
            name=name, description=description, parameters=parameters, handler=fn
        )
        return fn

    return decorator


def definitions() -> list[dict]:
    """Return name-sorted OpenAI Responses tool definitions (strict mode)."""
    return [
        {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "strict": True,
        }
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.name)
    ]


async def execute(name: str, arguments_json: str, ctx: ToolContext) -> str:
    """Dispatch a tool call. Dispatch-level failures (unknown tool, bad JSON)
    return an ``"Error: ..."`` string; the handler's own exceptions propagate."""
    spec = _REGISTRY.get(name)
    if spec is None:
        return f"Error: no such tool {name!r}"
    try:
        kwargs: dict[str, Any] = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError:
        return "Error: invalid tool arguments"
    return await spec.handler(ctx, **kwargs)
