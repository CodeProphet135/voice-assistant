# Voice Assistant — Project One-Pager

**A real-time, tool-using voice assistant you can talk to in the browser — and
then *replay* any conversation, token-by-token, against a latency timeline.**

Speak into the mic and it streams your speech to text, reasons with an LLM
(calling tools like weather, timers, and notes), and speaks its answer back —
all over a single WebSocket, sentence by sentence, at **sub-2-second
voice-to-voice latency**. Every session is captured as an append-only event log,
so the whole thing can be played back and inspected after the fact.

Solo build · ~3,500 lines · Python/FastAPI backend + React/TypeScript frontend ·
~120 tests.

---

## The "wow" factors

### 🎬 Deterministic replay from an event-sourced core
Every server→client event flows through **one seam** and is persisted as an
append-only log. The live UI is a **pure reducer** over that event stream — and
Replay re-drives the *exact same reducer* over the recorded events. That means
any past conversation can be scrubbed and replayed at 1×/2×/4×, reproducing the
original token-by-token streaming with zero separate "replay renderer."

This falls out of a deliberate early decision — the app owns its conversation
state (`store=False`) so a session is fully serializable. Replay wasn't bolted
on; the architecture was laid for it from day one.

### ⚡ Sub-2-second voice-to-voice latency through pipelining
The assistant starts *speaking the first sentence while the LLM is still writing
the rest*. A sync sentence-chunker feeds an async queue consumed by an ordered
TTS worker, so synthesis overlaps generation. Measured end-to-end: **~1.6 s**
from end-of-speech to first audio out.

### 🧠 A hand-written streaming agent loop
Streaming per-token text *and* executing tools mid-stream doesn't compose with
any SDK's built-in tool-runner — so the tool loop is custom: it consumes the
model's stream, runs tool calls, feeds results back as structured items, caps
iterations, and turns tool exceptions into text so a failing tool can never
crash the conversation.

### 🎙️ Interrupt it mid-sentence (barge-in) — without it hearing itself
You can talk over the assistant to cut it off; it cancels the turn and flushes
audio instantly. A self-echo guard scores and drops the assistant's *own* TTS
leaking back through the open mic, so it doesn't interrupt itself.

### 📊 A latency timeline + event inspector for every session
A per-turn Gantt chart on a shared time axis visualizes **listening → thinking →
speaking** phases, tool sub-spans, and barge-in cancels — turning latency into
something you can *see*. An Event Inspector exposes the raw event payloads,
including the exact input sent to the model on each agent-loop iteration.

---

## What this demonstrates

| Area | Evidence in this project |
|---|---|
| **Real-time & concurrency** | Full-duplex audio over WebSockets, `asyncio` pipelining of STT → LLM → TTS, backpressure via queues, clean teardown on disconnect |
| **LLM / agent engineering** | Custom streaming tool loop, serializable conversation state, prompt-cache-friendly stable system prompt, cost/latency-tuned model config |
| **Systems architecture** | Event sourcing through a single emit seam; live UI and replay share one pure reducer; a provider seam that keeps the vendor SDK out of the app |
| **Full-stack** | FastAPI + SQLAlchemy async + Postgres backend; React/TypeScript frontend with no state library (just `useReducer`); low-level AudioWorklet PCM capture |
| **Operability** | OpenTelemetry tracing (→ Jaeger), Alembic migrations, graceful degradation (recording self-disables if the DB is down; STT auto-reconnects) |
| **Engineering discipline** | ~120 tests with externals mocked (suite runs with no API keys), phased delivery, conventional commits, a living design/handoff doc trail |

---

## Under the hood

**Stack** — Python 3.12 · FastAPI · SQLAlchemy 2.0 async + asyncpg · Alembic ·
OpenTelemetry · React + Vite + TypeScript · OpenAI Responses API · Deepgram STT
& TTS · PostgreSQL · Docker Compose.

**A few decisions I'm proud of**
- **AudioWorklet, not MediaRecorder** — emits 16 kHz Int16 PCM at ~40 ms
  granularity for tight turn latency, instead of MediaRecorder's coarse chunks.
- **Recording is best-effort and off the hot path** — events are enqueued
  non-blocking and batch-written; a Postgres hiccup never touches the live call.
- **Turn detection is the STT provider's job** — no bespoke VAD to maintain;
  the design leans on the right tool for each seam.
- **The provider seam is a thin async `Protocol`** — swapping speech vendors is
  two classes, and the Deepgram SDK never leaks past one module.

---

## See it for yourself

Clone, add two API keys, and run four `make` commands — then have a conversation
and open the **Sessions** view to watch it replay. The README walks through both
the live demo and the replay, with an architecture diagram of the full live +
replay data flow.

> _Repo: `https://github.com/CodeProphet135/voice-assistant` · Author: `Harshit Sharma` · `https://www.linkedin.com/in/hsharma369`_
