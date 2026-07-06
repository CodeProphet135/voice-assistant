"""Shared test fixtures: a FakeOpenAI client scripting Responses API streams,
plus a FakeWebSocket harness for driving ``Session`` end-to-end.

``run_agent`` (agent/agent.py) duck-types on ``event.type`` and reads a small
set of attributes (``.delta``, ``.item``, ``.response``) rather than importing
concrete SDK event classes, so simple ``types.SimpleNamespace`` objects stand
in perfectly as scripted stream events here.
"""

import asyncio
from types import SimpleNamespace

import pytest


def make_text_delta_event(delta: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=delta)


def make_function_call_item(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def make_output_item_added_event(item: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.added", item=item)


def make_completed_event(output: list) -> SimpleNamespace:
    response = SimpleNamespace(output=output)
    return SimpleNamespace(type="response.completed", response=response)


def make_text_turn(text: str) -> list[SimpleNamespace]:
    """Script a plain text turn: streamed deltas (split on spaces, kept
    joinable) then a response.completed with no function calls."""
    words = text.split(" ")
    deltas = [w + " " if i < len(words) - 1 else w for i, w in enumerate(words)]
    events = [make_text_delta_event(d) for d in deltas]
    events.append(make_completed_event(output=[]))
    return events


def make_function_call_turn(call_id: str, name: str, arguments: str) -> list[SimpleNamespace]:
    """Script a function-call turn: an output_item.added carrying the
    function_call, then response.completed whose output contains it."""
    item = make_function_call_item(call_id, name, arguments)
    return [
        make_output_item_added_event(item),
        make_completed_event(output=[item]),
    ]


class _FakeStream:
    """Async-iterable wrapper around a pre-scripted list of events."""

    def __init__(self, events: list) -> None:
        self._events = events

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for event in self._events:
            yield event


class FakeResponses:
    """Stand-in for ``client.responses`` — ``create()`` pops the next
    scripted turn off a queue each time it's called."""

    def __init__(self) -> None:
        self.scripted_turns: list[list] = []
        self.calls: list[dict] = []

    def script(self, events: list) -> None:
        """Queue up the events for the next ``create()`` call."""
        self.scripted_turns.append(events)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted_turns:
            raise AssertionError("FakeResponses.create() called with no scripted turn queued")
        events = self.scripted_turns.pop(0)
        return _FakeStream(events)


class FakeOpenAI:
    """Minimal stand-in for ``AsyncOpenAI`` exposing just ``.responses``."""

    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


class FakeWebSocket:
    """Minimal stand-in for a FastAPI ``WebSocket`` — captures every
    ``send_json`` call and lets tests feed scripted ``receive()`` results
    (JSON text frames or binary frames) so ``Session.run()`` can be driven
    end-to-end without a real ASGI server."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._incoming: asyncio.Queue[dict] = asyncio.Queue()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def queue_text(self, raw: str) -> None:
        self._incoming.put_nowait({"type": "websocket.receive", "text": raw})

    def queue_bytes(self, data: bytes) -> None:
        self._incoming.put_nowait({"type": "websocket.receive", "bytes": data})

    def queue_disconnect(self) -> None:
        self._incoming.put_nowait({"type": "websocket.disconnect"})

    async def receive(self) -> dict:
        return await self._incoming.get()


@pytest.fixture
def fake_websocket() -> FakeWebSocket:
    return FakeWebSocket()
