# Phase 6 — Event Timeline + Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every session's event stream append-only, expose it over REST, and add a `/sessions/:id` view with a latency Timeline, a reducer-driven Replay, and an Event Inspector.

**Architecture:** `Session.emit()` (the single server→client seam) also hands each event to a per-session `EventRecorder` that assigns monotonic `seq` + metadata and batch-writes to Postgres off the hot path. The frontend re-drives the *same* pure `reducer` from `state.ts` over the recorded log for Replay; the Timeline is a pure function of event timestamps.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, OpenTelemetry; React 19 + TypeScript + Vite, `react-router-dom`, Vitest.

## Global Constraints

- Backend: Python 3.12, `uv` for all commands (`cd backend && uv run ...`).
- SQLAlchemy 2.0 async, `postgresql.UUID(as_uuid=True)` + `postgresql.JSONB`, hand-written Alembic migrations matching `0001_notes.py` style.
- DB access in new modules imports the `db` **module** (`from voice_assistant import db`; use `db.async_session_factory`) so tests can monkeypatch it — never `from voice_assistant.db import async_session_factory`.
- `emit()` stays the ONLY server→client + recording path. New internal data becomes a protocol event that flows through `emit()`; the live reducer ignores unknown types via its `default` case.
- `state.ts`'s `reducer` MUST stay pure and MUST NOT be modified for Replay — Replay reuses it verbatim.
- Recording is best-effort: a DB failure logs a warning and never breaks the live conversation. The recorder self-disables when Postgres is unreachable so the DB-less test suite stays green.
- Postgres-dependent tests skip when the DB is unreachable (same posture as `test_migrations.py` / notes tests).
- Conventional commits, one focused commit per task. Run `uv run ruff check . --fix` before committing backend changes (ruff reformats Alembic autogen/import blocks).
- Default model `gpt-5-mini`; read model/reasoning from `settings`, never hardcode.
- Tag `v1.0.0` is a separate explicit step, only after user confirmation — never auto-tag.

---

### Task 1: Session + Event models and migration `0002`

**Files:**
- Modify: `backend/src/voice_assistant/models.py`
- Create: `backend/alembic/versions/0002_sessions_events.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `models.Session` (`id`, `started_at`, `ended_at`, `title`), `models.Event` (`id`, `session_id`, `seq`, `ts`, `turn_id`, `trace_id`, `span_id`, `type`, `payload`) with `UNIQUE(session_id, seq)` and an index on `session_id`. Both set `model_config` is N/A (ORM); pydantic read-models live in Task 3.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_migrations.py`, inside the existing `test_alembic_upgrade_head_builds_schema`, extend the final assertion block:

```python
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            for table in ("notes", "sessions", "events"):
                exists = await conn.execute(sa.text(f"SELECT to_regclass('public.{table}')"))
                assert exists.scalar() == table
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_migrations.py -v`
Expected: with Postgres up, FAIL (`sessions`/`events` do not exist yet — `to_regclass` returns `None`). If Postgres is down, it SKIPs — start it with `make db-up` first to actually exercise this task.

- [ ] **Step 3: Add the ORM models**

Replace the placeholder comment at the bottom of `backend/src/voice_assistant/models.py` and update imports. The full file top imports become:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
```

Then, replacing the `# Session and Event models are added in Phase 6` comment:

```python
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),)
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0002_sessions_events.py`:

```python
"""create sessions and events tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
    )
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("span_id", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
    )
    op.create_index("ix_events_session_id", "events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_events_session_id", table_name="events")
    op.drop_table("events")
    op.drop_table("sessions")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && make db-up >/dev/null 2>&1; uv run pytest tests/test_migrations.py -v`
Expected: PASS (all three tables present after `downgrade base` → `upgrade head`).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run ruff check . --fix
git add backend/src/voice_assistant/models.py backend/alembic/versions/0002_sessions_events.py backend/tests/test_migrations.py
git commit -m "feat(db): sessions + events tables (0002)"
```

---

### Task 2: EventRecorder, emit() wiring, turn_id, new protocol events

**Files:**
- Create: `backend/src/voice_assistant/events.py`
- Modify: `backend/src/voice_assistant/protocol.py`
- Modify: `backend/src/voice_assistant/agent/agent.py`
- Modify: `backend/src/voice_assistant/session.py`
- Modify: `frontend/src/ws.ts`
- Modify: `backend/tests/conftest.py` (add `pg_db` fixture)
- Create: `backend/tests/test_events.py`

**Interfaces:**
- Consumes: `models.Session`, `models.Event` (Task 1); `db.async_session_factory`.
- Produces:
  - `events.EventMetadata(session_id, seq, ts, turn_id, trace_id, span_id)` dataclass.
  - `events.RecordedEvent(metadata, payload)` dataclass.
  - `events.EventRecorder(session_id: uuid.UUID)` with async `start()`, `stop()`, sync `record(event, *, turn_id, trace_id, span_id)`, sync `note_title(text)`.
  - `protocol.SpeechStartedEvent` (`type="speech_started"`), `protocol.LlmRequestEvent` (`type="llm_request"`, `input: list[dict]`, `model: str`, `iteration: int`); both added to the `ServerEvent` union.
  - `Session._current_turn_id: uuid.UUID | None`, `Session._new_turn_id() -> uuid.UUID`, `Session._recorder`, `Session._make_event_recorder()`, `Session._session_uuid: uuid.UUID`.
  - conftest `pg_db` fixture: yields nothing, monkeypatches `db.async_session_factory` onto a rolled-back transaction with all tables created; skips if Postgres is unreachable.

- [ ] **Step 1: Add the two protocol events**

In `backend/src/voice_assistant/protocol.py`, after `SttFinalEvent` add:

```python
class SpeechStartedEvent(BaseModel):
    """Deepgram VAD detected the user beginning to speak (Phase 6). Emitted
    only when no turn is active, so it marks a genuine user-turn onset (not
    the assistant's own TTS echo). A Timeline/Replay signal; the live reducer
    ignores it."""

    type: Literal["speech_started"] = "speech_started"
```

After `AssistantDoneEvent` add:

```python
class LlmRequestEvent(BaseModel):
    """A snapshot of the exact serialized ``input`` sent to the Responses API
    for one tool-loop iteration (Phase 6). The payoff of the ``store=False``
    design — fully serializable conversation state per request. Surfaced in
    the Event Inspector; the live reducer ignores it."""

    type: Literal["llm_request"] = "llm_request"
    input: list[dict]
    model: str
    iteration: int
```

Add both to the `ServerEvent` union:

```python
ServerEvent = (
    ReadyEvent
    | StateEvent
    | SpeechStartedEvent
    | SttPartialEvent
    | SttFinalEvent
    | AssistantDeltaEvent
    | AssistantDoneEvent
    | LlmRequestEvent
    | ToolCallEvent
    | ToolResultEvent
    | TtsStartEvent
    | TtsEndEvent
    | TtsCancelEvent
    | TimerFiredEvent
    | ErrorEvent
)
```

- [ ] **Step 2: Mirror them in the frontend contract**

In `frontend/src/ws.ts`, add to the `ServerEvent` union (order irrelevant):

```typescript
  | { type: 'speech_started' }
  | { type: 'llm_request'; input: unknown[]; model: string; iteration: number }
```

- [ ] **Step 3: Write the failing recorder test**

Create `backend/tests/test_events.py`:

```python
"""EventRecorder tests. Pure metadata/seq behavior needs no DB; persistence
and ordering run against Postgres (skip if unreachable, like the notes tests)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import uuid

import pytest
import sqlalchemy as sa

from voice_assistant import db
from voice_assistant.events import EventRecorder
from voice_assistant.models import Event
from voice_assistant.protocol import AssistantDeltaEvent, ReadyEvent


async def test_record_is_noop_when_disabled():
    rec = EventRecorder(uuid.uuid4())
    # start() never called -> disabled -> record must not raise and must not seq.
    rec.record(ReadyEvent(session_id="x"), turn_id=None, trace_id=None, span_id=None)
    assert rec._seq == 0


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
```

- [ ] **Step 4: Add the `pg_db` fixture to conftest**

Append to `backend/tests/conftest.py`:

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def pg_db(monkeypatch):
    """Bind db.async_session_factory to a rolled-back Postgres transaction with
    all tables created. Skips when Postgres is unreachable."""
    from voice_assistant import db as db_module
    from voice_assistant.config import settings
    from voice_assistant.models import Base

    engine = create_async_engine(settings.database_url)
    try:
        conn = await engine.connect()
    except Exception:
        await engine.dispose()
        pytest.skip("Postgres not reachable")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    try:
        yield
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'voice_assistant.events'`.

- [ ] **Step 6: Implement the recorder**

Create `backend/src/voice_assistant/events.py`:

```python
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
from datetime import datetime, timezone

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
            ts=datetime.now(timezone.utc),
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
            _logger.warning("EventRecorder batch write failed (%d events)", len(batch), exc_info=True)

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
                    .values(ended_at=datetime.now(timezone.utc), title=self._title)
                )
                await s.commit()
        except Exception:  # noqa: BLE001
            _logger.warning("EventRecorder could not finalize session row", exc_info=True)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_events.py -v`
Expected: `test_record_is_noop_when_disabled` PASS; `test_records_events_in_seq_order` PASS with Postgres up (SKIP if down — run `make db-up` to exercise it).

- [ ] **Step 8: Emit the LLM input snapshot in the agent loop**

In `backend/src/voice_assistant/agent/agent.py`:
1. Add `LlmRequestEvent` to the protocol import block.
2. Change the loop header from `for _ in range(MAX_TOOL_LOOP_ITERATIONS):` to `for iteration in range(MAX_TOOL_LOOP_ITERATIONS):`.
3. Immediately inside the `with _tracer.start_as_current_span("llm.request") as span:` block, before `stream = await client.responses.create(`, add:

```python
                await emit(
                    LlmRequestEvent(
                        input=list(input_items),
                        model=settings.openai_model,
                        iteration=iteration,
                    )
                )
```

- [ ] **Step 9: Wire the recorder + turn_id into Session**

In `backend/src/voice_assistant/session.py`:

1. Add imports at top:

```python
from opentelemetry import trace

from voice_assistant.events import EventRecorder
from voice_assistant.protocol import SpeechStartedEvent  # add to the existing protocol import group
```

2. In `__init__`, replace `self.session_id = uuid.uuid4().hex` with:

```python
        self._session_uuid = uuid.uuid4()
        self.session_id = self._session_uuid.hex
```

and after the TTS/timer state is set up, add:

```python
        self._current_turn_id: uuid.UUID | None = None
        self._recorder = self._make_event_recorder()
```

3. Replace `emit()` with:

```python
    async def emit(self, event) -> None:
        """The single seam every server→client event flows through: send to the
        WS, then hand to the EventRecorder for append-only persistence."""
        await self.ws.send_json(event.model_dump())
        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else None
        span_id = format(ctx.span_id, "016x") if ctx.is_valid else None
        self._recorder.record(
            event, turn_id=self._current_turn_id, trace_id=trace_id, span_id=span_id
        )
```

4. Add the factory + turn-id helpers (near `_make_tts_provider`):

```python
    def _make_event_recorder(self) -> EventRecorder:
        """Factory seam so tests can inject a fake recorder."""
        return EventRecorder(self._session_uuid)

    def _new_turn_id(self) -> uuid.UUID:
        self._current_turn_id = uuid.uuid4()
        return self._current_turn_id
```

5. In `_handle_text_input`, set a turn id before running the turn:

```python
    async def _handle_text_input(self, event: TextInputEvent) -> None:
        self._new_turn_id()
        self._recorder.note_title(event.text)
        await self._run_turn(event.text)
```

6. In `_commit_stt_turn`, after the `if self._turn_active(): await self._barge_in()` block and before `await self.emit(SttFinalEvent(text=utterance))`, add:

```python
        self._new_turn_id()
        self._recorder.note_title(utterance)
```

7. At the end of `_run_turn`'s success path, right after the `StateEvent(state="listening"/"idle")` emit, add `self._current_turn_id = None`. In `_barge_in`, after the `StateEvent(state="listening")` emit, add `self._current_turn_id = None`.

8. In `_fire_timer`, call `self._new_turn_id()` immediately before `async with self._turn_lock:`, and add `self._current_turn_id = None` in the `finally` (alongside the existing `self._timer_tasks.pop`).

9. In `_consume_stt`, replace the `SpeechStarted` branch body (currently `pass`) with:

```python
                elif isinstance(ev, SpeechStarted):
                    # VAD fires on any noise incl. our own TTS echo during
                    # SPEAKING; emit only when no turn is active so recorded
                    # speech_started marks a genuine user-turn onset. Still a
                    # no-op for barge-in (interim transcripts own that).
                    if not self._turn_active():
                        await self.emit(SpeechStartedEvent())
```

10. In `run()`: add `await self._recorder.start()` as the FIRST line of the method (before `emit(ReadyEvent(...))`), and add `await self._recorder.stop()` as the LAST line of the `finally` block.

- [ ] **Step 10: Write the session-integration test**

Append to `backend/tests/test_events.py`:

```python
from conftest import FakeTTSProvider, FakeWebSocket, make_text_turn
from voice_assistant.session import Session


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
                sa.select(Event).where(Event.session_id == session._session_uuid).order_by(Event.seq)
            )
        ).scalars().all()
    turn_types = [r.type for r in rows if r.turn_id == tid]
    assert "assistant_delta" in turn_types
    assert "llm_request" in turn_types
    assert all(r.turn_id == tid for r in rows if r.type == "assistant_delta")
```

A `speech_started`-onset test is unnecessary here — its emission is covered by the `_consume_stt` guard, and its grouping behavior (t0 association) is tested purely in the frontend `computeTurns` test (Task 5).

- [ ] **Step 11: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all prior tests still green (~100 with Postgres up), plus the new event tests. `test_record_is_noop_when_disabled` passes with or without Postgres.

- [ ] **Step 12: Verify the live-UI reducer still ignores the new events**

Run: `cd frontend && npm run typecheck && npm run test`
Expected: PASS. The reducer's `default` case already swallows `speech_started` / `llm_request`; no state.ts change needed.

- [ ] **Step 13: Lint + commit**

```bash
cd backend && uv run ruff check . --fix
git add backend/src/voice_assistant/events.py backend/src/voice_assistant/protocol.py backend/src/voice_assistant/agent/agent.py backend/src/voice_assistant/session.py backend/tests/conftest.py backend/tests/test_events.py frontend/src/ws.ts
git commit -m "feat(events): append-only EventRecorder wired through emit()"
```

---

### Task 3: Session + event REST endpoints

**Files:**
- Create: `backend/src/voice_assistant/api.py`
- Modify: `backend/src/voice_assistant/main.py`
- Create: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `models.Session`, `models.Event`; `db.async_session_factory`.
- Produces: `api.router` (`APIRouter(prefix="/api")`); `api.SessionSummary`, `api.EventOut` pydantic models; coroutines `api.list_sessions()`, `api.list_events(session_id: uuid.UUID)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api.py`:

```python
"""REST endpoint tests (Phase 6). Seed rows in a rolled-back Postgres tx and
call the endpoint coroutines directly against the monkeypatched factory."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from voice_assistant import db
from voice_assistant.api import list_events, list_sessions
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow


async def _seed(sid: uuid.UUID):
    async with db.async_session_factory() as s:
        s.add(SessionRow(id=sid, title="hello"))
        s.add_all(
            [
                Event(
                    session_id=sid, seq=1, ts=datetime.now(timezone.utc), turn_id=None,
                    trace_id=None, span_id=None, type="state",
                    payload={"type": "state", "state": "thinking"},
                ),
                Event(
                    session_id=sid, seq=0, ts=datetime.now(timezone.utc), turn_id=None,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'voice_assistant.api'`.

- [ ] **Step 3: Implement the router**

Create `backend/src/voice_assistant/api.py`:

```python
"""Read-only REST surface for the Timeline/Replay UI (Phase 6)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from voice_assistant import db
from voice_assistant.models import Event
from voice_assistant.models import Session as SessionRow

router = APIRouter(prefix="/api")


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
    async with db.async_session_factory() as s:
        rows = (
            await s.execute(sa.select(SessionRow).order_by(SessionRow.started_at.desc()))
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
    return rows
```

- [ ] **Step 4: Mount the router**

In `backend/src/voice_assistant/main.py`, after the `healthz` route and BEFORE the static mount block, add:

```python
from voice_assistant.api import router as api_router

app.include_router(api_router)
```

(Place the import with the other top-level imports; the `include_router` call must run before the `app.mount("/", ...)` catch-all.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS with Postgres up (SKIP if down).

- [ ] **Step 6: Smoke the live endpoints**

Run: `cd backend && make db-up >/dev/null 2>&1; uv run alembic upgrade head && uv run python -c "import httpx, asyncio; from fastapi.testclient import TestClient; from voice_assistant.main import app; c=TestClient(app); print(c.get('/api/sessions').status_code, c.get('/api/sessions').json())"`
Expected: `200 []` (empty list on a fresh DB — proves the route is mounted and serializes).

- [ ] **Step 7: Lint + commit**

```bash
cd backend && uv run ruff check . --fix
git add backend/src/voice_assistant/api.py backend/src/voice_assistant/main.py backend/tests/test_api.py
git commit -m "feat(api): session + event REST endpoints"
```

---

### Task 4: Frontend routing + sessions list

**Files:**
- Modify: `frontend/package.json` (add `react-router-dom`)
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/routes/LiveAssistant.tsx`
- Create: `frontend/src/routes/SessionsList.tsx`
- Create: `frontend/src/routes/SessionDetail.tsx` (skeleton; filled in Tasks 5–6)
- Create: `frontend/src/api.ts`
- Modify: `frontend/src/index.css` (nav + list styles)

**Interfaces:**
- Consumes: `GET /api/sessions`, `GET /api/sessions/:id/events`.
- Produces:
  - `api.ts`: `SessionSummary` (`{id, started_at, ended_at, title}`), `RecordedEvent` (`{seq, ts, turn_id, trace_id, span_id, type, payload}`), `fetchSessions(): Promise<SessionSummary[]>`, `fetchEvents(id): Promise<RecordedEvent[]>`.
  - `routes/LiveAssistant.tsx`: the former `App` body (default export `LiveAssistant`).
  - `routes/SessionDetail.tsx`: exports `SessionDetail`, fetches events into state (panels wired in later tasks).

- [ ] **Step 1: Install react-router-dom**

Run: `cd frontend && npm install react-router-dom`
Expected: `package.json` gains `"react-router-dom"` under dependencies; lockfile updates.

- [ ] **Step 2: Create the API client**

Create `frontend/src/api.ts`:

```typescript
import type { ServerEvent } from './ws'

export interface SessionSummary {
  id: string
  started_at: string
  ended_at: string | null
  title: string | null
}

export interface RecordedEvent {
  seq: number
  ts: string
  turn_id: string | null
  trace_id: string | null
  span_id: string | null
  type: string
  payload: ServerEvent
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const res = await fetch('/api/sessions')
  if (!res.ok) throw new Error(`GET /api/sessions failed: ${res.status}`)
  return res.json()
}

export async function fetchEvents(id: string): Promise<RecordedEvent[]> {
  const res = await fetch(`/api/sessions/${id}/events`)
  if (!res.ok) throw new Error(`GET /api/sessions/${id}/events failed: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 3: Extract the live UI into a route**

Create `frontend/src/routes/LiveAssistant.tsx` with the ENTIRE current body of `App.tsx` (imports, the `App` function's contents), renamed to `LiveAssistant`, with two changes: import paths go up one level (`../audio/player`, `../components/...`, `../state`, `../ws`), and a nav link is added inside `<header className="app-header">` after the `<h1>`:

```tsx
import { useEffect, useReducer, useState } from 'react'
import { Link } from 'react-router-dom'
import { AudioPlayer } from '../audio/player'
import { MicButton } from '../components/MicButton'
import { StatusBadge } from '../components/StatusBadge'
import { Transcript } from '../components/Transcript'
import { initialState, reducer } from '../state'
import { VoiceAssistantClient, type ConnectionStatus } from '../ws'

export function LiveAssistant() {
  // ... exact body of the old App() function ...
}
```

In the returned JSX header, add the link:

```tsx
      <header className="app-header">
        <h1>Voice Assistant</h1>
        <StatusBadge connectionStatus={connectionStatus} pipelineState={state.status} />
        <Link className="nav-link" to="/sessions">Sessions</Link>
      </header>
```

(Copy the rest of the old body verbatim — `useEffect`, `handleSubmit`, derived vars, and the full JSX.)

- [ ] **Step 4: Create the sessions list route**

Create `frontend/src/routes/SessionsList.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchSessions, type SessionSummary } from '../api'

export function SessionsList() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSessions().then(setSessions).catch((e) => setError(String(e)))
  }, [])

  return (
    <main>
      <header className="app-header">
        <h1>Sessions</h1>
        <Link className="nav-link" to="/">Live</Link>
      </header>
      {error && <p className="error-banner">{error}</p>}
      <ul className="session-list">
        {sessions.map((s) => (
          <li key={s.id}>
            <Link to={`/sessions/${s.id}`}>
              {s.title ?? '(untitled session)'}
              <span className="session-date"> · {new Date(s.started_at).toLocaleString()}</span>
            </Link>
          </li>
        ))}
        {sessions.length === 0 && !error && <li className="muted">No sessions recorded yet.</li>}
      </ul>
    </main>
  )
}
```

- [ ] **Step 5: Create the session detail skeleton**

Create `frontend/src/routes/SessionDetail.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvents, type RecordedEvent } from '../api'

export function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const [events, setEvents] = useState<RecordedEvent[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetchEvents(id).then(setEvents).catch((e) => setError(String(e)))
  }, [id])

  return (
    <main>
      <header className="app-header">
        <h1>Session</h1>
        <Link className="nav-link" to="/sessions">All sessions</Link>
      </header>
      {error && <p className="error-banner">{error}</p>}
      <p className="muted">{events.length} events</p>
      {/* Timeline (Task 5), Replay + Inspector (Task 6) mount here. */}
    </main>
  )
}
```

- [ ] **Step 6: Convert App into the router shell**

Replace `frontend/src/App.tsx` entirely:

```tsx
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { LiveAssistant } from './routes/LiveAssistant'
import { SessionDetail } from './routes/SessionDetail'
import { SessionsList } from './routes/SessionsList'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LiveAssistant />} />
        <Route path="/sessions" element={<SessionsList />} />
        <Route path="/sessions/:id" element={<SessionDetail />} />
      </Routes>
    </BrowserRouter>
  )
}
```

- [ ] **Step 7: Add nav/list styles**

Append to `frontend/src/index.css`:

```css
.nav-link {
  margin-left: auto;
  font-size: 0.9rem;
  color: var(--accent, #4f8cff);
  text-decoration: none;
}
.session-list {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.session-list a {
  text-decoration: none;
  color: inherit;
}
.session-date,
.muted {
  opacity: 0.6;
  font-size: 0.85rem;
}
```

- [ ] **Step 8: Typecheck, build, lint**

Run: `cd frontend && npm run typecheck && npm run build && npx oxlint`
Expected: all clean; the build emits the app + `pcm-worklet` chunk.

- [ ] **Step 9: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/App.tsx frontend/src/api.ts frontend/src/routes frontend/src/index.css
git commit -m "feat(frontend): routing + sessions list"
```

---

### Task 5: Per-turn latency Timeline (absolute axis)

**Files:**
- Create: `frontend/src/timeline.ts` (pure)
- Create: `frontend/src/timeline.test.ts`
- Create: `frontend/src/components/Timeline.tsx`
- Modify: `frontend/src/routes/SessionDetail.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `RecordedEvent` (`api.ts`).
- Produces:
  - `timeline.ts`: `SegmentKind = 'listening' | 'thinking' | 'tool' | 'speaking'`; `TurnSegment { kind; start; end }` (ms from session start); `TurnRow { turnId; t0; tEnd; cancelled; utterance; segments }`; `Timeline { turns: TurnRow[]; start: number; end: number }`; `computeTurns(events: RecordedEvent[]): Timeline`.
  - `components/Timeline.tsx`: `Timeline` component taking `{ events: RecordedEvent[] }`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/timeline.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { computeTurns } from './timeline'
import type { RecordedEvent } from './api'

function ev(seq: number, tsMs: number, type: string, turn: string | null, payload: object = {}): RecordedEvent {
  return {
    seq,
    ts: new Date(tsMs).toISOString(),
    turn_id: turn,
    trace_id: null,
    span_id: null,
    type,
    payload: { type, ...payload } as never,
  }
}

const T = 'turn-1'

describe('computeTurns', () => {
  it('builds one turn with speech_started as t0 and phase segments', () => {
    const events: RecordedEvent[] = [
      ev(0, 1000, 'speech_started', null),
      ev(1, 2000, 'stt_final', T, { text: 'hi' }),
      ev(2, 2000, 'state', T, { state: 'thinking' }),
      ev(3, 2600, 'assistant_delta', T, { text: 'hello' }),
      ev(4, 2700, 'tts_start', T, { sentence_index: 0, text: 'hello' }),
      ev(5, 3500, 'tts_end', T),
    ]
    const tl = computeTurns(events)
    expect(tl.turns).toHaveLength(1)
    const turn = tl.turns[0]
    expect(turn.t0).toBe(0) // speech_started at 1000ms == session start
    expect(turn.cancelled).toBe(false)
    expect(turn.utterance).toBe('hi')
    const kinds = turn.segments.map((s) => s.kind)
    expect(kinds).toContain('listening')
    expect(kinds).toContain('thinking')
    expect(kinds).toContain('speaking')
    const speaking = turn.segments.find((s) => s.kind === 'speaking')!
    expect(speaking.end).toBe(2500) // 3500ms - 1000ms session start
  })

  it('marks a barge-in turn cancelled when it ends in tts_cancel', () => {
    const events: RecordedEvent[] = [
      ev(0, 0, 'stt_final', T, { text: 'go' }),
      ev(1, 100, 'tts_start', T, { sentence_index: 0, text: 'a long' }),
      ev(2, 900, 'tts_cancel', T),
    ]
    const tl = computeTurns(events)
    expect(tl.turns[0].cancelled).toBe(true)
    expect(tl.turns[0].segments.find((s) => s.kind === 'speaking')?.end).toBe(900)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/timeline.test.ts`
Expected: FAIL — cannot resolve `./timeline`.

- [ ] **Step 3: Implement computeTurns**

Create `frontend/src/timeline.ts`:

```typescript
import type { RecordedEvent } from './api'

export type SegmentKind = 'listening' | 'thinking' | 'tool' | 'speaking'

export interface TurnSegment {
  kind: SegmentKind
  start: number
  end: number
}

export interface TurnRow {
  turnId: string
  t0: number
  tEnd: number
  cancelled: boolean
  utterance: string | null
  segments: TurnSegment[]
}

export interface Timeline {
  turns: TurnRow[]
  start: number
  end: number
}

function ms(e: RecordedEvent): number {
  return Date.parse(e.ts)
}

export function computeTurns(events: RecordedEvent[]): Timeline {
  if (events.length === 0) return { turns: [], start: 0, end: 0 }
  const start = ms(events[0])
  const rel = (e: RecordedEvent) => ms(e) - start
  const end = rel(events[events.length - 1])

  const speechStarts = events.filter((e) => e.type === 'speech_started')

  const byTurn = new Map<string, RecordedEvent[]>()
  for (const e of events) {
    if (e.turn_id == null) continue
    const list = byTurn.get(e.turn_id) ?? []
    list.push(e)
    byTurn.set(e.turn_id, list)
  }

  const turns: TurnRow[] = []
  for (const [turnId, group] of byTurn) {
    const first = group[0]
    const sttFinal = group.find((e) => e.type === 'stt_final')
    const firstTts = group.find((e) => e.type === 'tts_start')
    const ended = group.find((e) => e.type === 'tts_end' || e.type === 'tts_cancel')
    const cancelled = group.some((e) => e.type === 'tts_cancel')

    // t0: latest speech_started strictly before this turn's stt_final (or first
    // event); else fall back to the turn's own first event.
    const anchor = sttFinal ?? first
    const precedingOnset = speechStarts
      .filter((s) => ms(s) <= ms(anchor))
      .sort((a, b) => ms(b) - ms(a))[0]
    const t0 = rel(precedingOnset ?? first)

    const thinkingStart = rel(sttFinal ?? first)
    const segments: TurnSegment[] = []
    if (sttFinal && precedingOnset) {
      segments.push({ kind: 'listening', start: t0, end: rel(sttFinal) })
    }
    if (firstTts) {
      segments.push({ kind: 'thinking', start: thinkingStart, end: rel(firstTts) })
      const speakEnd = ended ? rel(ended) : rel(group[group.length - 1])
      segments.push({ kind: 'speaking', start: rel(firstTts), end: speakEnd })
    } else {
      const thinkEnd = ended ? rel(ended) : rel(group[group.length - 1])
      segments.push({ kind: 'thinking', start: thinkingStart, end: thinkEnd })
    }

    // tool sub-spans, matched by call_id, layered over the thinking window.
    const calls = new Map<string, number>()
    for (const e of group) {
      if (e.type === 'tool_call') calls.set((e.payload as { call_id: string }).call_id, rel(e))
      else if (e.type === 'tool_result') {
        const cid = (e.payload as { call_id: string }).call_id
        const cs = calls.get(cid)
        if (cs != null) segments.push({ kind: 'tool', start: cs, end: rel(e) })
      }
    }

    const tEnd = ended ? rel(ended) : rel(group[group.length - 1])
    const utterance =
      (sttFinal?.payload as { text?: string } | undefined)?.text ?? null
    turns.push({ turnId, t0, tEnd, cancelled, utterance, segments })
  }

  turns.sort((a, b) => a.t0 - b.t0)
  return { turns, start, end }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/timeline.test.ts`
Expected: PASS.

- [ ] **Step 5: Build the Timeline component**

Create `frontend/src/components/Timeline.tsx`:

```tsx
import { computeTurns, type SegmentKind } from '../timeline'
import type { RecordedEvent } from '../api'

const COLORS: Record<SegmentKind, string> = {
  listening: '#378add',
  thinking: '#ef9f27',
  tool: '#7f77dd',
  speaking: '#639922',
}

export function Timeline({ events }: { events: RecordedEvent[] }) {
  const tl = computeTurns(events)
  if (tl.turns.length === 0) return <p className="muted">No turns to plot.</p>
  const span = Math.max(tl.end, 1)
  const pct = (v: number) => `${(v / span) * 100}%`

  return (
    <div className="timeline">
      <div className="timeline-legend">
        <span style={{ color: COLORS.listening }}>■ listening</span>
        <span style={{ color: COLORS.thinking }}>■ thinking</span>
        <span style={{ color: COLORS.tool }}>■ tool</span>
        <span style={{ color: COLORS.speaking }}>■ speaking</span>
        <span style={{ color: '#a32d2d' }}>✕ cancelled</span>
      </div>
      {tl.turns.map((turn, i) => (
        <div key={turn.turnId} className="timeline-row">
          <div className="timeline-label" title={turn.utterance ?? ''}>
            Turn {i + 1}
          </div>
          <div className="timeline-track">
            {turn.segments.map((s, j) => (
              <div
                key={j}
                className={`timeline-seg ${s.kind === 'tool' ? 'is-tool' : ''}`}
                style={{
                  left: pct(s.start),
                  width: pct(Math.max(s.end - s.start, 0)),
                  background: COLORS[s.kind],
                }}
                title={`${s.kind} ${(s.end - s.start).toFixed(0)}ms`}
              />
            ))}
            {turn.cancelled && (
              <div className="timeline-cancel" style={{ left: pct(turn.tEnd) }}>
                ✕
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 6: Mount it in SessionDetail + add styles**

In `frontend/src/routes/SessionDetail.tsx`, import `Timeline` and replace the `{/* Timeline ... */}` comment with `{events.length > 0 && <Timeline events={events} />}`.

Append to `frontend/src/index.css`:

```css
.timeline { display: flex; flex-direction: column; gap: 0.35rem; margin: 1rem 0; }
.timeline-legend { display: flex; gap: 1rem; font-size: 0.8rem; margin-bottom: 0.3rem; }
.timeline-row { display: flex; align-items: center; gap: 0.5rem; }
.timeline-label { width: 5rem; font-size: 0.8rem; opacity: 0.8; }
.timeline-track { position: relative; flex: 1; height: 20px; background: rgba(127,127,127,0.12); border-radius: 4px; }
.timeline-seg { position: absolute; top: 0; height: 20px; border-radius: 3px; }
.timeline-seg.is-tool { top: 5px; height: 10px; opacity: 0.9; }
.timeline-cancel { position: absolute; top: 0; color: #a32d2d; font-weight: 500; transform: translateX(-50%); }
```

- [ ] **Step 7: Typecheck + test + build**

Run: `cd frontend && npm run typecheck && npx vitest run && npm run build`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/timeline.ts frontend/src/timeline.test.ts frontend/src/components/Timeline.tsx frontend/src/routes/SessionDetail.tsx frontend/src/index.css
git commit -m "feat(frontend): per-turn latency timeline"
```

---

### Task 6: Replay engine + Event Inspector

**Files:**
- Create: `frontend/src/replay.ts` (pure fold)
- Create: `frontend/src/replay.test.ts`
- Create: `frontend/src/components/Replay.tsx`
- Create: `frontend/src/components/EventInspector.tsx`
- Modify: `frontend/src/routes/SessionDetail.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `reducer`, `initialState`, `AppState` (`state.ts`); `RecordedEvent` (`api.ts`); `Transcript` (`components/Transcript.tsx`).
- Produces:
  - `replay.ts`: `foldTo(events: RecordedEvent[], count: number): AppState` — reduce the first `count` payloads through the live reducer from `initialState`.
  - `components/Replay.tsx`: `Replay` component (`{ events }`) with play/pause, 1×/2×/4× speed, scrubber, rendering `Transcript` from the folded state; calls an optional `onCursor` callback.
  - `components/EventInspector.tsx`: `EventInspector` (`{ events, selected, onSelect }`).

- [ ] **Step 1: Write the failing fold test**

Create `frontend/src/replay.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { foldTo } from './replay'
import type { RecordedEvent } from './api'

function ev(seq: number, payload: object): RecordedEvent {
  return {
    seq,
    ts: new Date(seq * 100).toISOString(),
    turn_id: null,
    trace_id: null,
    span_id: null,
    type: (payload as { type: string }).type,
    payload: payload as never,
  }
}

const LOG: RecordedEvent[] = [
  ev(0, { type: 'ready', session_id: 's1' }),
  ev(1, { type: 'stt_final', text: 'hi' }),
  ev(2, { type: 'assistant_delta', text: 'he' }),
  ev(3, { type: 'assistant_delta', text: 'llo' }),
  ev(4, { type: 'assistant_done', text: 'hello' }),
  ev(5, { type: 'state', state: 'idle' }),
]

describe('foldTo', () => {
  it('reproduces the live end-state when folding the whole log', () => {
    const final = foldTo(LOG, LOG.length)
    expect(final.sessionId).toBe('s1')
    expect(final.status).toBe('idle')
    expect(final.messages).toEqual([
      { role: 'user', text: 'hi' },
      { role: 'assistant', text: 'hello' },
    ])
  })

  it('scrubbing matches an incremental fold at every cursor', () => {
    for (let k = 0; k <= LOG.length; k++) {
      const viaFold = foldTo(LOG, k)
      const incremental = foldTo(LOG.slice(0, k), k)
      expect(viaFold).toEqual(incremental)
    }
  })

  it('mid-stream cursor shows the partial assistant message', () => {
    const mid = foldTo(LOG, 3) // ready, stt_final, one delta
    expect(mid.messages[1]).toEqual({ role: 'assistant', text: 'he' })
    expect(mid.assistantInProgress).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/replay.test.ts`
Expected: FAIL — cannot resolve `./replay`.

- [ ] **Step 3: Implement the fold**

Create `frontend/src/replay.ts`:

```typescript
import { initialState, reducer, type AppState } from './state'
import type { RecordedEvent } from './api'

/** Fold the first `count` recorded payloads through the live reducer. Pure —
 * the same reducer that drives the live UI, which is what makes replay
 * deterministic. Unknown event types (speech_started, llm_request, tts_*) fall
 * through the reducer's default case unchanged. */
export function foldTo(events: RecordedEvent[], count: number): AppState {
  let state = initialState
  for (let i = 0; i < count && i < events.length; i++) {
    state = reducer(state, events[i].payload as never)
  }
  return state
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/replay.test.ts`
Expected: PASS.

- [ ] **Step 5: Build the Replay component**

Create `frontend/src/components/Replay.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { foldTo } from '../replay'
import { Transcript } from './Transcript'
import type { RecordedEvent } from '../api'

const SPEEDS = [1, 2, 4]

export function Replay({
  events,
  cursor,
  onCursor,
}: {
  events: RecordedEvent[]
  cursor: number
  onCursor: (n: number) => void
}) {
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (!playing) return
    if (cursor >= events.length) {
      setPlaying(false)
      return
    }
    const cur = events[cursor]
    const next = events[cursor + 1]
    const gap = next ? Math.max(Date.parse(next.ts) - Date.parse(cur.ts), 0) : 0
    timer.current = window.setTimeout(() => onCursor(cursor + 1), gap / speed)
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [playing, cursor, speed, events, onCursor])

  const state = foldTo(events, cursor)

  return (
    <div className="replay">
      <div className="replay-controls">
        <button onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : 'Play'}</button>
        <button onClick={() => { setPlaying(false); onCursor(0) }}>Reset</button>
        <input
          type="range"
          min={0}
          max={events.length}
          value={cursor}
          onChange={(e) => { setPlaying(false); onCursor(Number(e.target.value)) }}
        />
        <span className="muted">{cursor}/{events.length}</span>
        {SPEEDS.map((s) => (
          <button key={s} className={s === speed ? 'active' : ''} onClick={() => setSpeed(s)}>
            {s}×
          </button>
        ))}
      </div>
      <Transcript
        messages={state.messages}
        isThinking={state.status === 'thinking' && !state.assistantInProgress}
        toolActivity={state.toolActivity}
        sttPartial={state.sttPartial}
      />
    </div>
  )
}
```

- [ ] **Step 6: Build the Event Inspector**

Create `frontend/src/components/EventInspector.tsx`:

```tsx
import type { RecordedEvent } from '../api'

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
  return (
    <div className="inspector">
      <ol className="inspector-list">
        {events.map((e, i) => (
          <li key={i} className={i === selected ? 'active' : ''}>
            <button onClick={() => onSelect(i)}>
              <span className="muted">{e.seq}</span> {e.type}
            </button>
          </li>
        ))}
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

- [ ] **Step 7: Wire both panels into SessionDetail**

Replace `frontend/src/routes/SessionDetail.tsx` body to own the shared cursor and lay out the three panels:

```tsx
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEvents, type RecordedEvent } from '../api'
import { Timeline } from '../components/Timeline'
import { Replay } from '../components/Replay'
import { EventInspector } from '../components/EventInspector'

export function SessionDetail() {
  const { id } = useParams<{ id: string }>()
  const [events, setEvents] = useState<RecordedEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [cursor, setCursor] = useState(0)

  useEffect(() => {
    if (!id) return
    fetchEvents(id).then(setEvents).catch((e) => setError(String(e)))
  }, [id])

  return (
    <main>
      <header className="app-header">
        <h1>Session</h1>
        <Link className="nav-link" to="/sessions">All sessions</Link>
      </header>
      {error && <p className="error-banner">{error}</p>}
      {events.length > 0 && (
        <>
          <Timeline events={events} />
          <div className="session-panels">
            <Replay events={events} cursor={cursor} onCursor={setCursor} />
            <EventInspector
              events={events}
              selected={Math.min(cursor, events.length - 1)}
              onSelect={setCursor}
            />
          </div>
        </>
      )}
    </main>
  )
}
```

- [ ] **Step 8: Add panel styles**

Append to `frontend/src/index.css`:

```css
.session-panels { display: flex; gap: 1rem; align-items: flex-start; }
.replay { flex: 2; min-width: 0; }
.replay-controls { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
.replay-controls input[type='range'] { flex: 1; }
.replay-controls button.active { font-weight: 500; text-decoration: underline; }
.inspector { flex: 1; min-width: 0; max-height: 420px; overflow: auto; border: 1px solid rgba(127,127,127,0.25); border-radius: 8px; }
.inspector-list { list-style: none; margin: 0; padding: 0.25rem; max-height: 160px; overflow: auto; }
.inspector-list button { width: 100%; text-align: left; background: none; border: none; cursor: pointer; font-size: 0.8rem; color: inherit; padding: 0.15rem 0.3rem; }
.inspector-list li.active button { background: rgba(127,127,127,0.18); border-radius: 4px; }
.inspector-payload { margin: 0; padding: 0.5rem; font-size: 0.75rem; white-space: pre-wrap; word-break: break-word; }
```

- [ ] **Step 9: Full frontend gate**

Run: `cd frontend && npm run typecheck && npx vitest run && npm run build && npx oxlint`
Expected: all clean; determinism, timeline, and replay tests green.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/replay.ts frontend/src/replay.test.ts frontend/src/components/Replay.tsx frontend/src/components/EventInspector.tsx frontend/src/routes/SessionDetail.tsx frontend/src/index.css
git commit -m "feat(frontend): replay engine + event inspector"
```

---

### Task 7: End-to-end verification, docs, and release

**Files:**
- Modify: `HANDOFF.md`
- Modify: `README.md`

**Interfaces:** none (docs + manual verification).

- [ ] **Step 1: Full backend + frontend suites green**

Run: `cd backend && make db-up >/dev/null 2>&1 && uv run alembic upgrade head && uv run pytest -q && uv run ruff check .`
Then: `cd frontend && npm run test && npm run typecheck && npm run build && npx oxlint`
Expected: backend all-passed (event/api/migration tests exercised against Postgres), frontend all green.

- [ ] **Step 2: Live end-to-end record → replay**

Start `make dev-backend` + `make dev-frontend` (Postgres up). In the browser: have a short conversation (type or speak, trigger one tool, e.g. weather), then reload and open `/sessions`. Confirm: the session appears with its first-utterance title; opening it shows the Timeline (one row per turn, tool sub-span visible), the Replay transcript reproduces the conversation when played, and the Inspector shows an `llm_request` payload with the exact `input` snapshot. Capture the Jaeger waterfall + a demo GIF here (the Phase 5 deferral) if desired — optional, not blocking.

- [ ] **Step 3: Update HANDOFF.md**

Add a new top section `## Status: Phase 6 done ✅ (Event Timeline + Replay)` summarizing: the `0002` migration + `sessions`/`events` tables; the async-batched `EventRecorder` wired through `emit()` with `turn_id`/trace/span metadata; the `speech_started` + `llm_request` events; the REST endpoints; the routed frontend with Timeline/Replay/Inspector; and the test counts. Note the `web_search` tool + Jaeger screenshot/GIF remain the only deferrals. Update the "Full phase plan" checklist item 7 to ✅.

- [ ] **Step 4: Update README.md**

Add a "Timeline & Replay" subsection describing the event-sourced log, the `/sessions/:id` view, and the reducer-reuse (same pure reducer drives live UI and replay). Reference the design/plan docs. Drop in the Jaeger screenshot/GIF if captured in Step 2.

- [ ] **Step 5: Commit docs**

```bash
git add HANDOFF.md README.md
git commit -m "docs: Phase 6 timeline + replay — handoff + README"
```

- [ ] **Step 6: Tag (only after explicit user confirmation)**

Do NOT tag automatically. Ask the user to confirm Phase 6 is complete and they want the release tag, then:

```bash
git tag v1.0.0
```

---

## Notes for the implementer

- **Barge-in turn_id ordering** (Task 2, step 9.6/9.7): in `_commit_stt_turn`, `_barge_in()` runs BEFORE `_new_turn_id()`, so the `tts_cancel`/`listening` emitted during barge-in carry the OLD (cancelled) turn's id — correct grouping. Don't reorder those calls.
- **`db` import discipline**: `events.py` and `api.py` both use `from voice_assistant import db` + `db.async_session_factory`. Importing the name directly would break the `pg_db` monkeypatch and silently write to the real DB in tests.
- **Reducer is frozen**: Task 6 must not edit `state.ts`. If a recorded event type needs handling, it already flows through the reducer's `default` case unchanged — that is the invariant Replay depends on.
