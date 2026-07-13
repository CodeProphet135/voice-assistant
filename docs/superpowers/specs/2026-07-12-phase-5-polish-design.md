# Phase 5 — Polish: Design

Status: approved-pending-review · Date: 2026-07-12

Phase 5 hardens the working Phase 0–4 assistant for a portfolio audience. It is
five loosely-coupled slices, each independently committable, CI green at each
step. No new product surface — this is reliability + documentation + a
verified container image.

## Scope decisions (confirmed with user)

- **Deepgram STT reconnect**: bounded transparent auto-reconnect on an
  unexpected mid-conversation socket drop; surface an error only when retries
  are exhausted.
- **Jaeger waterfall screenshot + demo GIF**: **deferred to Phase 6** (both need
  a live browser+mic demo run that can't happen headlessly). No placeholder
  slots ship in the README now; Phase 6 owns capturing them.
- **Jaeger default**: **restore the opt-in `--profile observability`
  convention** documented in CLAUDE.md/README. Revert the uncommitted working-
  tree edits that made Jaeger always-on.
- **Test additions**: Alembic upgrade-head migration smoke test + a frontend
  `state.ts` reducer determinism vitest, plus unit tests for the new reconnect
  and truncation code.

## Slice 1 — History truncation guard

**Problem.** `Session.input_items` grows unbounded across a long conversation;
every turn re-sends the full list to OpenAI. PLAN: "Truncate oldest turns past
~40 items, never the system prompt." The system prompt rides in
`instructions=`, not in `input_items`, so truncation is purely a matter of
trimming the oldest `input_items`.

**Invariant that makes this non-trivial.** A `function_call` item and its
matching `function_call_output` (linked by `call_id`) must never be split — an
orphaned output (or call) is a hard 400 from the Responses API. Turns are laid
out as `user message → [assistant function_call…, function_call_output…] →
assistant text`, so a `user` message item is always a safe cut boundary (start
of a whole turn).

**Design.** A pure function `truncate_history(items, max_items) -> list[dict]`
in a new `agent/history.py`:
- If `len(items) <= max_items`, return `items` unchanged.
- Otherwise compute a tentative cut at `len - max_items`, then advance the cut
  forward to the next `user` message boundary so only whole turns are dropped.
  If no user-message boundary exists at/after the tentative cut (degenerate:
  one enormous turn), keep the whole tail — never orphan a call/output pair.

`Session._run_turn` calls it once at the top, before appending the new user
message, replacing `self.input_items` with the trimmed list. `MAX_INPUT_ITEMS =
40`, a module constant in `session.py`.

**Tests** (pure, no asyncio): under-cap no-op; over-cap trims to whole turns;
never orphans a `function_call`/`function_call_output` pair; single-giant-turn
degenerate case keeps the tail.

## Slice 2 — Deepgram STT reconnect

**Problem.** `DeepgramSTT.start()` opens the socket once. If Deepgram drops the
connection mid-conversation, `_read_loop`'s `async for` ends, the reader task
exits (logs a warning), and `events()` blocks forever on an empty queue —
`_consume_stt` hangs, the mic appears live but is dead, audio is silently
discarded. No recovery.

**Design — reconnect lives in the provider** (keeps vendor logic behind the
seam; `session.py` stays vendor-agnostic).

- Extract `_open_socket()` / `_close_socket()` from `start()`.
- Add a `self._closing` flag. `finish()` and `aclose()` set it True so an
  intentional stop is distinguished from an unexpected drop.
- `_read_loop` becomes a reconnect loop: run `async for msg in self._socket`;
  when it ends, if `self._closing` → break; else attempt bounded reconnect
  (`_MAX_RECONNECT_ATTEMPTS = 3`, exponential backoff via `asyncio.sleep`, e.g.
  0.5 / 1 / 2 s). Any successfully received message resets the attempt counter.
  On exhaustion, enqueue a terminal `SttClosed()` normalized event and break.
- New normalized event `SttClosed` in `providers/base.py` (added to the
  `SttEvent` union). Vendor types still never leak.

**Session handling.** `_consume_stt` gains an `SttClosed` branch: emit
`ErrorEvent("Speech recognition disconnected — please restart the mic.")` +
`StateEvent(state="idle")`, detach the provider (`self.stt = None`,
`self._stt_task = None`), `await provider.aclose()`, and return. It cannot call
`_stop_stt()` (that would cancel-and-await its own task).

**Tests** (fake sockets, no real SDK, no key):
- Provider: a fake connect factory yields a socket that ends after K messages;
  assert `_read_loop` reopens and continues; after `_MAX_RECONNECT_ATTEMPTS`
  failed reopens it enqueues exactly one `SttClosed`.
- Provider: `finish()`/`aclose()` set `_closing` so a socket end does **not**
  trigger reconnect.
- Session: a fake STT that emits `SttClosed` drives one `ErrorEvent` + an
  `idle` `StateEvent` and leaves `self.stt is None`.

## Slice 3 — README

Fill the two `TODO(Phase 5)` blocks and refresh surrounding copy:
- **Mermaid architecture diagram** replacing the ASCII block: browser (mic/
  speaker) ⇄ WebSocket ⇄ FastAPI session orchestrator → Deepgram STT / OpenAI
  agent loop (with tool registry) / Deepgram TTS, plus Postgres and OTel/Jaeger.
- **Latency budget table**: the voice-to-voice budget with the measured
  numbers we already have (STT endpoint ~300 ms, LLM TTFT ~0.85 s on
  gpt-5-mini+minimal, first-sentence TTS synth, first-audio) → sub-2 s target.
  Numbers cited as measured where HANDOFF recorded them, else labeled target.
- **Design Decisions** section: AudioWorklet vs MediaRecorder, sentence-chunked
  REST TTS vs the TTS WebSocket, manual streaming tool loop vs the SDK runner,
  `store=False` client-owned conversation state (→ Phase 6 replay), the
  provider seam, barge-in + self-echo guard, event-sourcing foundation.
- Update the observability line to the opt-in `--profile observability` wording
  once Slice 5 restores it.
- **No** screenshot/GIF slots (deferred to Phase 6). Roadmap unchanged.

## Slice 4 — Test-suite completion

- `backend/tests/test_migrations.py`: `alembic upgrade head` against the test
  Postgres, assert it succeeds and the `notes` table exists. Follows the
  existing notes-test pattern — `pytest.skip` when Postgres is unreachable.
- Frontend vitest: add `vitest` + `jsdom` dev deps, a `test` script
  (`vitest run`), and `frontend/src/state.test.ts` asserting reducer
  determinism — a fixed event sequence yields the expected `AppState`, and the
  reducer is pure (same `(state, event)` → deep-equal result, input unmutated).
- `make test` / `make lint` updated to run the frontend test where sensible
  (keep backend pytest as the primary `make test`; frontend test invoked in the
  frontend typecheck/build path or added to `make test`).

## Slice 5 — Docker image + Jaeger opt-in restore

- **Revert** the working-tree edits that made Jaeger always-on: `docker-
  compose.yml` `jaeger` regains `profiles: ["observability"]`; `Makefile`
  `db-up` goes back to `docker compose up -d postgres` (Jaeger via
  `--profile observability`); `.env.example` `OTEL_EXPORTER_OTLP_ENDPOINT`
  back to empty (console exporter default) with the helpful Jaeger-URL comment
  retained as guidance.
- **Verify the container**: `docker compose --profile app build app` then a
  cold `up` serving `/healthz` → 200 and the built frontend at `/`. Fix any
  Dockerfile/static-path issues found. If Docker isn't runnable in this
  environment, the build attempt + a documented human cold-start check stands
  in (per the PLAN verification note), clearly flagged in HANDOFF.

## Out of scope (explicit)

- `web_search` built-in tool (deferred; may fold into Phase 6 or its own slice).
- Jaeger screenshot + demo GIF (Phase 6).
- Any new tools, protocol changes, or frontend feature surface.

## Verification (whole phase)

- `cd backend && uv run pytest -q` green (existing 89 + new); `uv run ruff
  check .` clean.
- `cd frontend && npm run typecheck && npm run build && npm run test` clean.
- `docker compose --profile app build app` succeeds (cold `up` /healthz check
  where Docker is available, else documented as a human step).
- README renders (Mermaid + table) on GitHub.
- HANDOFF updated to "Phase 5 done", with any human-only checks called out.
