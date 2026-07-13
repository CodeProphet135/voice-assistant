"""Tests for the STT turn path wired into Session (Phase 2).

Uses a FakeSTTProvider (implements providers.base.STTProvider) injected via
Session._make_stt_provider(), a FakeWebSocket (conftest.py) capturing
send_json calls, and FakeOpenAI (conftest.py) scripting the agent's Responses
stream. No Deepgram key or real network I/O is involved.
"""

import asyncio
import os

# Session() constructs a real AsyncOpenAI client (its .responses is swapped
# for FakeOpenAI right after); the installed SDK version requires *some*
# credential to be present at construction time even though we never make a
# real call. Set a harmless placeholder so these tests need no real key.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from conftest import (
    FakeEventRecorder,
    FakeOpenAI,
    FakeTTSProvider,
    FakeWebSocket,
    make_completed_event,
    make_text_delta_event,
    make_text_turn,
)

from voice_assistant.providers.base import SpeechStarted, SttEvent, Transcript, UtteranceEnd
from voice_assistant.session import Session


class FakeSTTProvider:
    """Scriptable stand-in for providers.base.STTProvider. Tests push
    normalized events onto an internal queue; `events()` drains it."""

    def __init__(self) -> None:
        self.started = False
        self.finished = False
        self.closed = False
        self.sent_audio: list[bytes] = []
        self._queue: asyncio.Queue[SttEvent] = asyncio.Queue()

    def push(self, event: SttEvent) -> None:
        self._queue.put_nowait(event)

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    async def finish(self) -> None:
        self.finished = True

    async def events(self):
        while True:
            yield await self._queue.get()

    async def aclose(self) -> None:
        self.closed = True


def make_session(
    fake_ws: FakeWebSocket, fake_openai: FakeOpenAI
) -> tuple[Session, FakeSTTProvider]:
    session = Session(fake_ws)
    session.client = fake_openai
    session._recorder = FakeEventRecorder()  # noqa: SLF001 - keep DB out of fake-ws timing
    fake_stt = FakeSTTProvider()
    session._make_stt_provider = lambda: fake_stt  # noqa: SLF001 - test injection seam
    session.tts = FakeTTSProvider()  # keep TTS hermetic/offline for STT-path tests
    return session, fake_stt


async def _run_until_idle_after_start(session: Session, fake_ws: FakeWebSocket) -> None:
    """Drive Session.run() until it observes a disconnect, in a background
    task, so the test can push scripted frames/STT events concurrently."""
    fake_ws.queue_disconnect()
    await session.run()


async def test_interim_transcript_emits_stt_partial() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')

    run_task = asyncio.create_task(session.run())
    # Let the dispatch loop process the start frame and spin up _consume_stt.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_stt.push(Transcript(text="hello wor", is_final=False, speech_final=False))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    partial_events = [e for e in fake_ws.sent if e.get("type") == "stt_partial"]
    assert partial_events == [{"type": "stt_partial", "text": "hello wor"}]
    # No agent call should have been made off an interim transcript.
    assert fake_openai.responses.calls == []


async def test_speech_final_commits_turn_and_runs_agent() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("Hi there"))
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_stt.push(Transcript(text="hello ", is_final=False, speech_final=False))
    fake_stt.push(Transcript(text="hello world", is_final=True, speech_final=True))

    # Give the event loop enough turns to drain the STT queue, commit the
    # turn, and run the (fake, instant) agent loop to completion.
    for _ in range(20):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    types_seen = [e["type"] for e in fake_ws.sent]

    stt_final_events = [e for e in fake_ws.sent if e["type"] == "stt_final"]
    assert stt_final_events == [{"type": "stt_final", "text": "hello world"}]
    # Exactly one stt_final, not one per final transcript fragment.
    assert types_seen.count("stt_final") == 1

    assert "thinking" in [e.get("state") for e in fake_ws.sent if e["type"] == "state"]

    delta_events = [e for e in fake_ws.sent if e["type"] == "assistant_delta"]
    assert [e["text"] for e in delta_events] == ["Hi ", "there"]

    done_events = [e for e in fake_ws.sent if e["type"] == "assistant_done"]
    assert len(done_events) == 1
    assert done_events[0]["text"] == "Hi there"

    # The transcribed user text made it into the Responses `input`.
    assert len(fake_openai.responses.calls) == 1
    call_input = fake_openai.responses.calls[0]["input"]
    assert call_input[-1] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hello world"}],
    }

    # After the agent settles, state returns to "listening" (mic still open).
    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert state_events[-1] == "listening"


async def test_utterance_end_fallback_commits_turn() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("Okay"))
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # is_final but NOT speech_final -- turn should not commit yet.
    fake_stt.push(Transcript(text="testing", is_final=True, speech_final=False))
    for _ in range(5):
        await asyncio.sleep(0)
    assert [e for e in fake_ws.sent if e["type"] == "stt_final"] == []

    # UtteranceEnd arrives as the fallback commit signal.
    fake_stt.push(UtteranceEnd())
    for _ in range(20):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    stt_final_events = [e for e in fake_ws.sent if e["type"] == "stt_final"]
    assert stt_final_events == [{"type": "stt_final", "text": "testing"}]
    assert len(fake_openai.responses.calls) == 1


async def test_empty_utterance_does_not_launch_agent() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Whitespace-only final transcript, then speech_final with nothing
    # meaningful buffered.
    fake_stt.push(Transcript(text="   ", is_final=True, speech_final=True))
    for _ in range(10):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    assert [e for e in fake_ws.sent if e["type"] == "stt_final"] == []
    assert fake_openai.responses.calls == []


async def test_speech_started_event_is_ignored_without_crashing() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_stt.push(SpeechStarted())
    for _ in range(5):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    assert fake_openai.responses.calls == []


async def test_binary_audio_frames_forwarded_to_stt_send_audio() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_ws.queue_bytes(b"\x01\x02\x03\x04")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    assert fake_stt.sent_audio == [b"\x01\x02\x03\x04"]
    assert fake_stt.started is True


async def test_stop_event_tears_down_stt() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    session, fake_stt = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    fake_ws.queue_text('{"type": "stop"}')
    run_task = asyncio.create_task(session.run())

    for _ in range(10):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    assert fake_stt.finished is True
    assert fake_stt.closed is True
    assert session.stt is None

    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert state_events[-1] == "idle"


class _GatedStream:
    """An async Responses stream that yields `pre` events, blocks on
    `release`, then yields `post` events — lets a test freeze the agent
    mid-turn."""

    def __init__(self, pre: list, release: asyncio.Event, post: list) -> None:
        self._pre = pre
        self._release = release
        self._post = post

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for event in self._pre:
            yield event
        await self._release.wait()
        for event in self._post:
            yield event


async def test_stop_does_not_abort_in_flight_turn() -> None:
    """Regression: a `stop` (end-of-audio) arriving while the agent is still
    generating a reply for an already-committed utterance must NOT cancel that
    reply. (Aborting a turn on *new* speech is barge-in — a separate Phase 3
    path.) This reproduces the bug the first live WAV round trip exposed."""
    fake_ws = FakeWebSocket()
    release = asyncio.Event()

    class GatedResponses:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return _GatedStream(
                [make_text_delta_event("Hi ")],
                release,
                [make_text_delta_event("there"), make_completed_event(output=[])],
            )

    class GatedClient:
        def __init__(self) -> None:
            self.responses = GatedResponses()

    session = Session(fake_ws)
    session.client = GatedClient()
    session._recorder = FakeEventRecorder()  # noqa: SLF001 - keep DB out of fake-ws timing
    fake_stt = FakeSTTProvider()
    session._make_stt_provider = lambda: fake_stt  # noqa: SLF001 - test injection seam
    session.tts = FakeTTSProvider()  # keep TTS hermetic/offline

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    for _ in range(3):
        await asyncio.sleep(0)

    # Commit a turn; the agent emits its first delta then blocks on `release`.
    fake_stt.push(Transcript(text="hello world", is_final=True, speech_final=True))
    for _ in range(15):
        await asyncio.sleep(0)

    assert [e["text"] for e in fake_ws.sent if e["type"] == "assistant_delta"] == ["Hi "]
    assert not any(e["type"] == "assistant_done" for e in fake_ws.sent)

    # `stop` arrives mid-turn. _stop_stt should signal finish() but then block
    # on the turn lock rather than cancelling the in-flight reply.
    fake_ws.queue_text('{"type": "stop"}')
    for _ in range(15):
        await asyncio.sleep(0)

    assert fake_stt.finished is True  # end-of-audio was signalled
    assert fake_stt.closed is False  # but teardown is deferred until the turn ends
    assert session.stt is not None
    assert not any(e["type"] == "assistant_done" for e in fake_ws.sent)

    # Let the agent finish; the reply must complete in full.
    release.set()
    for _ in range(30):
        await asyncio.sleep(0)

    fake_ws.queue_disconnect()
    await run_task

    deltas = [e["text"] for e in fake_ws.sent if e["type"] == "assistant_delta"]
    assert deltas == ["Hi ", "there"]
    done = [e for e in fake_ws.sent if e["type"] == "assistant_done"]
    assert len(done) == 1
    assert done[0]["text"] == "Hi there"

    # Only after the turn completed did STT tear down.
    assert fake_stt.closed is True
    assert session.stt is None


async def test_text_input_path_still_works_unchanged() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("Hello there"))
    session = Session(fake_ws)
    session.client = fake_openai
    session._recorder = FakeEventRecorder()  # noqa: SLF001 - keep DB out of fake-ws timing
    session.tts = FakeTTSProvider()  # keep TTS hermetic/offline

    fake_ws.queue_text('{"type": "text_input", "text": "hi"}')
    fake_ws.queue_disconnect()

    await session.run()

    types_seen = [e["type"] for e in fake_ws.sent]
    assert "ready" in types_seen
    assert "assistant_done" in types_seen

    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    # No mic active -> settles back to idle, not listening.
    assert state_events[-1] == "idle"
    assert "thinking" in state_events

    assert len(fake_openai.responses.calls) == 1
    call_input = fake_openai.responses.calls[0]["input"]
    assert call_input[-1] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hi"}],
    }
