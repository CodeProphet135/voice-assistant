"""Tests for the TTS turn path wired into Session (Phase 3 voice OUT).

Drives a full turn (both the STT-commit path and the text-input path) with a
FakeSTTProvider (imported from test_session_stt.py), a FakeTTSProvider and
FakeWebSocket (conftest.py), and FakeOpenAI (conftest.py) scripting the
agent's Responses stream. No Deepgram key or real network I/O is involved.
"""

import asyncio
import os

# Session() constructs a real AsyncOpenAI client (its .responses is swapped
# for FakeOpenAI right after); the installed SDK version requires *some*
# credential to be present at construction time even though we never make a
# real call. Set a harmless placeholder so these tests need no real key.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from conftest import FakeOpenAI, FakeTTSProvider, FakeWebSocket, make_text_turn
from test_session_stt import FakeSTTProvider

from voice_assistant.providers.base import Transcript
from voice_assistant.session import Session


def make_session(
    fake_ws: FakeWebSocket, fake_openai: FakeOpenAI
) -> tuple[Session, FakeSTTProvider, FakeTTSProvider]:
    session = Session(fake_ws)
    session.client = fake_openai
    fake_stt = FakeSTTProvider()
    session._make_stt_provider = lambda: fake_stt  # noqa: SLF001 - test injection seam
    fake_tts = FakeTTSProvider()
    session.tts = fake_tts
    return session, fake_stt, fake_tts


async def _drive(n: int = 30) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_stt_turn_speaks_sentences_in_order() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("Hello there. How are you?"))
    session, fake_stt, fake_tts = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(2)

    fake_stt.push(Transcript(text="hi", is_final=True, speech_final=True))
    await _drive(40)

    fake_ws.queue_disconnect()
    await run_task

    tts_start_events = [e for e in fake_ws.sent if e["type"] == "tts_start"]
    assert [e["sentence_index"] for e in tts_start_events] == list(
        range(len(tts_start_events))
    )
    assert len(tts_start_events) >= 2, "chunker should have split into >=2 sentences"

    # Each tts_start's text matches what was actually synthesized, in order.
    assert [e["text"] for e in tts_start_events] == fake_tts.synthesized

    # Binary frames sent match exactly what the fake TTS produced, per
    # sentence, in the same order.
    expected_frames = [b"PCM:" + text.encode() for text in fake_tts.synthesized]
    assert fake_ws.sent_bytes == expected_frames

    # Exactly one tts_end, emitted after the last tts_start.
    tts_end_indices = [i for i, e in enumerate(fake_ws.sent) if e["type"] == "tts_end"]
    assert len(tts_end_indices) == 1
    last_tts_start_index = max(
        i for i, e in enumerate(fake_ws.sent) if e["type"] == "tts_start"
    )
    assert tts_end_indices[0] > last_tts_start_index

    # State sequence: thinking before speaking, final state is listening
    # (mic is still open).
    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert "thinking" in state_events
    assert "speaking" in state_events
    assert state_events.index("thinking") < state_events.index("speaking")
    assert state_events[-1] == "listening"


async def test_empty_reply_still_terminates_without_speaking() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    # An empty text turn: no deltas, straight to response.completed. The
    # chunker never yields a sentence to flush, so on_sentence is never
    # called for real text -- only the terminating sentinel is enqueued.
    fake_openai.responses.script(make_text_turn(""))
    session, fake_stt, fake_tts = make_session(fake_ws, fake_openai)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(2)

    fake_stt.push(Transcript(text="hi", is_final=True, speech_final=True))
    await _drive(40)

    fake_ws.queue_disconnect()
    await run_task

    assert [e for e in fake_ws.sent if e["type"] == "tts_start"] == []
    tts_end_events = [e for e in fake_ws.sent if e["type"] == "tts_end"]
    assert len(tts_end_events) == 1
    assert fake_ws.sent_bytes == []
    assert fake_tts.synthesized == []

    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert "speaking" not in state_events
    assert state_events[-1] == "listening"


async def test_text_input_path_also_speaks() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("Hello there. How are you?"))
    session = Session(fake_ws)
    session.client = fake_openai
    fake_tts = FakeTTSProvider()
    session.tts = fake_tts

    fake_ws.queue_text('{"type": "text_input", "text": "hi"}')
    fake_ws.queue_disconnect()

    await session.run()

    tts_start_events = [e for e in fake_ws.sent if e["type"] == "tts_start"]
    assert len(tts_start_events) >= 2
    assert [e["sentence_index"] for e in tts_start_events] == list(
        range(len(tts_start_events))
    )

    tts_end_events = [e for e in fake_ws.sent if e["type"] == "tts_end"]
    assert len(tts_end_events) == 1

    expected_frames = [b"PCM:" + text.encode() for text in fake_tts.synthesized]
    assert fake_ws.sent_bytes == expected_frames
    assert fake_ws.sent_bytes != []

    # No mic active -> settles back to idle after speaking, not listening.
    state_events = [e["state"] for e in fake_ws.sent if e["type"] == "state"]
    assert state_events[-1] == "idle"
    assert "speaking" in state_events
