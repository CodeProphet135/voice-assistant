"""Append-only EventRecorder (Phase 6).

One recorder per Session. ``record()`` is a synchronous, non-blocking call on
the hot path: it assigns monotonic ``seq`` + metadata and enqueues onto an
in-memory queue. A background task batch-writes to Postgres off the hot path.
Best-effort: a DB failure logs and is swallowed so it never breaks the live
conversation, and the recorder self-disables when Postgres is unreachable."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa

from voice_assistant import db
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow

_logger = logging.getLogger(__name__)
_STOP = object()


@dataclass
class EventMetadata:
    session_id: uuid.UUID
    seq: int
    ts: datetime
    turn_id: uuid.UUID | None
    trace_id: str | None
    span_id: str | None


@dataclass
class RecordedEvent:
    metadata: EventMetadata
    payload: dict


class EventRecorder:
    def __init__(self, session_id: uuid.UUID) -> None:
        self._session_id = session_id
        self._seq = 0
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._enabled = False
        self._title: str | None = None

    async def start(self) -> None:
        try:
            async with db.async_session_factory() as s:
                s.add(SessionRow(id=self._session_id))
                await s.commit()
        except Exception:  # noqa: BLE001 - disable recording, never break the session
            _logger.warning("EventRecorder disabled: could not create session row", exc_info=True)
            self._enabled = False
            return
        self._enabled = True
        self._task = asyncio.create_task(self._flush_loop())

    def record(self, event, *, turn_id, trace_id, span_id) -> None:
        if not self._enabled:
            return
        meta = EventMetadata(
            session_id=self._session_id,
            seq=self._seq,
            ts=datetime.now(UTC),
            turn_id=turn_id,
            trace_id=trace_id,
            span_id=span_id,
        )
        self._seq += 1
        self._queue.put_nowait(RecordedEvent(meta, event.model_dump()))

    def note_title(self, text: str) -> None:
        if self._title is None:
            self._title = text

    async def _flush_loop(self) -> None:
        while True:
            first = await self._queue.get()
            if first is _STOP:
                return
            batch = [first]
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is _STOP:
                    await self._write_batch(batch)
                    return
                batch.append(item)
            await self._write_batch(batch)

    async def _write_batch(self, batch: list[RecordedEvent]) -> None:
        try:
            async with db.async_session_factory() as s:
                s.add_all(
                    [
                        Event(
                            session_id=r.metadata.session_id,
                            seq=r.metadata.seq,
                            ts=r.metadata.ts,
                            turn_id=r.metadata.turn_id,
                            trace_id=r.metadata.trace_id,
                            span_id=r.metadata.span_id,
                            type=r.payload["type"],
                            payload=r.payload,
                        )
                        for r in batch
                    ]
                )
                await s.commit()
        except Exception:  # noqa: BLE001 - best effort; dropped events must not crash
            _logger.warning(
                "EventRecorder batch write failed (%d events)", len(batch), exc_info=True
            )

    async def stop(self) -> None:
        if not self._enabled:
            return
        self._queue.put_nowait(_STOP)
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                _logger.warning("EventRecorder flush task errored on stop", exc_info=True)
        try:
            async with db.async_session_factory() as s:
                await s.execute(
                    sa.update(SessionRow)
                    .where(SessionRow.id == self._session_id)
                    .values(ended_at=datetime.now(UTC), title=self._title)
                )
                await s.commit()
        except Exception:  # noqa: BLE001
            _logger.warning("EventRecorder could not finalize session row", exc_info=True)
