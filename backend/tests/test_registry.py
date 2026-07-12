"""Tests for the @tool registry mechanism (Phase 4).

Registration mutates a module-global dict, so an autouse fixture snapshots and
restores it around every test to keep this file hermetic even after the real
tool modules register themselves at import time.
"""

import json

import pytest

from voice_assistant.agent.tools import registry


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def _register_sample_tools():
    @registry.tool(
        name="zzz_sample",
        description="Z sample.",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        },
    )
    async def zzz_sample(ctx, x: str) -> str:
        return f"z:{x}"

    @registry.tool(
        name="aaa_sample",
        description="A sample.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    async def aaa_sample(ctx) -> str:
        return "a"


def test_definitions_are_name_sorted_and_strict_shaped():
    _register_sample_tools()
    defs = registry.definitions()
    sample = [d for d in defs if d["name"] in {"aaa_sample", "zzz_sample"}]
    assert [d["name"] for d in sample] == ["aaa_sample", "zzz_sample"]
    z = next(d for d in defs if d["name"] == "zzz_sample")
    assert z["type"] == "function"
    assert z["strict"] is True
    assert z["parameters"]["additionalProperties"] is False
    assert z["description"] == "Z sample."


async def test_execute_dispatches_to_handler():
    _register_sample_tools()
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("zzz_sample", json.dumps({"x": "hi"}), ctx)
    assert out == "z:hi"


async def test_execute_unknown_tool_returns_error_string():
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("nope", "{}", ctx)
    assert out.startswith("Error:")
    assert "nope" in out


async def test_execute_bad_json_returns_error_string():
    _register_sample_tools()
    ctx = registry.ToolContext(session=None)
    out = await registry.execute("zzz_sample", "{not json", ctx)
    assert out.startswith("Error:")


async def test_execute_propagates_handler_exception():
    @registry.tool(
        name="boom",
        description="Always fails.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    async def boom(ctx) -> str:
        raise RuntimeError("kaboom")

    ctx = registry.ToolContext(session=None)
    with pytest.raises(RuntimeError, match="kaboom"):
        await registry.execute("boom", "{}", ctx)
