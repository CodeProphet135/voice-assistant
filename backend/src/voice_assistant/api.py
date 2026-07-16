"""Read-only REST surface for the Timeline/Replay UI (Phase 6)."""

import logging
import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from voice_assistant import db
from voice_assistant.gaps import find_seq_gaps
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow

router = APIRouter(prefix="/api")
_logger = logging.getLogger(__name__)


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    title: str | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    ts: datetime
    turn_id: uuid.UUID | None
    trace_id: str | None
    span_id: str | None
    type: str
    payload: dict


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions():
    has_turns = (
        sa.select(Event.session_id)
        .where(Event.session_id == SessionRow.id, Event.turn_id.isnot(None))
        .exists()
    )
    async with db.async_session_factory() as s:
        rows = (
            await s.execute(
                sa.select(SessionRow).where(has_turns).order_by(SessionRow.started_at.desc())
            )
        ).scalars().all()
    return rows


@router.get("/sessions/{session_id}/events", response_model=list[EventOut])
async def list_events(session_id: uuid.UUID):
    async with db.async_session_factory() as s:
        exists = (
            await s.execute(sa.select(SessionRow.id).where(SessionRow.id == session_id))
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = (
            await s.execute(
                sa.select(Event).where(Event.session_id == session_id).order_by(Event.seq)
            )
        ).scalars().all()
    gaps = find_seq_gaps([r.seq for r in rows])
    if gaps:
        _logger.warning(
            "session %s has %d seq gap(s): %s",
            session_id,
            len(gaps),
            ", ".join(
                f"{g.after_seq}->{g.before_seq} ({g.missing_count} missing)" for g in gaps
            ),
        )
    return rows
