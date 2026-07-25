"""A malformed or version-skewed client WS frame must be a per-frame
protocol violation, not a reason to tear down the whole connection.

Before this, a bad frame propagated out of ``_dispatch`` to ``run()``'s
last-resort ``except Exception``, which ends the session (stops STT, closes
TTS) over a single frame. These tests drive ``Session.run()`` end-to-end with
a real (unmocked) ``_dispatch`` and assert the session survives a bad frame
and keeps serving subsequent turns.
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from conftest import FakeEventRecorder, FakeOpenAI, FakeTTSProvider, FakeWebSocket, make_text_turn

from voice_assistant.session import Session


def make_session(fake_ws: FakeWebSocket, fake_openai: FakeOpenAI) -> Session:
    session = Session(fake_ws)
    session.client = fake_openai
    session._recorder = FakeEventRecorder()  # noqa: SLF001 - keep DB out of fake-ws timing
    session.tts = FakeTTSProvider()
    return session


async def _drive(n: int = 30) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_invalid_json_frame_is_reported_but_session_continues() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("hi there"))
    session = make_session(fake_ws, fake_openai)

    fake_ws.queue_text("not json at all")
    run_task = asyncio.create_task(session.run())
    await _drive()

    # Reported as an error frame, not a crash...
    errors = [e for e in fake_ws.sent if e["type"] == "error"]
    assert len(errors) == 1
    assert "Malformed client frame" in errors[0]["message"]

    # ...and the connection is still alive: a subsequent, well-formed turn
    # goes through normally.
    fake_ws.queue_text('{"type": "text_input", "text": "hello"}')
    await _drive(40)
    assert any(e["type"] == "assistant_done" for e in fake_ws.sent)

    fake_ws.queue_disconnect()
    await run_task


async def test_unrecognized_event_type_is_reported_but_session_continues() -> None:
    fake_ws = FakeWebSocket()
    fake_openai = FakeOpenAI()
    fake_openai.responses.script(make_text_turn("hi there"))
    session = make_session(fake_ws, fake_openai)

    # Valid JSON, but doesn't match any ClientEvent variant -- a
    # ValidationError from parse_client_event's discriminated union, not a
    # JSONDecodeError.
    fake_ws.queue_text('{"type": "not_a_real_event", "foo": "bar"}')
    run_task = asyncio.create_task(session.run())
    await _drive()

    errors = [e for e in fake_ws.sent if e["type"] == "error"]
    assert len(errors) == 1
    assert "Malformed client frame" in errors[0]["message"]

    fake_ws.queue_text('{"type": "text_input", "text": "hello"}')
    await _drive(40)
    assert any(e["type"] == "assistant_done" for e in fake_ws.sent)

    fake_ws.queue_disconnect()
    await run_task
