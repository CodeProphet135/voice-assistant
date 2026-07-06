# Voice Assistant — Project Guide

Real-time, tool-using voice assistant: browser mic → WebSocket → FastAPI backend
(Deepgram STT → OpenAI agent loop → Deepgram TTS) → browser speaker. Portfolio
project — solo build, phased, no k8s/queues/auth-system over-engineering.

**Read [HANDOFF.md](HANDOFF.md) first** — it's the living status doc that says
exactly what's done, what's next, and what gotchas the last session hit. This
file only holds durable conventions that shouldn't need updating every phase.

## Stack

- Backend: Python 3.12, FastAPI, `uv` for deps/venv, SQLAlchemy 2.0 async +
  `asyncpg`, Alembic migrations, OpenTelemetry.
- Frontend: Vite + React + TypeScript, no state library (`useReducer`).
- LLM: OpenAI Python SDK (`openai`), **Responses API** — not Chat Completions.
- Speech: Deepgram (`deepgram-sdk`) for both STT (streaming) and TTS.
- DB: PostgreSQL via `docker compose up -d postgres`.

## Non-negotiable API conventions (established by design — don't relitigate)

- **OpenAI**: `client.responses.create(model=..., instructions=SYSTEM_PROMPT,
  input=input_items, tools=[...], stream=True, store=False,
  max_output_tokens=...)`. `store=False` — the app owns the `input` item list
  itself. This is deliberate: it makes conversation state fully serializable,
  which is what makes Phase 6 event-log replay possible. Default model
  `gpt-5-mini`, `reasoning={"effort": "minimal"}` for voice latency — both are
  env-configurable via `config.py` (`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`),
  don't hardcode elsewhere. No sampling params — reasoning models reject
  `temperature`/`top_p`.
- **The tool loop is manual, not the SDK's built-in tool-runner** — streaming
  per-token text and executing tools mid-stream don't compose with the runner.
  Function-call outputs go back as one
  `{"type": "function_call_output", "call_id", "output"}` item per call
  (max 6 loop iterations, lives in `agent/agent.py`). Tool exceptions become
  error-text outputs — never let a tool crash the loop.
- **`agent/chunker.py` is a pure sync class** (`feed()`/`flush()`) — it's
  trivial string splitting, nothing to bridge across threads. The async agent
  task calls `chunker.feed()` inline while consuming the stream, then
  `await tts_queue.put(sentence)`. One event loop; no `janus`, no
  `call_soon_threadsafe`. This was a deliberate decision, not an oversight —
  don't "fix" it into an async chunker.
- **Deepgram STT**: `nova-3`, `encoding=linear16`, `sample_rate=16000`,
  `endpointing=300`, `utterance_end_ms=1000`, `vad_events=true`. Turn detection
  is Deepgram's job — no custom VAD.
- **Deepgram TTS**: Aura REST, synthesized **per sentence** (not the Deepgram
  TTS WebSocket), `linear16` @ 24kHz, `container=none`.
- **Mic capture**: AudioWorklet → 16kHz mono Int16 PCM frames, not
  MediaRecorder (Deepgram wants raw `linear16`, and MediaRecorder's practical
  chunk granularity is too coarse for low latency).
- **Event sourcing (Phase 6)**: every `session.emit()` call both sends to the
  WS and will hand off to an `EventRecorder` for append-only persistence. The
  frontend live UI is a pure reducer over the event stream (`state.ts`) —
  Replay reuses that exact reducer at recorded or scaled timing. Don't build a
  separate replay-rendering path.

## Commands

```bash
make dev-backend    # uvicorn on :8000 (uv run)
make dev-frontend   # vite on :5173
make db-up          # postgres via docker compose (add --profile observability for Jaeger)
make migrate        # alembic upgrade head
make test           # backend pytest + frontend typecheck
make lint           # ruff + tsc
```

## Conventions

- Conventional commits, one focused commit per slice — check `git log --oneline`
  for the established style before adding more.
- Keep the OpenAI system prompt (`agent/prompts.py`) frozen — no
  timestamps/session IDs interpolated. OpenAI's automatic prompt caching needs
  a stable prefix ≥1024 tokens.
- Provider seam (`providers/base.py`) is a thin async `Protocol` — Deepgram is
  the only concrete implementation in v1. Don't add a second provider unless
  asked; the seam exists to *demonstrate* swappability, not to be exercised.
