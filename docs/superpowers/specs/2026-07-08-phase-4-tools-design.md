# Phase 4 — Tools: Design

Status: approved 2026-07-08. Implements PLAN.md Phase 5 ("Tools") / README roadmap
item 5. Builds directly on `main` (Phase 3 / `v0.1.0` already merged).

## Context

The agent loop (`agent/agent.py`), protocol (`tool_call`/`tool_result`/`timer_fired`
events), and frontend (reducer + `Transcript.tsx` tool chips) were all built
tool-generic from Phase 1 onward — they work today with an empty tool list. This
phase fills in the one remaining gap: a real tool registry, three concrete tools,
and wiring `session.py`'s `tool_executor` stub to it. `web_search` (OpenAI's
built-in tool) is explicitly deferred to a later phase — out of scope here.

## Goals

- `get_weather(city)`, `set_timer(seconds, label?)`, `save_note(text)`,
  `list_notes()` all callable by the agent and demoable end-to-end.
- A registry pattern (`@tool` decorator) that's trivial to extend later.
- Timers fire a UI event **and** a spoken notification, without an extra LLM
  round-trip.
- Notes persist to the existing Postgres `notes` table via the existing
  `db.py` session factory.
- No changes to the agent loop, protocol shapes, or frontend reducer's
  existing tool_call/tool_result handling — those are already correct.

## Non-goals

- `web_search` (deferred).
- Any change to the barge-in / turn-cancellation machinery.
- A new DB table or migration (notes already has one from Phase 0).

## Design

### Registry (`backend/src/voice_assistant/agent/tools/registry.py`)

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema, strict-mode shaped
    handler: Callable[..., Awaitable[str]]  # async def handler(ctx, **kwargs) -> str
```

- `@tool(name, description, parameters)` decorator registers a `ToolSpec` into
  a module-level dict keyed by name.
- `definitions() -> list[dict]` returns **name-sorted** Responses-flattened
  tool defs: `{"type": "function", "name", "description", "parameters", "strict": True}`.
  Sorting is for prompt-cache-prefix stability (matches the existing
  `registry.definitions() name-sorted` convention already documented in
  PLAN.md).
- `execute(name, arguments_json, ctx) -> str`:
  - Unknown tool name → `f"Error: no such tool {name!r}"` (string, not raised).
  - `json.loads` failure on `arguments_json` → `f"Error: invalid arguments"` (string).
  - Otherwise `await handler(ctx, **kwargs)`. Handler exceptions propagate —
    `agent.py`'s existing `_execute_tool_call` already maps any exception from
    `tool_executor` to an `f"Error: {exc}"` string, so `registry.execute` does
    not need its own blanket try/except for handler-internal errors.
- `ToolContext` (small dataclass): `session: Session`. Handlers reach
  `set_timer` scheduling via `ctx.session.schedule_timer(...)` and reach the DB
  via the module-level `db.async_session_factory` import (not through
  `ctx`) — same posture as importing `settings` directly.

Schemas are authored for OpenAI **strict mode**: every property listed in
`required`, optional fields typed as a nullable union (`["string", "null"]`)
rather than omitted, `additionalProperties: false`.

### Tools

**`agent/tools/weather.py` — `get_weather(city: str)`**
- Open-Meteo geocoding (`https://geocoding-api.open-meteo.com/v1/search?name=...`)
  → forecast (`https://api.open-meteo.com/v1/forecast?...&current=temperature_2m,weathercode`)
  via a module-level `httpx.AsyncClient` (keyless, no auth).
- WMO weather-code → short phrase table (clear/cloudy/rain/snow/etc., collapsed
  to ~8 buckets — not all 27 WMO codes need distinct phrasing for a spoken
  reply).
- No geocoding match → `"I couldn't find a city called {city}."` (a normal
  string return, not an error — the model should be able to relay this
  conversationally).
- Network/HTTP failure → raises (caught by `agent.py`'s existing exception
  mapping, same as any other tool failure).

**`agent/tools/timers.py` — `set_timer(seconds: int, label: str | None)`**
- Validates `seconds > 0` (raise `ValueError` on bad input — becomes an
  `Error: ...` string via the existing exception path).
- Calls `ctx.session.schedule_timer(seconds, label)`, which:
  - creates `asyncio.create_task(self._fire_timer(timer_id, seconds, label))`
  - tracks it in `self._timer_tasks: dict[str, asyncio.Task]`
- Returns immediately: `"Timer set for {duration phrase}."`. Duration phrasing:
  under 60s → `"{n} seconds"`; exact multiple of 60 → `"{n} minutes"`; otherwise
  → `"{m} minutes {s} seconds"` (e.g. 90 → "1 minute 30 seconds", 300 → "5
  minutes"). Singular "minute"/"second" when the count is 1.
- `Session._fire_timer`: `await asyncio.sleep(seconds)`, remove itself from
  `_timer_tasks`, `await self.emit(TimerFiredEvent(timer_id, label))`, then
  **speak a fixed phrase** by reusing the existing turn/TTS machinery under
  `self._turn_lock`:
  - `state: speaking` → `tts_start` → synthesize `f"Your {label} timer is done."`
    (or "Your timer is done." if no label) via `self.tts.synthesize` → binary
    frames → `tts_end` → settle to `listening`/`idle` per `self.stt is not None`.
  - This does **not** go through `run_agent`/the chunker/`input_items` — it's a
    fixed phrase, not a model turn, so it's simpler than `_run_turn` and does
    not append anything to conversation history.
  - Guarded by `_turn_lock` so it can't talk over an in-flight assistant turn;
    if the lock is held, the timer simply waits its turn (no barge-in
    semantics needed for a background notification).
- All outstanding `_timer_tasks` are cancelled in `Session.run()`'s existing
  teardown `finally` block (alongside the current STT/TTS cleanup), so a
  dropped socket leaks nothing.

**`agent/tools/notes.py` — `save_note(text: str)` / `list_notes()`**
- `save_note`: opens `db.async_session_factory()`, inserts a `Note(text=text)`,
  commits, returns `"Saved."` (or similar spoken confirmation).
- `list_notes`: queries all `Note` rows ordered by `created_at`, returns a
  spoken-style joined summary (e.g. "You have 2 notes: buy milk, and call
  mom.") or `"You don't have any notes yet."` when empty.
- Both are plain async functions using the existing `db.async_session_factory`
  — no new engine/session plumbing.

### Session wiring (`session.py`)

- `from voice_assistant.agent.tools import registry, weather, timers, notes  # noqa: F401`
  — the submodule imports are for their `@tool` registration side effects;
  `registry` is what's actually called.
- Module-level `_TOOLS: list[dict] = registry.definitions()` replaces the
  current empty list.
- `Session.tool_executor` replaces its `NotImplementedError` body:
  ```python
  async def tool_executor(self, name, arguments, call_id):
      with _tracer.start_as_current_span("tool.execute") as span:
          span.set_attribute("tool.name", name)
          return await registry.execute(name, arguments, ToolContext(session=self))
  ```
  This span nests under the existing `turn` span (same pattern as
  `llm.request`/`tts.synthesize`).
- New `Session` state: `self._timer_tasks: dict[str, asyncio.Task] = {}`.
- New methods: `schedule_timer(seconds, label) -> str` (returns timer_id),
  `_fire_timer(timer_id, seconds, label)` (as above).
- `run()`'s `finally` gains: cancel every task in `_timer_tasks` (mirrors the
  existing STT-task-cancel pattern).

### Frontend

The reducer/UI already handle `tool_call`/`tool_result` end-to-end — no
changes needed there. The only gap is `timer_fired`, currently swallowed by
`state.ts`'s default case. Add:
- `AppState.notifications: string[]` (or reuse a single `lastNotification:
  string | null` — a list is simpler and matches the append style of
  `toolActivity`).
- A new pure reducer case for `timer_fired` appending a formatted string
  (`"⏱ {label} timer done"` or similar) to `notifications`.
- A small render in `App.tsx`/`Transcript.tsx` — a dismissible-looking banner
  list, styled minimally in `index.css`. This is intentionally small; it's a
  notification list, not a new subsystem.

### Testing

- `test_registry.py`: `@tool` registration populates the dict; `definitions()`
  is name-sorted and strict-shaped; `execute()` on an unknown name and on
  malformed JSON both return `Error: ...` strings without raising; a handler
  that raises propagates the exception (verifying `agent.py` remains the
  single place that maps it to a string).
- `test_tools.py`:
  - Weather: respx-mocked geocoding + forecast responses; a no-match-city
    case; an HTTP-error case (asserts it raises, since `agent.py` owns
    catching it).
  - Notes: a transactional Postgres fixture (open a connection, begin a
    transaction, bind the session factory to it, roll back after each test) —
    `pytest.skip("Postgres not reachable")` if the DB can't be reached, so the
    keyless/DB-less unit suite still passes clean everywhere else. Covers
    save + list (including the empty-notes phrasing).
  - Timers: fake `Session`-like harness (reuses the `FakeWebSocket` /
    fake-TTS patterns already in `conftest.py`) asserting `set_timer` returns
    immediately, `timer_fired` is emitted after the scheduled delay (test
    uses a very short duration, not real wall-clock minutes), a spoken phrase
    is synthesized, and cancelling the owning session cancels the pending
    timer task.
- Manual/live check: `scripts/ws_client.py --text "what's the weather in
  Tokyo?"` against a live backend, confirming a real `tool_call` →
  `tool_result` → spoken final reply with no 400s (mirrors the Phase 1/2 live
  smoke-test convention).

### Out of scope / explicitly deferred

- `web_search` — deferred to a later phase per user decision.
- Any notes UI beyond the existing generic tool chip (no dedicated "notes
  list" view).
- Timer persistence across reconnects (in-memory `asyncio.Task` only, same
  posture as the rest of per-connection session state).

## Commit slices (on `main`, TDD, one focused commit each)

1. Registry + `ToolContext` + session wiring (empty-but-wired) + `tool.execute`
   OTel span + `test_registry.py`.
2. `get_weather` + respx tests.
3. `save_note`/`list_notes` + Postgres-or-skip tests.
4. `set_timer` + session timer scheduling/spoken-fire + `timer_fired` frontend
   reducer case + minimal UI + tests.

Each slice: red test → green implementation → `ruff check` / `pytest` (and
`tsc`/`build`/`oxlint` for slice 4's frontend bit) → commit.

## Verification checklist (Definition of Done)

- [ ] `backend/tests` green including new `test_registry.py`/`test_tools.py`.
- [ ] `uv run ruff check .` clean.
- [ ] `npm run typecheck` / `npm run build` / `oxlint` clean (slice 4).
- [ ] Live smoke test: weather tool round-trip via `ws_client.py --text`, no
      400s, real spoken-style reply.
- [ ] Manual browser check: ask for weather, set a short timer, save/list a
      note — chips render, timer notification appears, no console errors.
- [ ] HANDOFF.md updated with Phase 4 status before wrap-up.
