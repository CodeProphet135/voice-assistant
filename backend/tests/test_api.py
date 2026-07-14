"""REST endpoint tests (Phase 6). Seed rows in a rolled-back Postgres tx and
call the endpoint coroutines directly against the monkeypatched factory."""

import logging
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException

from voice_assistant import db
from voice_assistant.api import list_events, list_sessions
from voice_assistant.main import app
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


async def test_list_sessions_newest_first(pg_db):
    now = datetime.now(UTC)
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    async with db.async_session_factory() as s:
        s.add(SessionRow(id=old_id, title="old", started_at=now - timedelta(hours=1)))
        s.add(SessionRow(id=new_id, title="new", started_at=now))
        await s.commit()
    out = await list_sessions()
    ids = [r.id for r in out]
    assert ids.index(new_id) < ids.index(old_id)


async def test_endpoints_serialize_over_http(pg_db):
    sid = uuid.uuid4()
    await _seed(sid)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/sessions")
        assert r.status_code == 200
        assert any(row["id"] == str(sid) and row["title"] == "hello" for row in r.json())

        r2 = await client.get(f"/api/sessions/{sid}/events")
        assert r2.status_code == 200
        events = r2.json()
        assert [e["seq"] for e in events] == [0, 1]
        assert set(events[0].keys()) == {
            "seq", "ts", "turn_id", "trace_id", "span_id", "type", "payload",
        }
        assert events[0]["type"] == "ready"

        r3 = await client.get(f"/api/sessions/{uuid.uuid4()}/events")
        assert r3.status_code == 404


async def test_list_events_logs_seq_gap(pg_db, caplog):
    sid = uuid.uuid4()
    async with db.async_session_factory() as s:
        s.add(SessionRow(id=sid, title="gap"))
        await s.flush()
        s.add_all(
            [
                Event(
                    session_id=sid, seq=0, ts=datetime.now(UTC), turn_id=None,
                    trace_id=None, span_id=None, type="ready",
                    payload={"type": "ready", "session_id": sid.hex},
                ),
                Event(
                    session_id=sid, seq=3, ts=datetime.now(UTC), turn_id=None,
                    trace_id=None, span_id=None, type="state",
                    payload={"type": "state", "state": "thinking"},
                ),
            ]
        )
        await s.commit()

    with caplog.at_level(logging.WARNING, logger="voice_assistant.api"):
        out = await list_events(sid)

    assert [e.seq for e in out] == [0, 3]
    assert any("seq gap" in r.message for r in caplog.records)
