# Phase 6 — Event Timeline + Replay: Design

Status: approved-pending-review · Date: 2026-07-12

Phase 6 is the portfolio finale: an append-only event log of every session,
persisted through the existing `emit()` seam, exposed over REST, and surfaced
in a `/sessions/:id` view with three panels — a per-turn latency **Timeline**, a
**Replay** that re-drives the exact same reducer as the live UI, and an **Event
Inspector**. It ends with tag `v1.0.0`.

The architecture was laid for this from Phase 1: `emit()` is already the single
server→client path (with a Phase 6 recorder hook reserved in its docstring), and
`state.ts`'s `reducer` is already a pure `(state, action) => state`. Replay is
therefore an architectural freebie — feed recorded events back through that same
reducer.

## Scope decisions (confirmed with user)

- **In scope**: the core — `0002` migration, `EventRecorder` persistence, REST
  endpoints, Timeline, Replay, Event Inspector, `react-router-dom`, a new
  `SpeechStartedEvent`.
- **Deferred (unchanged)**: the `web_search` built-in tool, and the Jaeger
  waterfall screenshot + demo GIF (both need a live browser+mic run that can't
  happen headlessly). They stay out of this pass.
- **Recording fidelity**: **full fidelity, async-batched** — record every
  emitted event, including each `assistant_delta` token, so Replay reproduces
  token-by-token streaming. Writes go off the hot path.
- **Capture seam**: **single seam via `emit()`** — the one new internal datum
  (the LLM input snapshot) becomes a real protocol event that flows through
  `emit()`; the live reducer ignores it. No second recording path.
- **Event metadata**: a dedicated `EventMetadata` (`session_id`, `seq`, `ts`,
  `turn_id`, `trace_id`, `span_id`) + `payload`, not a bag of loose columns.
- **Timeline axis**: **absolute shared time axis** (wall-clock from session
  start), so inter-turn gaps and barge-in overlap are visible.

## Data model — `models.py` + migration `0002_sessions_events`

Two new tables alongside the existing `notes`.

**`Session`**
- `id UUID PK`
- `started_at TIMESTAMPTZ NOT NULL` (server default `now()`)
- `ended_at TIMESTAMPTZ NULL` (set on clean teardown)
- `title TEXT NULL` — the first user utterance of the session, set when the
  first turn commits, so `GET /api/sessions` needs no join.

**`Event`** — flat columns for everything queried/ordered on, JSONB for the
event body:

| column | type | purpose |
|---|---|---|
| `id` | `UUID PK` | |
| `session_id` | `UUID FK→sessions.id` (indexed) | |
| `seq` | `INT` | monotonic per session; `UNIQUE(session_id, seq)`. Assigned synchronously at record time, so it is the authoritative order even if batched writes land out of order. |
| `ts` | `TIMESTAMPTZ` | server wall-clock captured at `emit()`. Drives Timeline geometry and Replay pacing. |
| `turn_id` | `UUID NULL` | groups all events of one user turn. Null for events outside a turn (`ready`, a pre-commit `speech_started`, inter-turn `state` transitions). |
| `trace_id` | `VARCHAR NULL` | OTel trace id (hex) of the active span at emit, or null. |
| `span_id` | `VARCHAR NULL` | OTel span id (hex) of the active span at emit, or null. |
| `type` | `VARCHAR` | denormalized event type for filtering. |
| `payload` | `JSONB` | full `event.model_dump()` (includes `type`) — fed directly to the reducer on Replay. |

App-layer shapes (in `events.py`):
- `EventMetadata` dataclass: `session_id, seq, ts, turn_id, trace_id, span_id`.
- `RecordedEvent`: `metadata: EventMetadata` + `payload: dict`.

The migration follows `0001`'s hand-written style (revision `"0002"`,
`down_revision "0001"`), `postgresql.UUID(as_uuid=True)` + `postgresql.JSONB`,
the unique constraint and the `session_id` index. `downgrade()` drops `events`
then `sessions`.

## EventRecorder — `events.py`

One recorder per `Session`. Full-fidelity, async-batched, best-effort.

**`record(event, *, turn_id, trace_id, span_id)` (hot path).** Called by
`Session.emit()` after `ws.send_json`. `emit()` supplies the per-event context
(`turn_id` from `self._current_turn_id`; `trace_id`/`span_id` from
`trace.get_current_span().get_span_context()`, null when there is no valid span)
so the recorder stays decoupled from `Session` internals. `record()` then:
1. assigns recorder-owned metadata — `seq = self._seq; self._seq += 1`,
   `ts = now`,
2. does a **non-blocking** `queue.put_nowait(RecordedEvent(...))` onto an
   in-memory `asyncio.Queue`.

No DB I/O here. `seq` is assigned in emit order regardless of when the row is
actually written. A no-op when `not self._enabled`.

**Background flush task.** Started by `start()`. Loop: `await queue.get()` for
the first item, then drain the rest currently buffered with `get_nowait()` into
one batch, `INSERT` the batch in a single statement via `async_session_factory`,
commit, repeat. On any DB exception: log a warning, keep looping (a Postgres
hiccup must never break the live conversation). A sentinel enqueued by `stop()`
ends the loop after a final drain.

**Lifecycle.**
- `start()` — insert the `Session` row (title still null, so the FK exists
  before any `Event`), then create the flush task. If the DB is unreachable
  here, log a warning, set `self._enabled = False`, and return; `record()`
  becomes a no-op.
- `note_title(text)` — records the first user utterance in memory (first call
  wins). Not written yet — it is persisted once in `stop()`.
- `stop()` — enqueue the stop sentinel, await the flush task (final drain), then
  a single `UPDATE sessions` sets `ended_at = now` and `title` (the noted first
  utterance, if any), dispose its DB resources. Called in `Session.run()`'s
  `finally`.

**Graceful degradation + test seam.** `record()` is a no-op when
`not self._enabled`. `Session` gets a `_make_event_recorder()` factory (mirrors
`_make_stt_provider`) so unit tests inject a null/fake recorder; the real one
self-disables when Postgres is down. This keeps the existing ~100 non-DB tests
green without them ever touching the database.

## Session integration — `session.py`

- **`emit()`** gains one call after `ws.send_json` (synchronous, non-blocking):
  `self._recorder.record(event, turn_id=self._current_turn_id,
  trace_id=..., span_id=...)`, reading trace/span from the current OTel span.
- **`_current_turn_id: uuid.UUID | None`** field + `_new_turn_id()` helper that
  assigns a fresh id. Called at each turn-commit point so the whole turn shares
  one id:
  - `_commit_stt_turn` — before emitting `stt_final` (so `stt_final` is part of
    the turn).
  - the text path (`_handle_text_input`) — before `_run_turn`.
  - `_fire_timer` — before its notification turn span (a timer notification is
    its own one-event "turn").
  Cleared to `None` when a turn settles. Safe as a plain field: turns are
  serialized on one event loop by `_turn_lock` + explicit barge-in await, so
  there is never a concurrent writer.
- **`_start_stt` / `run` start**: call `self._recorder.start()` at the top of
  `run()` (right around the `ReadyEvent`), and `self._recorder.stop()` in the
  `finally`.
- **Title**: on the first turn commit of the session, call
  `self._recorder.note_title(utterance)`; the recorder persists it in `stop()`
  (see above). Live sessions therefore list with a null title until they end —
  acceptable, since `/sessions` is browsed after the fact.

## SpeechStarted event

- **`protocol.py`**: new `SpeechStartedEvent` (`type: "speech_started"`, no
  fields — its `ts` comes from recorder metadata). Added to the `ServerEvent`
  union; mirrored in `ws.ts`.
- **`session.py._consume_stt`**: in the existing `SpeechStarted` branch (today a
  `pass`), emit it **only when `not self._turn_active()`**. Deepgram's VAD fires
  `SpeechStarted` on any noise, including the assistant's own TTS echoing back
  during SPEAKING; gating on "no active turn" keeps recorded `speech_started`
  events to genuine user-turn onsets and avoids a flood of phantom markers. It
  remains a no-op for barge-in — interim transcripts still own that path.
- **`state.ts`**: no new case; the reducer's `default` ignores it (it is a
  Timeline/Replay signal, not a live-UI one).
- **Turn grouping**: `speech_started` carries `turn_id = None` (it fires before
  commit generates the id). The Timeline's `computeTurns` associates each turn's
  **t0 = the latest `speech_started` whose `ts` precedes that turn's
  `stt_final`**, falling back to `stt_final` when none precedes (e.g. a
  barge-initiated turn, whose onset fired during the previous active turn and
  was suppressed).

## LLM input snapshot event

- **`protocol.py`**: new `LlmRequestEvent` — `type: "llm_request"`,
  `input: list[dict]` (a copy of the exact `input_items` sent), `model: str`,
  `iteration: int` (which tool-loop pass, 0-based).
- **`agent.py`**: emit it inside the loop immediately before
  `client.responses.create`, snapshotting `input_items` (a shallow copy of the
  list; items are plain JSON dicts). This is the literal payoff of the
  `store=False` design — the exact serializable conversation state per request.
- Flows through `emit()` → recorded + sent to WS. The live reducer ignores it;
  the Event Inspector renders it specially.

## REST API — `api.py` (router mounted in `main.py`)

Both read-only, using the `get_db` dependency. Mounted before the static mount,
same as `/ws`.

- `GET /api/sessions` → `[{id, started_at, ended_at, title}]`, newest first.
- `GET /api/sessions/{id}/events` → `[{seq, ts, turn_id, trace_id, span_id,
  type, payload}]` ordered by `seq`. 404 if the session is unknown. No
  pagination (portfolio-sized sessions); returns the full log that drives all
  three panels.

Pydantic response models mirror the shapes above so the frontend has a stable
contract.

## Frontend — `react-router-dom`

Add `react-router-dom`. `App.tsx` becomes a thin router shell; the current live
UI moves **verbatim** into a `LiveAssistant` route. `state.ts` is not touched —
that invariant is the whole Replay pitch.

**Routes**
- `/` — `LiveAssistant` (today's App body, unchanged behavior) + a nav link to
  Sessions.
- `/sessions` — session list: fetch `/api/sessions`, render links (title +
  started_at) to each detail page.
- `/sessions/:id` — the centerpiece. One fetch of the event log feeds all three
  panels. Layout: Timeline across the top, Replay (transcript + controls) on the
  left, Event Inspector on the right.

**Timeline** (`components/Timeline.tsx` + pure `timeline.ts`)
- A pure, tested `computeTurns(events)` groups events by `turn_id`, resolves each
  turn's t0 (rule above), and derives absolute-time segments: `listening`
  (`speech_started`/t0 → `stt_final`), `thinking` (`stt_final` → first
  `tts_start`, with `tool_call`→`tool_result` sub-spans), `speaking` (first
  `tts_start` → `tts_end`), and a **cancelled** cap where a turn ends in
  `tts_cancel` instead of `tts_end`.
- Rendered as Gantt rows on one absolute axis (wall-clock from the first event).
  A barge-in shows as Turn N ending at ✕ (`tts_cancel`) with Turn N+1 beginning
  at the same instant.

**Replay** (`components/Replay.tsx` + `replay.ts`)
- Folds the recorded `payload`s through the **same `reducer`** from `state.ts`,
  from `initialState`. A scheduler advances a cursor, waiting scaled `ts` deltas
  (1× / 2× / 4×) between events; play/pause + a scrubber. Scrubbing re-folds
  `events[0..cursor]` (pure, cheap) to rebuild state at any point.
- Renders the existing `Transcript` component from the replayed `AppState`. No
  audio replay (binary TTS frames are not recorded).

**Event Inspector** (`components/EventInspector.tsx`)
- Event list (`seq` · `ts` · `type`); selecting one shows the pretty-printed raw
  `payload` (the `llm_request` snapshot, tool args/outputs, state transitions)
  plus `trace_id`/`span_id`. Selecting an event also seeks the Replay cursor to
  it.

Styling: functional and clean, themed with the existing `index.css` variables.

## Testing

**Backend**
- Migration smoke: extend `tests/test_migrations.py` (or add a case) —
  `downgrade base` → `upgrade head` asserts `sessions` and `events` exist. Skips
  without Postgres, like the existing test.
- `tests/test_events.py`: `EventRecorder` seq monotonicity; order preserved
  despite batching; metadata populated (`turn_id` grouping, trace/span when in a
  span); graceful no-op when the DB is down; `Session` row + `title` set. The
  persistence assertions run against test Postgres (skip if unreachable, like
  the notes tests); pure seq/metadata logic is unit-tested without a DB.
- REST tests: seed a session + events, assert `/api/sessions` and
  `/api/sessions/{id}/events` shapes and ordering; 404 path.
- Session integration: a turn through `Session` with the real/fake recorder
  records events with the correct `turn_id` grouping and a `speech_started` only
  when no turn is active.

**Frontend (vitest)**
- Determinism (extends `state.test.ts`): folding a recorded event log through
  `reducer` from `initialState` yields the same end-state the live run produced.
- `computeTurns` fixture test: segment boundaries and the barge-in cancel cap
  from a hand-built event list.
- Replay scrubber: folding `events[0..cursor]` matches the incremental fold.

## Slices (one focused commit each — repo convention)

1. `feat(db)`: Session/Event models + `0002_sessions_events` migration + smoke
   test.
2. `feat(events)`: `EventRecorder` (async-batched, graceful degradation) wired
   through `emit()`; `_current_turn_id`/`_new_turn_id`; `SpeechStartedEvent` +
   `LlmRequestEvent`; trace/span capture. Backend tests.
3. `feat(api)`: session + event REST endpoints + tests.
4. `feat(frontend)`: `react-router-dom`, `LiveAssistant` extraction, `/sessions`
   list.
5. `feat(frontend)`: absolute-axis latency Timeline + `computeTurns` + tests.
6. `feat(frontend)`: Replay engine + Event Inspector + determinism/scrubber
   tests.
7. `docs`: HANDOFF + README replay section. Tag `v1.0.0` is a separate explicit
   step, confirmed with the user — not auto-tagged.

## Out of scope

- `web_search` built-in tool (still deferred).
- Jaeger waterfall screenshot + demo GIF (need a live browser+mic run).
- Timeline zoom/pan, normalized-axis toggle, replay of audio.
