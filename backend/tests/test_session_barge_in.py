"""Tests for barge-in (Task 2b): the user starts speaking again while the
assistant is still mid-turn, and the in-flight turn must be cancelled.

Reuses the fake harness from test_session_stt.py/test_session_tts.py
(FakeSTTProvider, FakeTTSProvider, FakeWebSocket) plus the _GatedStream
pattern (test_session_stt.py) to freeze a turn's agent stream mid-flight so
``_turn_task`` stays "active" while barge-in events are pushed.
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from conftest import (
    FakeTTSProvider,
    FakeWebSocket,
    _FakeStream,
    make_completed_event,
    make_text_delta_event,
)
from test_session_stt import FakeSTTProvider, _GatedStream

from voice_assistant.providers.base import SpeechStarted, Transcript
from voice_assistant.session import Session


class BargeInResponses:
    """Scriptable stand-in for ``client.responses`` supporting both a
    permanently-gated (frozen mid-stream) turn -- to simulate an active
    turn for barge-in to interrupt -- and plain scripted turns for
    subsequent, uninterrupted turns."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._turns: list[tuple] = []

    def script_gated(self, pre: list, release: asyncio.Event, post: list) -> None:
        self._turns.append(("gated", pre, release, post))

    def script(self, events: list) -> None:
        self._turns.append(("plain", events))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._turns:
            raise AssertionError("BargeInResponses.create() called with no scripted turn queued")
        turn = self._turns.pop(0)
        if turn[0] == "gated":
            _, pre, release, post = turn
            return _GatedStream(pre, release, post)
        _, events = turn
        return _FakeStream(events)


class BargeInClient:
    def __init__(self) -> None:
        self.responses = BargeInResponses()


def make_session(fake_ws: FakeWebSocket, client: BargeInClient):
    session = Session(fake_ws)
    session.client = client
    fake_stt = FakeSTTProvider()
    session._make_stt_provider = lambda: fake_stt  # noqa: SLF001 - test injection seam
    fake_tts = FakeTTSProvider()
    session.tts = fake_tts
    return session, fake_stt, fake_tts


async def _drive(n: int = 30) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def _start_and_commit_gated_turn(
    fake_ws: FakeWebSocket, session: Session, fake_stt: FakeSTTProvider, release: asyncio.Event
) -> asyncio.Task:
    """Boot the session, commit an utterance whose agent reply is gated
    (frozen after the first delta), and drive until the turn is
    observably active (a delta was emitted, no assistant_done yet)."""
    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    fake_stt.push(Transcript(text="hello world", is_final=True, speech_final=True))
    await _drive(20)

    # Sanity: the turn really is active and frozen mid-stream -- otherwise
    # the barge-in tests below would pass vacuously.
    assert session._turn_active(), "expected the gated turn to still be running"
    assert [e["text"] for e in fake_ws.sent if e["type"] == "assistant_delta"] == ["Hi "]
    assert not any(e["type"] == "assistant_done" for e in fake_ws.sent)

    return run_task


async def test_speech_started_during_active_turn_barges_in() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    release = asyncio.Event()  # never set: turn stays frozen unless cancelled
    client.responses.script_gated(
        [make_text_delta_event("Hi ")],
        release,
        [make_text_delta_event("there"), make_completed_event(output=[])],
    )
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    run_task = await _start_and_commit_gated_turn(fake_ws, session, fake_stt, release)

    fake_stt.push(SpeechStarted())
    await _drive(20)

    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent), "expected a tts_cancel event"
    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert state_events[-1] == "listening"
    assert not any(
        e["type"] == "assistant_done" for e in fake_ws.sent
    ), "the gated reply must never complete once barged in"
    assert session._turn_task is None or session._turn_task.done()

    fake_ws.queue_disconnect()
    await run_task


async def test_nonempty_interim_during_active_turn_barges_in() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    release = asyncio.Event()
    client.responses.script_gated(
        [make_text_delta_event("Hi ")],
        release,
        [make_text_delta_event("there"), make_completed_event(output=[])],
    )
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    run_task = await _start_and_commit_gated_turn(fake_ws, session, fake_stt, release)

    fake_stt.push(Transcript(text="wait", is_final=False, speech_final=False))
    await _drive(20)

    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent), "expected a tts_cancel event"
    partial_events = [e for e in fake_ws.sent if e["type"] == "stt_partial"]
    assert {"type": "stt_partial", "text": "wait"} in partial_events
    assert not any(e["type"] == "assistant_done" for e in fake_ws.sent)
    assert session._turn_task is None or session._turn_task.done()

    fake_ws.queue_disconnect()
    await run_task


async def test_no_active_turn_no_barge_in() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    assert not session._turn_active()

    fake_stt.push(SpeechStarted())
    await _drive(5)
    fake_stt.push(Transcript(text="hey", is_final=False, speech_final=False))
    await _drive(5)

    assert not any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    assert client.responses.calls == []
    partial_events = [e for e in fake_ws.sent if e["type"] == "stt_partial"]
    assert {"type": "stt_partial", "text": "hey"} in partial_events

    fake_ws.queue_disconnect()
    await run_task


async def test_barge_in_then_new_utterance_commits_fresh_turn() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    release = asyncio.Event()
    client.responses.script_gated(
        [make_text_delta_event("Hi ")],
        release,
        [make_text_delta_event("there"), make_completed_event(output=[])],
    )
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    run_task = await _start_and_commit_gated_turn(fake_ws, session, fake_stt, release)

    fake_stt.push(SpeechStarted())
    await _drive(20)
    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    assert session._turn_task is None or session._turn_task.done()
    assert client.responses.calls and len(client.responses.calls) == 1

    # Queue a second, plain (non-gated) scripted turn and drive a fresh
    # utterance through to completion -- this proves the lock/queue are
    # clean after barge-in and a subsequent turn works normally.
    client.responses.script(
        [
            make_text_delta_event("Sure "),
            make_text_delta_event("thing"),
            make_completed_event(output=[]),
        ]
    )
    fake_stt.push(Transcript(text="new question", is_final=True, speech_final=True))
    await _drive(40)

    assert len(client.responses.calls) == 2
    done_events = [e for e in fake_ws.sent if e["type"] == "assistant_done"]
    assert len(done_events) == 1
    assert done_events[0]["text"] == "Sure thing"

    fake_ws.queue_disconnect()
    await run_task
