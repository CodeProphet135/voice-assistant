"""The manual streaming OpenAI Responses API tool-use loop — the centerpiece.

Deliberately NOT using the SDK's built-in tool runner: we stream per-token
text to the UI/TTS chunker while tools execute mid-stream, and that doesn't
compose with the runner's synchronous request/response abstraction (see
CLAUDE.md). Event dispatch is duck-typed on ``event.type`` strings rather than
importing concrete SDK event classes, so a plain ``types.SimpleNamespace`` (or
any object with the right attributes) can stand in for real SDK events in
tests.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from voice_assistant.agent.chunker import Chunker
from voice_assistant.agent.prompts import SYSTEM_PROMPT
from voice_assistant.config import settings
from voice_assistant.protocol import (
    AssistantDeltaEvent,
    AssistantDoneEvent,
    ErrorEvent,
    LlmRequestEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from voice_assistant.telemetry import get_tracer

MAX_TOOL_LOOP_ITERATIONS = 6

EmitFn = Callable[[Any], Awaitable[None]]
OnSentenceFn = Callable[[str], Awaitable[None]]
ToolExecutorFn = Callable[[str, str, str], Awaitable[str]]

_tracer = get_tracer(__name__)


async def _execute_tool_call(
    tool_executor: ToolExecutorFn, name: str, arguments: str, call_id: str
) -> str:
    """Run a single tool call, mapping any exception to an error-text output.

    Tool exceptions must never propagate out of the agent loop — a broken
    tool should degrade to an apologetic string the model can read, not kill
    the WebSocket session.
    """
    try:
        return await tool_executor(name, arguments, call_id)
    except Exception as exc:  # noqa: BLE001 - intentionally broad, see docstring
        return f"Error: {exc}"


async def run_agent(
    *,
    client: AsyncOpenAI,
    input_items: list[dict],
    tools: list[dict],
    emit: EmitFn,
    on_sentence: OnSentenceFn,
    tool_executor: ToolExecutorFn,
) -> str:
    """Run one user turn to completion, including any tool-call round-trips.

    ``input_items`` is mutated in place — assistant function calls and their
    outputs are appended as the loop progresses — so the caller (``Session``)
    keeps the full running conversation state across turns.

    Returns the full concatenated assistant text produced across every
    iteration of the loop (i.e. the final textual reply after all tool calls
    resolve).
    """
    chunker = Chunker()
    full_text_parts: list[str] = []

    for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
        function_calls: list[Any] = []

        try:
            with _tracer.start_as_current_span("llm.request") as span:
                span.set_attribute("model", settings.openai_model)
                await emit(
                    LlmRequestEvent(
                        input=list(input_items),
                        model=settings.openai_model,
                        iteration=iteration,
                    )
                )
                stream = await client.responses.create(
                    model=settings.openai_model,
                    instructions=SYSTEM_PROMPT,
                    input=input_items,
                    tools=tools,
                    stream=True,
                    store=False,
                    max_output_tokens=settings.openai_max_output_tokens,
                    reasoning={"effort": settings.openai_reasoning_effort},
                )

                final_response = None
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        delta = event.delta
                        full_text_parts.append(delta)
                        await emit(AssistantDeltaEvent(text=delta))
                        for sentence in chunker.feed(delta):
                            await on_sentence(sentence)
                    elif event.type == "response.output_item.added":
                        item = event.item
                        if getattr(item, "type", None) == "function_call":
                            await emit(
                                ToolCallEvent(
                                    call_id=item.call_id,
                                    name=item.name,
                                    arguments=getattr(item, "arguments", "") or "",
                                )
                            )
                    elif event.type == "response.completed":
                        final_response = event.response
        except Exception as exc:  # noqa: BLE001 - never let this kill the WS
            await emit(ErrorEvent(message=f"LLM request failed: {exc}"))
            return "".join(full_text_parts)

        if final_response is not None:
            function_calls = [
                item
                for item in final_response.output
                if getattr(item, "type", None) == "function_call"
            ]

        if not function_calls:
            break

        for item in function_calls:
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                }
            )

        results = await asyncio.gather(
            *(
                _execute_tool_call(tool_executor, item.name, item.arguments, item.call_id)
                for item in function_calls
            )
        )

        for item, output in zip(function_calls, results, strict=True):
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": output,
                }
            )
            await emit(
                ToolResultEvent(
                    call_id=item.call_id,
                    name=item.name,
                    arguments=item.arguments,
                    output=output,
                )
            )

    trailing = chunker.flush()
    if trailing:
        await on_sentence(trailing)

    full_text = "".join(full_text_parts)
    await emit(AssistantDoneEvent(text=full_text))
    return full_text
