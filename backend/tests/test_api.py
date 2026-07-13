"""REST endpoint tests (Phase 6). Seed rows in a rolled-back Postgres tx and
call the endpoint coroutines directly against the monkeypatched factory."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from voice_assistant import db
from voice_assistant.api import list_events, list_sessions
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow


async def _seed(sid: uuid.UUID):
    async with db.async_session_factory() as s:
        s.add(SessionRow(id=sid, title="hello"))
        await s.flush()
        s.add_all(
            [
                Event(
                    session_id=sid, seq=1, ts=datetime.now(UTC), turn_id=None,
                    trace_id=None, span_id=None, type="state",
                    payload={"type": "state", "state": "thinking"},
                ),
                Event(
                    session_id=sid, seq=0, ts=datetime.now(UTC), turn_id=None,
                    trace_id=None, span_id=None, type="ready",
                    payload={"type": "ready", "session_id": sid.hex},
                ),
            ]
        )
        await s.commit()


async def test_list_sessions_returns_seeded(pg_db):
    sid = uuid.uuid4()
    await _seed(sid)
    out = await list_sessions()
    assert any(row.id == sid and row.title == "hello" for row in out)


async def test_list_events_ordered_by_seq(pg_db):
    sid = uuid.uuid4()
    await _seed(sid)
    out = await list_events(sid)
    assert [e.seq for e in out] == [0, 1]
    assert out[0].type == "ready"
    assert out[1].payload == {"type": "state", "state": "thinking"}


async def test_list_events_unknown_session_404(pg_db):
    with pytest.raises(HTTPException) as exc:
        await list_events(uuid.uuid4())
    assert exc.value.status_code == 404
