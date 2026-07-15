"""EventRecorder tests. Pure metadata/seq behavior needs no DB; persistence
and ordering run against Postgres (skip if unreachable, like the notes tests)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import asyncio
import contextlib
import uuid

import sqlalchemy as sa
from conftest import FakeTTSProvider, FakeWebSocket, make_text_turn

from voice_assistant import db
from voice_assistant.config import settings
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


async def test_ready_alone_leaves_no_session_row(pg_db):
    # Every connection greets with `ready` before the user has done anything,
    # so `ready` on its own must not create a row -- otherwise an idle reload
    # (and StrictMode's dev double-connect) litters the sessions list.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    await rec.stop()

    async with db.async_session_factory() as s:
        row = (
            await s.execute(sa.select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one_or_none()
    assert row is None


async def test_first_event_creates_row_and_stop_finalizes(pg_db):
    # The complementary guarantee: once a substantive event is recorded, the
    # row is created exactly once and stop() finalizes ended_at + title.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    rec.record(AssistantDeltaEvent(text="hi"), turn_id=None, trace_id=None, span_id=None)
    rec.note_title("hello there")
    await rec.stop()

    async with db.async_session_factory() as s:
        row = (
            await s.execute(sa.select(SessionRow).where(SessionRow.id == sid))
        ).scalar_one_or_none()
    assert row is not None
    assert row.title == "hello there"
    assert row.ended_at is not None


class _FailingSession:
    """Stands in for an ``AsyncSession`` whose commit always raises."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *args, **kwargs):
        return None

    def add_all(self, *args, **kwargs):
        return None

    async def commit(self):
        raise RuntimeError("simulated DB failure")


class _FlakyFactory:
    """Wraps a real session factory: the first ``fail_times`` calls return a
    session that fails on commit; afterwards it delegates to the real factory."""

    def __init__(self, real_factory, fail_times: int) -> None:
        self._real_factory = real_factory
        self._fail_remaining = fail_times

    def __call__(self):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            return _FailingSession()
        return self._real_factory()


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


async def test_batch_retries_transient_failure_then_succeeds(pg_db, monkeypatch):
    # One failed attempt, then a real write within event_write_max_attempts
    # (default 3) must still land the event -- no drop for a transient blip.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    real_factory = db.async_session_factory
    monkeypatch.setattr(db, "async_session_factory", _FlakyFactory(real_factory, fail_times=1))

    rec.record(AssistantDeltaEvent(text="hi"), turn_id=None, trace_id=None, span_id=None)
    await rec.stop()

    async with real_factory() as s:
        rows = (
            await s.execute(sa.select(Event).where(Event.session_id == sid))
        ).scalars().all()
    assert [r.seq for r in rows] == [0]


async def test_recorder_disables_then_reenables_after_reprobe(pg_db, monkeypatch):
    # Two consecutive fully-exhausted batch failures (threshold=2) must
    # disable the recorder. The reprobe interval is held long here so the
    # disabled state can be asserted deterministically (no race against a
    # background sleep); the reprobe itself is then driven directly (not
    # via wall-clock racing) to verify it re-enables once the DB is
    # reachable again. The two failed batches leave a real seq gap (0,1
    # dropped) -- this test is also a fixture for gaps.find_seq_gaps().
    monkeypatch.setattr(settings, "event_write_max_attempts", 1)
    monkeypatch.setattr(settings, "event_recorder_max_consecutive_failures", 2)
    monkeypatch.setattr(settings, "event_recorder_reprobe_seconds", 100.0)

    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    real_factory = db.async_session_factory
    monkeypatch.setattr(db, "async_session_factory", _FlakyFactory(real_factory, fail_times=2))

    rec.record(AssistantDeltaEvent(text="a"), turn_id=None, trace_id=None, span_id=None)
    await asyncio.sleep(0.05)  # let _flush_loop drain batch 1 (seq 0) -> fails
    rec.record(AssistantDeltaEvent(text="b"), turn_id=None, trace_id=None, span_id=None)
    await asyncio.sleep(0.05)  # let _flush_loop drain batch 2 (seq 1) -> fails, disables

    assert rec._enabled is False
    assert rec._reprobe_task is not None

    # Drive the reprobe directly instead of racing real time: cancel the
    # long-interval background task, shrink the interval, and await one
    # reprobe attempt to completion.
    rec._reprobe_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await rec._reprobe_task
    monkeypatch.setattr(settings, "event_recorder_reprobe_seconds", 0.01)
    await rec._reprobe_loop()  # flaky factory is exhausted -> real factory -> succeeds

    assert rec._enabled is True

    rec.record(AssistantDeltaEvent(text="c"), turn_id=None, trace_id=None, span_id=None)
    await rec.stop()

    async with real_factory() as s:
        rows = (
            await s.execute(sa.select(Event).where(Event.session_id == sid).order_by(Event.seq))
        ).scalars().all()
    # seq 0 and 1 were permanently dropped; only seq 2 (recorded post-reprobe) landed.
    assert [r.seq for r in rows] == [2]


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
