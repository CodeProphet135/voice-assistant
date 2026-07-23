"""Append-only EventRecorder.

One recorder per Session. ``record()`` is a synchronous, non-blocking call on
the hot path: it assigns monotonic ``seq`` + metadata and enqueues onto an
in-memory queue. A background task batch-writes to Postgres off the hot path.
Best-effort: a DB failure logs and is swallowed so it never breaks the live
conversation. A transient failure is retried with backoff; if enough batches
fail in a row the recorder self-disables and periodically re-probes
reachability (mirroring the one-shot probe in ``start()``), re-enabling once
Postgres is reachable again. ``seq`` is assigned before a batch is attempted
and is never reused, so a batch that is still permanently dropped after
retries leaves a real, detectable hole in the persisted ``seq`` column --
see ``gaps.find_seq_gaps``, used by the read-side API."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from voice_assistant import db
from voice_assistant.config import settings
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow

_logger = logging.getLogger(__name__)
_STOP = object()

# Event types a connection produces just by existing, before the user has done
# anything. They are recorded (replay needs them) but on their own they must
# not trigger a write -- see _flush_loop.
_CONNECT_ONLY_TYPES = frozenset({"ready"})


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
        # The sessions row is created lazily, on the first event actually
        # written (see _write_batch). A connection that records nothing --
        # an idle page load, a reload without talking, StrictMode's dev
        # double-connect -- leaves no "(untitled session)" row behind.
        self._session_created = False
        # Consecutive fully-exhausted batch failures; reset on any success.
        self._consecutive_failures = 0
        self._reprobe_task: asyncio.Task | None = None

    async def start(self) -> None:
        # Probe reachability only; do NOT create the session row here (that is
        # deferred to the first event). A DB failure self-disables recording so
        # it never breaks the live conversation.
        try:
            async with db.async_session_factory() as s:
                await s.execute(sa.text("SELECT 1"))
        except Exception:  # noqa: BLE001 - disable recording, never break the session
            _logger.warning("EventRecorder disabled: database unreachable", exc_info=True)
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
        # Events recorded before the first substantive one are held here rather
        # than written, because writing is what creates the sessions row: a
        # connection that only ever greets (an idle page load, a reload without
        # talking, StrictMode's dev double-connect) must leave no row behind.
        # They are flushed alongside the substantive event that unblocks them,
        # so a persisted stream still starts at seq 0 with `ready` intact.
        held: list[RecordedEvent] = []
        while True:
            first = await self._queue.get()
            if first is _STOP:
                return
            batch = [first]
            stopping = False
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is _STOP:
                    stopping = True
                    break
                batch.append(item)
            held.extend(batch)
            if any(r.payload["type"] not in _CONNECT_ONLY_TYPES for r in held):
                pending, held = held, []
                await self._write_batch(pending)
            if stopping:
                return

    async def _write_batch(self, batch: list[RecordedEvent]) -> None:
        start_seq, end_seq = batch[0].metadata.seq, batch[-1].metadata.seq
        for attempt in range(settings.event_write_max_attempts):
            try:
                async with db.async_session_factory() as s:
                    # Create the sessions row on first write so the events FK is
                    # satisfied. ON CONFLICT DO NOTHING keeps it idempotent if a
                    # prior batch already created it (the flag guards the common
                    # case; this guards a retried batch after a failed commit).
                    if not self._session_created:
                        await s.execute(
                            pg_insert(SessionRow)
                            .values(id=self._session_id)
                            .on_conflict_do_nothing(index_elements=["id"])
                        )
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
                    # Only mark created after a successful commit -- a failed
                    # commit leaves the flag False so the next batch retries the
                    # row insert.
                    self._session_created = True
                self._consecutive_failures = 0
                return
            except Exception:  # noqa: BLE001 - best effort; dropped events must not crash
                is_last_attempt = attempt == settings.event_write_max_attempts - 1
                if not is_last_attempt:
                    _logger.warning(
                        "EventRecorder batch write failed (seq %d-%d), retrying "
                        "(attempt %d/%d)",
                        start_seq, end_seq, attempt + 1, settings.event_write_max_attempts,
                        exc_info=True,
                    )
                    await asyncio.sleep(settings.event_write_retry_base_seconds * (2**attempt))
                    continue
                _logger.error(
                    "EventRecorder dropping batch after %d attempts (seq %d-%d, %d events)",
                    settings.event_write_max_attempts, start_seq, end_seq, len(batch),
                    exc_info=True,
                )
                self._consecutive_failures += 1
                self._maybe_disable_and_reprobe()

    def _maybe_disable_and_reprobe(self) -> None:
        if self._consecutive_failures < settings.event_recorder_max_consecutive_failures:
            return
        if self._reprobe_task is not None and not self._reprobe_task.done():
            return
        self._enabled = False
        _logger.warning(
            "EventRecorder disabled after %d consecutive batch failures; "
            "reprobing every %.1fs",
            self._consecutive_failures, settings.event_recorder_reprobe_seconds,
        )
        self._reprobe_task = asyncio.create_task(self._reprobe_loop())

    async def _reprobe_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.event_recorder_reprobe_seconds)
            try:
                async with db.async_session_factory() as s:
                    await s.execute(sa.text("SELECT 1"))
            except Exception:  # noqa: BLE001 - keep reprobing until reachable
                continue
            self._consecutive_failures = 0
            self._enabled = True
            _logger.warning("EventRecorder re-enabled: database reachable again")
            return

    async def stop(self) -> None:
        if self._reprobe_task is not None and not self._reprobe_task.done():
            self._reprobe_task.cancel()
        if not self._enabled and self._task is None:
            return
        if self._task is not None:
            self._queue.put_nowait(_STOP)
            try:
                await self._task
            except Exception:  # noqa: BLE001
                _logger.warning("EventRecorder flush task errored on stop", exc_info=True)
        # The flush loop above drains any queued events before returning, so
        # _session_created is now accurate. If nothing was ever written there
        # is no row to finalize -- leave no empty session behind.
        if not self._session_created:
            return
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
