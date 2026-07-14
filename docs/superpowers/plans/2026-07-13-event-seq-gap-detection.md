# Event Seq Gap Detection & Batch-Write Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `EventRecorder` from silently dropping event batches forever, and make any batch that is still permanently lost visible instead of silent.

**Architecture:** Three independent, additive changes, each testable on its own:
1. `_write_batch` in `backend/src/voice_assistant/events.py` gets bounded retry-with-backoff for transient failures, plus a consecutive-failure counter that self-disables the recorder and spawns a periodic reachability re-probe (mirroring the existing one-shot probe in `start()`), re-enabling recording once Postgres comes back.
2. A new pure helper, `backend/src/voice_assistant/gaps.py`, scans a session's persisted `seq` values for holes. `seq` is assigned in-memory before a batch is attempted and is never reused (`events.py:75,81`), so a permanently dropped batch always leaves a visible, computable hole in the persisted column — no schema change, no synthetic marker rows needed. `api.py`'s `list_events` endpoint calls this helper and logs a warning when it finds gaps.
3. A frontend mirror of the same helper, `frontend/src/gaps.ts`, is used by `EventInspector.tsx` to render an inline "N events missing" banner between two events whose `seq` isn't contiguous.

**Scope decision:** `timeline.ts`/`Timeline.tsx` render a coarse turn-level waterfall, not a per-event list — there's no natural visual slot for "event 7 is missing" at that altitude. This plan does not touch `timeline.ts`. `EventInspector.tsx` is the per-event list view and is where a human would actually see a gap, so that's where the frontend fix lands.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async (backend), TypeScript / Vitest (frontend). No new dependencies.

## Global Constraints

- Backend tests run with `cd backend && uv run pytest`; frontend tests with `cd frontend && npm run test` (Vitest) and `npm run typecheck` (tsc). Lint: `cd backend && uv run ruff check .` and `cd frontend && npm run typecheck`.
- `EventRecorder` must remain best-effort: a DB problem must never raise out of `record()`/`_write_batch()`/`stop()` into the live session (see `events.py:1-7` module docstring — this is a deliberate design invariant, do not remove the swallow-and-log behavior, only add retry/backoff/detection around it).
- Backend tests that touch Postgres use the `pg_db` fixture (`backend/tests/conftest.py`), which skips (not fails) if Postgres is unreachable. Follow this pattern for all new DB-touching tests.
- `os.environ.setdefault("OPENAI_API_KEY", "test-key")` must be set before importing `voice_assistant.session` in any test file that imports it (see top of `backend/tests/test_events.py`) — only relevant if a new test file imports `session`.
- Conventional commits, one focused commit per task (see `git log --oneline`).

---

### Task 1: Bounded retry-with-backoff and self-disable/re-probe in `EventRecorder`

**Files:**
- Modify: `backend/src/voice_assistant/config.py`
- Modify: `backend/src/voice_assistant/events.py:42-138` (constructor and `_write_batch`, add `_reprobe_loop`; also touches `stop()`)
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Consumes: `voice_assistant.config.settings` (existing `Settings` instance).
- Produces: no new public methods on `EventRecorder` — `start()`, `record()`, `stop()`, `note_title()` keep their existing signatures (`FakeEventRecorder` in `conftest.py` already mirrors this surface and needs no change). Adds two new `Settings` fields consumed only inside `events.py`: `event_write_max_attempts: int`, `event_write_retry_base_seconds: float`, `event_recorder_max_consecutive_failures: int`, `event_recorder_reprobe_seconds: float`.

- [ ] **Step 1: Add retry/backoff/disable config to `Settings`**

Edit `backend/src/voice_assistant/config.py` — add a new section after `# Database` (currently ends at line 26):

```python
    # Database
    database_url: str = "postgresql+asyncpg://va:va@localhost:5432/voice_assistant"

    # Event recording: batch-write retry/backoff, and self-disable + periodic
    # reachability re-probe after repeated batch-write failures (mirrors the
    # one-shot probe EventRecorder.start() already does).
    event_write_max_attempts: int = 3
    event_write_retry_base_seconds: float = 0.2
    event_recorder_max_consecutive_failures: int = 3
    event_recorder_reprobe_seconds: float = 5.0
```

- [ ] **Step 2: Write the failing tests**

These tests need a fake DB-session factory that fails a controllable number of times before delegating to the real one. Add this helper and the tests to `backend/tests/test_events.py` (append after the existing imports at the top, i.e. after line 18, and the tests at the end of the file, i.e. after line 82 / before `test_turn_events_share_turn_id`):

```python
# --- add near the top, after the existing imports (line 18) ---
import asyncio

from voice_assistant.config import settings
```

```python
# --- add these helper classes right before test_records_events_in_seq_order (line 63) ---
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
```

```python
# --- add these tests right before test_turn_events_share_turn_id (line 85) ---
async def test_batch_retries_transient_failure_then_succeeds(pg_db, monkeypatch):
    # One failed attempt, then a real write within event_write_max_attempts
    # (default 3) must still land the event -- no drop for a transient blip.
    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    real_factory = db.async_session_factory
    monkeypatch.setattr(db, "async_session_factory", _FlakyFactory(real_factory, fail_times=1))

    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    await rec.stop()

    async with real_factory() as s:
        rows = (
            await s.execute(sa.select(Event).where(Event.session_id == sid))
        ).scalars().all()
    assert [r.seq for r in rows] == [0]


async def test_recorder_disables_then_reenables_after_reprobe(pg_db, monkeypatch):
    # Two consecutive fully-exhausted batch failures (threshold=2) must
    # disable the recorder; once the DB is reachable again the periodic
    # reprobe must re-enable it, and events recorded after that must persist.
    # The two failed batches leave a real seq gap (0,1 dropped) -- this test
    # is also a fixture for gaps.find_seq_gaps().
    monkeypatch.setattr(settings, "event_write_max_attempts", 1)
    monkeypatch.setattr(settings, "event_recorder_max_consecutive_failures", 2)
    monkeypatch.setattr(settings, "event_recorder_reprobe_seconds", 0.01)

    sid = uuid.uuid4()
    rec = EventRecorder(sid)
    await rec.start()
    real_factory = db.async_session_factory
    monkeypatch.setattr(db, "async_session_factory", _FlakyFactory(real_factory, fail_times=2))

    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    await asyncio.sleep(0.05)  # let _flush_loop drain batch 1 (seq 0) -> fails
    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    await asyncio.sleep(0.05)  # let _flush_loop drain batch 2 (seq 1) -> fails, disables

    assert rec._enabled is False

    await asyncio.sleep(0.1)  # past reprobe_seconds; flaky factory now exhausted -> reprobe succeeds
    assert rec._enabled is True

    rec.record(ReadyEvent(session_id=sid.hex), turn_id=None, trace_id=None, span_id=None)
    await rec.stop()

    async with real_factory() as s:
        rows = (
            await s.execute(sa.select(Event).where(Event.session_id == sid).order_by(Event.seq))
        ).scalars().all()
    # seq 0 and 1 were permanently dropped; only seq 2 (recorded post-reprobe) landed.
    assert [r.seq for r in rows] == [2]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_events.py -k "retries_transient or disables_then_reenables" -v`
Expected: FAIL — `test_batch_retries_transient_failure_then_succeeds` fails because the current `_write_batch` has no retry (the event is dropped, `rows` is empty). `test_recorder_disables_then_reenables_after_reprobe` fails because `EventRecorder` has no `_enabled` self-disable-on-failure path and no reprobe (it either errors with `AttributeError`-style assertion mismatch or times out never re-enabling).

- [ ] **Step 4: Implement retry/backoff + self-disable + reprobe in `events.py`**

Replace the full contents of `backend/src/voice_assistant/events.py` with:

```python
"""Append-only EventRecorder (Phase 6).

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
```

Note the one behavior change in `stop()`: previously `if not self._enabled: return` skipped flushing entirely, which was correct when "disabled" only ever meant "`start()`'s probe failed, `_flush_loop` was never spawned." Now the recorder can become disabled mid-session *after* `_flush_loop` was already spawned (self-disable path), so `stop()` must still drain and await that existing task — it only skips the flush-and-await step when `self._task is None` (i.e. `start()`'s initial probe failed and no flush loop ever ran).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_events.py -v`
Expected: all tests PASS, including the two new ones and all pre-existing ones (`test_record_is_noop_when_disabled`, `test_no_events_leaves_no_session_row`, `test_first_event_creates_row_and_stop_finalizes`, `test_records_events_in_seq_order`, `test_turn_events_share_turn_id`).

- [ ] **Step 6: Lint**

Run: `cd backend && uv run ruff check .`
Expected: no new issues.

- [ ] **Step 7: Commit**

```bash
git add backend/src/voice_assistant/config.py backend/src/voice_assistant/events.py backend/tests/test_events.py
git commit -m "fix: retry batch writes with backoff and self-heal after repeated failures"
```

---

### Task 2: Detect and log persisted `seq` gaps on the read side

**Files:**
- Create: `backend/src/voice_assistant/gaps.py`
- Create: `backend/tests/test_gaps.py`
- Modify: `backend/src/voice_assistant/api.py`
- Test (append): `backend/tests/test_api.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `voice_assistant.gaps.SeqGap` (dataclass: `after_seq: int`, `before_seq: int`, `missing_count: int`) and `voice_assistant.gaps.find_seq_gaps(seqs: list[int]) -> list[SeqGap]`. `api.py`'s `list_events` response shape is unchanged (`list[EventOut]`) — gaps are logged, not added to the wire format, so the frontend fetch contract in `frontend/src/api.ts` needs no change.

- [ ] **Step 1: Write the failing tests for `find_seq_gaps`**

Create `backend/tests/test_gaps.py`:

```python
from voice_assistant.gaps import SeqGap, find_seq_gaps


def test_no_gaps_for_empty_or_single_element():
    assert find_seq_gaps([]) == []
    assert find_seq_gaps([5]) == []


def test_no_gaps_for_contiguous_seq():
    assert find_seq_gaps([0, 1, 2, 3]) == []


def test_single_gap_detected():
    assert find_seq_gaps([0, 1, 4, 5]) == [SeqGap(after_seq=1, before_seq=4, missing_count=2)]


def test_multiple_gaps_detected():
    assert find_seq_gaps([0, 2, 3, 7]) == [
        SeqGap(after_seq=0, before_seq=2, missing_count=1),
        SeqGap(after_seq=3, before_seq=7, missing_count=3),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_gaps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'voice_assistant.gaps'`.

- [ ] **Step 3: Implement `find_seq_gaps`**

Create `backend/src/voice_assistant/gaps.py`:

```python
"""Detect gaps in a session's persisted seq sequence.

``EventRecorder._write_batch`` (events.py) retries transient failures, but a
batch can still be permanently dropped after repeated failures. ``seq`` is
assigned in-memory before a batch is attempted and is never reused, so a
dropped batch always leaves a real, computable hole in the persisted ``seq``
column for that session -- this scans an ordered list of persisted ``seq``
values for exactly that."""

from dataclasses import dataclass


@dataclass
class SeqGap:
    after_seq: int
    before_seq: int
    missing_count: int


def find_seq_gaps(seqs: list[int]) -> list[SeqGap]:
    """``seqs`` must already be sorted ascending (e.g. a DB query ordered by
    seq). Returns one SeqGap per non-contiguous jump."""
    gaps = []
    for prev, nxt in zip(seqs, seqs[1:]):
        if nxt - prev > 1:
            gaps.append(SeqGap(after_seq=prev, before_seq=nxt, missing_count=nxt - prev - 1))
    return gaps
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_gaps.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for gap logging in `list_events`**

Append to `backend/tests/test_api.py` (after `test_endpoints_serialize_over_http`, and add `import logging` to the imports at the top of the file):

```python
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
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -k logs_seq_gap -v`
Expected: FAIL — `caplog.records` is empty, no warning is logged today.

- [ ] **Step 7: Wire `find_seq_gaps` into `list_events`**

Edit `backend/src/voice_assistant/api.py`. Add imports (after line 1's docstring, alongside the existing `import uuid`):

```python
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
```

Then replace the `list_events` function body (currently lines 47-60):

```python
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
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: all `test_api.py` tests PASS, including the new `test_list_events_logs_seq_gap`.

- [ ] **Step 9: Full backend suite + lint**

Run: `cd backend && uv run pytest && uv run ruff check .`
Expected: all PASS, no lint issues.

- [ ] **Step 10: Commit**

```bash
git add backend/src/voice_assistant/gaps.py backend/tests/test_gaps.py backend/src/voice_assistant/api.py backend/tests/test_api.py
git commit -m "feat: detect and log non-contiguous seq gaps on event read"
```

---

### Task 3: Surface `seq` gaps in the frontend Event Inspector

**Files:**
- Create: `frontend/src/gaps.ts`
- Create: `frontend/src/gaps.test.ts`
- Modify: `frontend/src/components/EventInspector.tsx`

**Interfaces:**
- Consumes: `RecordedEvent` type from `frontend/src/api.ts` (unchanged — `{ seq, ts, turn_id, trace_id, span_id, type, payload }`).
- Produces: `frontend/src/gaps.ts` exports `SeqGap` (`{ afterSeq: number, beforeSeq: number, missingCount: number }`) and `findSeqGaps(events: RecordedEvent[]): SeqGap[]`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/gaps.test.ts` (fixture helper mirrors the `ev()` pattern already used in `frontend/src/timeline.test.ts`):

```typescript
import { describe, it, expect } from 'vitest'
import { findSeqGaps } from './gaps'
import type { RecordedEvent } from './api'

function ev(seq: number, type = 'state'): RecordedEvent {
  return {
    seq,
    ts: new Date(0).toISOString(),
    turn_id: null,
    trace_id: null,
    span_id: null,
    type,
    payload: { type, state: 'idle' } as never,
  }
}

describe('findSeqGaps', () => {
  it('returns nothing for an empty or single-event list', () => {
    expect(findSeqGaps([])).toEqual([])
    expect(findSeqGaps([ev(5)])).toEqual([])
  })

  it('returns nothing for contiguous seq', () => {
    expect(findSeqGaps([ev(0), ev(1), ev(2)])).toEqual([])
  })

  it('finds a single gap', () => {
    expect(findSeqGaps([ev(0), ev(1), ev(4)])).toEqual([
      { afterSeq: 1, beforeSeq: 4, missingCount: 2 },
    ])
  })

  it('finds multiple gaps', () => {
    expect(findSeqGaps([ev(0), ev(2), ev(3), ev(7)])).toEqual([
      { afterSeq: 0, beforeSeq: 2, missingCount: 1 },
      { afterSeq: 3, beforeSeq: 7, missingCount: 3 },
    ])
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- gaps.test.ts`
Expected: FAIL — `Cannot find module './gaps'`.

- [ ] **Step 3: Implement `findSeqGaps`**

Create `frontend/src/gaps.ts`:

```typescript
import type { RecordedEvent } from './api'

export interface SeqGap {
  afterSeq: number
  beforeSeq: number
  missingCount: number
}

// Mirrors backend/src/voice_assistant/gaps.py: seq is assigned before a
// batch write is attempted and never reused, so a permanently dropped
// batch leaves a real, computable hole in an already seq-ordered event list.
export function findSeqGaps(events: RecordedEvent[]): SeqGap[] {
  const gaps: SeqGap[] = []
  for (let i = 1; i < events.length; i++) {
    const prev = events[i - 1].seq
    const next = events[i].seq
    if (next - prev > 1) {
      gaps.push({ afterSeq: prev, beforeSeq: next, missingCount: next - prev - 1 })
    }
  }
  return gaps
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm run test -- gaps.test.ts`
Expected: PASS.

- [ ] **Step 5: Render a gap banner in `EventInspector`**

Replace the full contents of `frontend/src/components/EventInspector.tsx`:

```tsx
import { Fragment } from 'react'
import type { RecordedEvent } from '../api'
import { findSeqGaps } from '../gaps'

export function EventInspector({
  events,
  selected,
  onSelect,
}: {
  events: RecordedEvent[]
  selected: number
  onSelect: (n: number) => void
}) {
  const current = events[selected]
  const gapsByAfterSeq = new Map(findSeqGaps(events).map((g) => [g.afterSeq, g]))
  return (
    <div className="inspector">
      <ol className="inspector-list">
        {events.map((e, i) => {
          const gap = gapsByAfterSeq.get(e.seq)
          return (
            <Fragment key={i}>
              {gap && (
                <li className="inspector-gap" role="alert">
                  {gap.missingCount} event{gap.missingCount === 1 ? '' : 's'} missing (seq{' '}
                  {gap.afterSeq}–{gap.beforeSeq})
                </li>
              )}
              <li className={i === selected ? 'active' : ''}>
                <button onClick={() => onSelect(i)}>
                  <span className="muted">{e.seq}</span> {e.type}
                </button>
              </li>
            </Fragment>
          )
        })}
      </ol>
      {current && (
        <pre className="inspector-payload">
          {JSON.stringify(
            { ts: current.ts, turn_id: current.turn_id, trace_id: current.trace_id, span_id: current.span_id, payload: current.payload },
            null,
            2,
          )}
        </pre>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Add a minimal style for the gap banner**

Find the stylesheet that defines `.inspector-list` / `.muted` (search: `grep -rn "inspector-list" frontend/src`) and add a neighboring rule, e.g.:

```css
.inspector-gap {
  color: var(--color-warning, #b45309);
  font-size: 0.85em;
  padding: 2px 8px;
}
```

(Match the existing file's actual custom-property / color convention rather than inventing `--color-warning` if the stylesheet already defines a warning color — check `frontend/src/index.css` or equivalent first.)

- [ ] **Step 7: Typecheck and run full frontend suite**

Run: `cd frontend && npm run test && npm run typecheck`
Expected: all PASS, no type errors.

- [ ] **Step 8: Manually verify in the browser**

Run `make dev-backend` and `make dev-frontend`, open a session in the Timeline/Replay UI that has a gap (or temporarily lower `event_recorder_max_consecutive_failures`/stop Postgres mid-session to induce one per Task 1's test scenario), navigate to that session's detail page, and confirm the Event Inspector list shows the "N events missing (seq X–Y)" banner at the right place.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/gaps.ts frontend/src/gaps.test.ts frontend/src/components/EventInspector.tsx frontend/src/*.css
git commit -m "feat: show seq gap banners in the Event Inspector"
```

---

### Task 4: Document the residual limitation in `TECH_DEBT.md`

**Files:**
- Modify: `TECH_DEBT.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Add a "Correctness" entry**

Edit `/Volumes/workplace/voice-assistant/TECH_DEBT.md`, adding this bullet to the existing `## Correctness` section (after the "Text-input path isn't under `_turn_lock`" bullet):

```markdown
- **A dropped event batch is detected but not recovered.** `EventRecorder`
  now retries transient write failures and self-disables + re-probes after
  repeated failures (see `events.py`), and `list_events` logs a warning and
  the Event Inspector shows a banner when persisted `seq` values have a gap
  (`gaps.py` / `gaps.ts`). But a batch that is still permanently lost after
  all retries has no recovery path — there's no re-synthesis, backfill, or
  operator alert beyond the log line and the UI banner.
```

- [ ] **Step 2: Commit**

```bash
git add TECH_DEBT.md
git commit -m "docs: note residual seq-gap-detection-without-recovery limitation"
```

---

## Self-Review Notes

- **Spec coverage:** retry/backoff for `_write_batch` (Task 1, Step 4) — done. Self-disable + re-probe/backoff after the initial probe passes (Task 1, `_maybe_disable_and_reprobe`/`_reprobe_loop`) — done. Read-side gap detection in `api.py` (Task 2) — done. `timeline.ts`/`EventInspector.tsx` detection — scoped to `EventInspector.tsx` only, with the reasoning stated up front (Task 3); `timeline.ts` deliberately left untouched.
- **Placeholder scan:** no TBD/"add error handling"/"similar to Task N" — every step has full code.
- **Type consistency:** `SeqGap` fields (`after_seq`/`before_seq`/`missing_count` in Python, `afterSeq`/`beforeSeq`/`missingCount` in TS, matching each language's convention) are used identically in their own test files and consumer code (`api.py`'s log line, `EventInspector.tsx`'s `Map` lookup by `.afterSeq`/`e.seq`).

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-13-event-seq-gap-detection.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
