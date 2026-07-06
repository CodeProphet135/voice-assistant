# Handoff — Current Status & Next Steps

Read this before touching anything else in this repo — it's the source of
truth for where the last session left off. Update it before you stop working,
so the next session (or the next you) doesn't have to reconstruct context from
`git log`.

## Status: Phase 0 complete ✅

Repo scaffold is built, verified end-to-end, and committed. Clean working tree,
four commits on `main`:

```
6a43f1f feat(frontend): Vite + React + TypeScript scaffold
054b078 feat(backend): Postgres persistence + Alembic migrations (notes table)
5b943b9 feat(backend): FastAPI scaffold with config and OpenTelemetry bootstrap
8b9f70b chore: scaffold repo (license, docker, CI, env template)
```

Verified live in this environment (not just "should work"):

- `cd backend && uv sync` installs cleanly; `uv run ruff check .` and
  `uv run pytest` both pass.
- Docker Desktop was **not** running at session start — had `open -a Docker`,
  then polled `docker info` until ready (~20s). Expect the same next time.
- `docker compose up -d postgres` → `uv run alembic upgrade head` applied the
  `0001_notes` migration cleanly against a live Postgres 16 container;
  confirmed the `notes` table schema via `psql`.
- `uv run uvicorn voice_assistant.main:app` serves `/healthz` →
  `200 {"status":"ok"}`, and OpenTelemetry console-exporter spans are visibly
  flowing (no `OTEL_EXPORTER_OTLP_ENDPOINT` set yet, so it falls back to
  console — expected, not a bug).
- `cd frontend && npm install && npm run typecheck && npm run build` all pass.

## What's NOT built yet

Nothing beyond scaffolding exists:

- No `/ws` endpoint, no `protocol.py`, no `session.py` orchestrator.
- No agent loop, no chunker, no tools, no providers.
- No frontend components beyond the placeholder `App.tsx`.
- `models.py` only has `Note` — `Session`/`Event` models are Phase 6 work.

## Next up: Phase 1 — Text chat loop end-to-end (no audio yet)

Goal: type a message in the browser → OpenAI streams a reply token-by-token →
renders live. This is the harness every later phase builds on, so get the tool
loop's shape right even though the tool list starts empty.

Files to create (see CLAUDE.md's API conventions before writing the agent loop):

- `backend/src/voice_assistant/protocol.py` — typed WS JSON frame schemas
  (client: `start`/`stop`/`text_input` + binary PCM; server: `ready`, `state`,
  `stt_partial`/`stt_final`, `assistant_delta`/`assistant_done`, `tool_call`/
  `tool_result`, `tts_start`/binary/`tts_end`/`tts_cancel`, `timer_fired`,
  `error`). Audio frame types can be defined now even though STT/TTS land in
  Phases 2–3 — the protocol is one contract.
- `backend/src/voice_assistant/session.py` — per-connection orchestrator,
  text-input path only for now. Build the `emit()` seam now (send to WS); wire
  it to an `EventRecorder` in Phase 6, don't build the recorder yet.
- `backend/src/voice_assistant/agent/agent.py` — the full streaming OpenAI
  Responses tool-use loop. Tool list is empty for now, but the `function_call`
  branch must be written and tested — Phase 4 tools slot in without touching
  this file.
- `backend/src/voice_assistant/agent/chunker.py` — pure sync sentence chunker.
- `backend/src/voice_assistant/agent/prompts.py` — frozen `SYSTEM_PROMPT`
  (voice style: 1–3 conversational sentences, no markdown/lists/code).
- `backend/tests/conftest.py` — `FakeOpenAI` fixture with scripted Responses
  stream events.
- `backend/tests/test_agent_loop.py`, `test_chunker.py`.
- `frontend/src/ws.ts` (typed client mirroring `protocol.py`), `state.ts`
  (reducer), a minimal `Transcript` component + text input in `App.tsx`.
- `scripts/ws_client.py` — CLI WS test client, `--text "hello"` mode first
  (add `--wav` support in Phase 2).

Don't mark Phase 1 done without all of:

- [ ] `pytest backend/tests/test_agent_loop.py backend/tests/test_chunker.py` green
- [ ] `python scripts/ws_client.py --text "hello"` prints streamed
      `assistant_delta` events ending in `assistant_done`
- [ ] Manual browser test: typed chat, streamed reply renders live
- [ ] **One real API call** against OpenAI confirming `reasoning.effort` +
      `store=False` + an empty/minimal tool schema are all accepted (no 400s).
      Needs `OPENAI_API_KEY` in `.env` — if it's not there yet, **ask the user
      for it**, don't skip this check silently.

## Prerequisites still needed from the user

- `OPENAI_API_KEY` — needed for Phase 1's live smoke test.
- `DEEPGRAM_API_KEY` — needed starting Phase 2, not yet.
- Docker running locally, for Postgres now and Jaeger later.

## Full phase plan

(Checklist mirrors [README.md](README.md#roadmap) — keep both in sync.)

1. ✅ Scaffold
2. ⬜ Text chat loop end-to-end — **you are here**
3. ⬜ Voice in (Deepgram STT, AudioWorklet mic capture)
4. ⬜ Voice out + barge-in (Deepgram TTS, gapless playback, interrupt handling)
   — tag `v0.1.0`
5. ⬜ Tools (weather, web_search, timers, notes)
6. ⬜ Polish (full test suite, README diagram + latency table, Docker image,
   error-handling passes)
7. ⬜ Event Timeline + Replay (event sourcing, Temporal-style replay UI) — tag
   `v1.0.0`

The full per-phase design rationale (audio pipeline choices, WS protocol
table, barge-in state machine, OTel span layout, event-sourcing details) was
worked out before Phase 0 started. If
`~/.claude/plans/i-am-a-software-replicated-cloud.md` still exists on this
machine it has the complete original doc — but don't depend on it being
there; everything load-bearing has already been copied into `CLAUDE.md` and
this file.

## Known gotchas hit during Phase 0 (don't rediscover these)

- `tsc -b --noEmit` is an **invalid** flag combination in TypeScript project-
  reference build mode. `noEmit: true` is already set per-project in
  `tsconfig.app.json`/`tsconfig.node.json`, so `frontend/package.json`'s
  `typecheck` script is just `tsc -b`.
- `create-vite`'s `react-ts` template ships a marketing landing page (hero
  image, logos, Oxlint scaffolding) — already stripped down to a minimal
  `App.tsx`/`index.css`. Don't reintroduce it.
- Ruff reformats import blocks in Alembic-generated files. Run
  `ruff check . --fix` after `alembic revision --autogenerate` and before
  committing, or CI's lint job will fail.
- A `StarletteDeprecationWarning` about `httpx`/`starlette.testclient` appears
  in `pytest` output (FastAPI's `TestClient` uses `httpx` under the hood).
  Harmless — not something to fix right now.
- Docker Desktop needs a manual `open -a Docker` + poll-until-ready on this
  machine; it is not started automatically.
