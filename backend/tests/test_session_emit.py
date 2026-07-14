"""emit() must persist to the recorder BEFORE the WS send, so a failed send
never drops the event from the durable event log (and seq is assigned
synchronously, before any await)."""

import os

# Session() constructs a real AsyncOpenAI client (never called here); the SDK
# requires a credential at construction time. Set a harmless placeholder before
# importing anything under voice_assistant.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest
from conftest import FakeTTSProvider, FakeWebSocket

from voice_assistant.protocol import StateEvent
from voice_assistant.session import Session


class _CapturingRecorder:
    """Records every event handed to it, so the test can assert the event was
    persisted even when the WS send raises."""

    def __init__(self) -> None:
        self.recorded: list = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def record(self, event, *, turn_id, trace_id, span_id) -> None:
        self.recorded.append(event)

    def note_title(self, text: str) -> None:
        return None


async def test_emit_records_even_when_ws_send_fails():
    ws = FakeWebSocket()

    async def boom(_data):
        raise RuntimeError("socket gone")

    ws.send_json = boom  # simulate a disconnect mid-emit
    session = Session(ws)
    session.tts = FakeTTSProvider()
    rec = _CapturingRecorder()
    session._recorder = rec

    # The send still fails and propagates (run() catches this upstream)...
    with pytest.raises(RuntimeError):
        await session.emit(StateEvent(state="thinking"))

    # ...but the event was already recorded before the send was attempted.
    assert len(rec.recorded) == 1
    assert rec.recorded[0].type == "state"
