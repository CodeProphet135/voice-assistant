"""EventRecorder tests. Pure metadata/seq behavior needs no DB; persistence
and ordering run against Postgres (skip if unreachable, like the notes tests)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import uuid

import sqlalchemy as sa
from conftest import FakeTTSProvider, FakeWebSocket, make_text_turn

from voice_assistant import db
from voice_assistant.events import EventRecorder
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow
from voice_assistant.protocol import AssistantDeltaEvent, ReadyEvent
from voice_assistant.session import Session


async def test_record_is_noop_when_disabled():
    rec = EventRecorder(uuid.uuid4())
    # start() never called -> disabled -> record must not raise and must not seq.
    rec.record(ReadyEvent(session_id="x"), turn_id=None, trace_id=None, span_id=None)
    assert rec._seq == 0


async def test_no_events_leaves_no_session_row(pg_db):
    # A connection where nothing was ever recorded (idle page load, a reload
    # without talking, StrictMode's dev double-connect) must not persist a
    # "(untitled session)" row: the row is created lazily on the first event.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    await rec.stop()

    async with db.async_session_factory() as s:
        row = (
            await s.execute(sa.select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one_or_none()
    assert row is None


async def test_first_event_creates_row_and_stop_finalizes(pg_db):
    # The complementary guarantee: once a real event is recorded, the row is
    # created exactly once and stop() finalizes ended_at + title.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    rec.note_title("hello there")
    await rec.stop()

    async with db.async_session_factory() as s:
        row = (
            await s.execute(sa.select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one_or_none()
    assert row is not None
    assert row.title == "hello there"
    assert row.ended_at is not None


async def test_records_events_in_seq_order(pg_db):
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    turn = uuid.uuid4()
    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id="t", span_id="s")
    rec.record(AssistantDeltaEvent(text="he"), turn_id=turn, trace_id="t", span_id="s")
    rec.record(AssistantDeltaEvent(text="llo"), turn_id=turn, trace_id="t", span_id="s")
    rec.note_title("hello there")
    await rec.stop()

    async with db.async_session_factory() as s:
        rows = (
            await s.execute(sa.select(Event).where(Event.session_id == sid).order_by(Event.seq))
        ).scalars().all()
    assert [r.seq for r in rows] == [0, 1, 2]
    assert [r.type for r in rows] == ["ready", "assistant_delta", "assistant_delta"]
    assert rows[1].turn_id == turn and rows[0].turn_id is None
    assert rows[1].trace_id == "t" and rows[1].span_id == "s"
    assert rows[2].payload == {"type": "assistant_delta", "text": "llo"}


async def test_turn_events_share_turn_id(pg_db, fake_openai):
    ws = FakeWebSocket()
    session = Session(ws)
    session.client = fake_openai
    session.tts = FakeTTSProvider()
    fake_openai.responses.script(make_text_turn("hi there"))

    await session._recorder.start()
    session._new_turn_id()
    tid = session._current_turn_id
    await session._run_turn("hi there")
    await session._recorder.stop()

    async with db.async_session_factory() as s:
        rows = (
            await s.execute(
                sa.select(Event)
                .where(Event.session_id == session._session_uuid)
                .order_by(Event.seq)
            )
        ).scalars().all()
    turn_types = [r.type for r in rows if r.turn_id == tid]
    assert "assistant_delta" in turn_types
    assert "llm_request" in turn_types
    assert all(r.turn_id == tid for r in rows if r.type == "assistant_delta")
