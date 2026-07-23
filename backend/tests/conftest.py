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
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
        # Snapshot ``input`` at call time: the real AsyncOpenAI client
        # serializes the request body immediately, but ``input_items`` is a
        # list the caller (agent.py) mutates in place afterward (appending
        # the assistant's reply). Without a copy here, that later mutation
        # would leak into this recorded call retroactively.
        self.calls.append({**kwargs, "input": list(kwargs["input"])})
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
        self.sent_bytes: list[bytes] = []
        self._incoming: asyncio.Queue[dict] = asyncio.Queue()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

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


class FakeTTSProvider:
    """Scriptable stand-in for ``providers.base.TTSProvider``. ``synthesize``
    yields a deterministic, single PCM-shaped chunk per call so tests can
    assert on exactly what was "spoken" without any network I/O."""

    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self.closed = False

    async def synthesize(self, text: str):
        self.synthesized.append(text)
        yield b"PCM:" + text.encode()

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_tts() -> FakeTTSProvider:
    return FakeTTSProvider()


class FakeEventRecorder:
    """No-op stand-in for ``events.EventRecorder`` (same public surface:
    ``start``/``stop``/``record``/``note_title``), injected via
    ``session._recorder`` in fake-WebSocket-driven Session tests.

    ``Session.run()`` awaits ``self._recorder.start()`` as its first line.
    The real ``EventRecorder`` does genuine Postgres I/O there (tens of ms
    even on localhost), which -- unlike CPU-bound work -- doesn't complete
    within a test's ``await asyncio.sleep(0)`` budget. That breaks the
    fake-websocket tests' timing: ``asyncio.Queue.get()`` resolves without
    yielding when the queue is already non-empty, so a ``disconnect``
    queued (synchronously, no real delay) while ``run()`` is still stuck
    awaiting the recorder's DB round-trip gets consumed immediately once
    ``run()`` resumes -- before the freshly spawned ``_consume_stt`` task
    ever gets scheduled to process already-pushed STT events. Swapping in
    this no-op keeps those tests hermetic and their timing deterministic."""

    def __init__(self) -> None:
        self._seq = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def record(self, event, *, turn_id, trace_id, span_id) -> None:
        return None

    def note_title(self, text: str) -> None:
        return None


@pytest_asyncio.fixture
async def pg_db(monkeypatch):
    """Bind db.async_session_factory to a rolled-back Postgres transaction with
    all tables created. Skips when Postgres is unreachable."""
    from voice_assistant import db as db_module
    from voice_assistant.config import settings
    from voice_assistant.models import Base

    engine = create_async_engine(settings.database_url)
    try:
        conn = await engine.connect()
    except Exception:
        await engine.dispose()
        pytest.skip("Postgres not reachable")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    try:
        yield
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()
