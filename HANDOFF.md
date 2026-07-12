# Handoff — Current Status & Next Steps

Read this before touching anything else in this repo — it's the source of
truth for where the last session left off. Update it before you stop working,
so the next session (or the next you) doesn't have to reconstruct context from
`git log`.

## Status: Phase 3 done ✅ (voice out + barge-in — voice-out verified live end-to-end)

Phases 0–2 are done and committed on `main`. **Phase 3 (voice out — Deepgram
TTS, gapless browser playback — plus barge-in interruption) is built, reviewed,
and merge-ready on branch `phase-3-voice-out`** (not yet merged to `main` /
tagged `v0.1.0` — that's the wrap-up step). Backend is **49 tests green**;
frontend typecheck/build/lint clean. The full voice-out path was verified LIVE
against real Deepgram + OpenAI. The only unchecked box is the human-at-a-mic
**barge-in** browser test (can't run headlessly). Numbering note: this is
PLAN.md's "Phase 3" and README's "Phase 3"; it is the 4th item in the "Full
phase plan" list further down.

### Phase 3 — what was built (branch `phase-3-voice-out`)

Built by sequential subagents (implementer → task review → fix loop per task,
then a whole-branch review), each on Sonnet. Commits, in order:
- `413cd04` **DeepgramTTS + TTSProvider seam.** `providers/base.py` gains a
  `TTSProvider` `Protocol` (`synthesize(text) -> AsyncIterator[bytes]`,
  `aclose()`); `providers/deepgram.py` gains `DeepgramTTS`. Uses the installed
  **`deepgram-sdk` 7.4.0** REST TTS surface: `client.speak.v1.audio.generate(
  text=..., model=settings.deepgram_tts_model, encoding="linear16",
  sample_rate=24000, container="none")` returns an `AsyncIterator[bytes]` of raw
  PCM. Construction is cheap/keyless (client built lazily via `_make_client()`);
  vendor errors degrade silently. (Do NOT "fix" this to a v3 API — verify against
  the installed package.)
- `c119b81` + `f5d03d9` **session TTS worker + cancellable-turn + OTel spans.**
  `session.py` now runs each turn under `_turn_lock` wrapped in a `turn` OTel
  span. `on_sentence` pushes each chunker sentence onto `self._tts_queue`; a
  producer task runs `run_agent` and enqueues a `_TURN_END` sentinel in its
  `finally`; the drain loop pulls sentences, emits `TtsStartEvent(sentence_index,
  text)`, synthesizes per sentence inside a `tts.synthesize` span, and streams
  PCM to the browser via `self.ws.send_bytes(chunk)` (binary, NOT through
  `emit()`). State machine: `thinking → speaking` (first sentence) `→
  listening/idle` (after `TtsEndEvent`). The STT-commit path launches the turn
  as `self._turn_task` (create_task, NOT awaited) so `_consume_stt` keeps
  looping. `_run_turn`'s `except CancelledError` branch cancels the producer,
  **drains the queue**, and re-raises (fix `f5d03d9` — so a cancelled turn can't
  leave stale sentences/sentinel for the next turn). `self.tts` is closed in
  `run()`'s finally. This also incidentally put the text-input path under the
  turn lock (resolves an old follow-up).
- `2465b22` **barge-in.** `_consume_stt` calls `_barge_in()` when a turn is
  active (`_turn_active()`) and a `SpeechStarted` OR a non-empty interim
  transcript arrives. `_barge_in` cancels+awaits `self._turn_task`, emits
  `TtsCancelEvent`, and settles state to `listening`. `SpeechStarted` with no
  active turn is still a no-op; a plain `stop` still does NOT abort an in-flight
  turn (that Phase 2 path is unchanged).
- `b806127` **frontend playback + StatusBadge.** `audio/player.ts` `AudioPlayer`
  plays raw linear16/24kHz/mono PCM gaplessly (`nextStartTime` cursor,
  `createBuffer(1,n,24000)`, odd-byte carry across frames), with `flush()` for
  barge-in (stops sources, resets cursor + leftover byte). `ws.ts` sets
  `binaryType='arraybuffer'` and surfaces binary frames via an `onAudio`
  callback. `state.ts` gains a **pure** `tts_cancel` case (`assistantInProgress
  = false`, so the next turn starts a fresh assistant bubble). `App.tsx` owns one
  `AudioPlayer`, wires `onAudio → enqueue`, and calls `player.flush()` on
  `tts_cancel`. `components/StatusBadge.tsx` extracted; `index.css` styles the
  pipeline-state pill.
- `ec5f518` **polish** (from final review): `_barge_in` now logs non-cancel
  teardown exceptions; `player.ts` guards `resume()` against unhandled rejection.

### Phase 3 — verified this session
- Backend: `cd backend && uv run pytest -q` → **49 passed**; `uv run ruff check
  .` clean. All TTS/barge-in tests are hermetic (fake STT/TTS/OpenAI — no keys).
- Frontend: `npm run typecheck` (`tsc -b`), `npm run build`, `npx oxlint` all
  clean; the `pcm-worklet` chunk still emits.
- Whole-branch review (final gate) returned **READY** — verified the
  backend↔frontend PCM/event contract, barge-in coherence, resource teardown,
  and the OTel span tree empirically (in-memory exporter + a 15-interleaving
  barge-in race stress test).
- **LIVE end-to-end voice-out** (real Deepgram + OpenAI, `scripts`-style WAV
  round trip via `scratchpad/verify_tts.py`): streamed a 16 kHz WAV of "What is
  the capital of France?" → `stt_final` (exact) → assistant "The capital of
  France is Paris." → `state:speaking` → `tts_start #0` → **51 binary audio
  frames ≈ 2.04 s of 24 kHz PCM** → `tts_end`. OTel `turn` span parents both
  `llm.request` and `tts.synthesize` (confirmed from the live console trace).

### Phase 3 — DoD status
- [x] Deepgram TTS synthesizes per sentence; audio streams to the browser as
      binary WS frames — verified live (51 frames, ~2.04 s).
- [x] Gapless player + `tts_cancel` flush path — built, typecheck/build/lint
      clean; happy-path playback smoke-tested live in the browser by the
      implementer (thinking→speaking→idle, audio scheduled, no console errors).
- [x] Barge-in backend (cancel turn on new speech, `tts_cancel`, queue drain) —
      unit-tested + adversarially race-tested in review.
- [x] Full-turn OTel spans (`turn` → `llm.request`/`tts.synthesize`) — confirmed
      in the live trace.
- [ ] **Manual browser BARGE-IN test** — the one remaining human check. `make
      dev-backend` + `make dev-frontend`, click 🎤, let the assistant start
      speaking, then talk over it: audio should stop <300 ms with no garbled
      playback, and a fresh reply should start. Can't be run headlessly (no mic).
      **⚠️ First attempt (2026-07-11) hit a real bug: with speakers, the
      assistant barges in on ITSELF** — its own TTS echo reaches the mic
      (Chrome AEC doesn't cancel Web Audio playback), cancels the turn ~1s in,
      and even commits the echo as a user turn (self-reply loop). Diagnosed +
      reproduced deterministically; fix deferred. Full analysis + fix options:
      [docs/bug-self-barge-in-echo.md](docs/bug-self-barge-in-echo.md).

### Wrap-up still pending for Phase 3
- Merge `phase-3-voice-out` → `main` and tag **`v0.1.0`** (per PLAN Phase 3).
- The manual mic barge-in check above (human).
- `.superpowers/sdd/progress.md` holds the per-task ledger + two logged benign
  minors (a `_commit_stt_turn` lock-acquisition race that can settle to `idle`
  instead of `listening` on a same-tick `stop`; both are non-blocking).

---

## Status: Phase 2 done ✅ (voice-in verified live end-to-end)

Phases 0–1 are done and committed on `main`. **Phase 2 (voice in — Deepgram
streaming STT + AudioWorklet mic capture) is built, merged, and verified live
end-to-end** — all committed on `main`. Both `OPENAI_API_KEY` and
`DEEPGRAM_API_KEY` are present in `backend/.env` (note the format:
`KEY = "value"` with spaces + quotes — `pydantic-settings` strips both fine, so
a naive `grep 'KEY=.\+'` misses them; check via `settings.deepgram_api_key`).
The only unchecked box is the human-at-a-mic browser test (can't run
headlessly). See the Phase 2 section below.

### Phase 2 — what was built (committed on `main`)

Built by two parallel subagents in isolated worktrees, then merged (backend and
frontend touch disjoint files, so the merges were conflict-free).

Backend:
- `providers/base.py` — the STT provider seam: `STTProvider` `Protocol`
  (`start`/`send_audio`/`finish`/`events`/`aclose`) + normalized
  `Transcript`/`SpeechStarted`/`UtteranceEnd` dataclasses (`SttEvent` union).
  Vendor types never leak past `providers/`.
- `providers/deepgram.py` — `DeepgramSTT`, the only concrete impl. **Uses the
  actually-installed `deepgram-sdk` 7.4.0 API, which is a totally different
  surface from the v3 patterns CLAUDE.md/PLAN.md were written against** —
  `AsyncDeepgramClient().listen.v1.connect(...)` is an *async context manager*
  yielding an `AsyncV1SocketClient` (`send_media(bytes)`, `recv()`/`async for`,
  `send_close_stream()`); received messages are pydantic models discriminated
  by `.type` (`"Results"`/`"SpeechStarted"`/`"UtteranceEnd"`/`"Metadata"`),
  transcript at `msg.channel.alternatives[0].transcript` with
  `is_final`/`speech_final`. Do NOT "fix" this back to the v3 callback API.
  Connection opens only in `start()` — construction is cheap and keyless, so
  the text path and unit tests need no Deepgram key.
- `session.py` — STT turn path wired in. `_run_turn(text)` is the shared body
  for both the text-input and STT-commit paths (settles to `listening` if the
  mic is open, else `idle`). `StartEvent` → open STT + spawn `_consume_stt`;
  binary frames in `run()` → `stt.send_audio(bytes)`; interim → `stt_partial`;
  `is_final` accumulates, `speech_final` (or `UtteranceEnd` fallback) commits →
  `stt_final` + agent. `_turn_lock` serializes turns; `_stop_stt` tears down on
  `StopEvent` and in `run()`'s `finally` (dropped socket can't leak the task).
  `_make_stt_provider()` is a factory seam tests override to inject a fake.
- `tests/test_session_stt.py` (8 tests) + a `FakeWebSocket` harness in
  `conftest.py`. All mocked — no Deepgram key needed.
- `scripts/ws_client.py` — new `--wav <path>` mode streams a 16 kHz mono PCM
  WAV as binary frames and prints `stt_partial`/`stt_final` (needs a live
  server + Deepgram key to actually run).

Frontend:
- `audio/pcm-worklet.ts` — `AudioWorkletProcessor` resampling native-rate
  Float32 mic audio → 16 kHz Int16LE PCM. Loaded via
  `import url from './pcm-worklet.ts?worker&url'` — **not** `addModule(new
  URL(...))`, which this Vite mis-sniffs as MPEG-TS and inlines untranspiled TS
  (crashes at runtime). See gotchas.
- `audio/mic.ts` — `MicCapture` (getUserMedia `echoCancellation:true` →
  AudioContext → worklet → `onPcm` callback), start/stop, idempotent.
- `ws.ts` — `sendAudio(data: ArrayBuffer)` binary sender.
- `state.ts` — `stt_final` now commits a `{role:'user'}` message + clears the
  partial; `stt_partial` sets the live partial. Reducer stays pure.
- `components/MicButton.tsx` — toggle button: sends `start`, streams PCM via
  `sendAudio`, sends `stop`; surfaces mic-permission errors.
- `App.tsx`/`Transcript.tsx`/`index.css` — MicButton wired next to text input,
  live partial shown as a muted in-progress bubble, mic styling (pulsing dot).

### Phase 2 — verified this session (no keys needed — all mocked)
- `cd backend && uv run ruff check .` clean; `uv run pytest` → **35 passed**
  (27 pre-existing + 8 new STT tests). Sanity-imports of `session` and
  `providers.deepgram` succeed with no Deepgram/OpenAI key set.
- `cd frontend && npm run typecheck` (`tsc -b`) clean; `npm run build` succeeds
  and emits `pcm-worklet-*.js` as its own transpiled chunk; `oxlint` exit 0.
- Frontend renders in the preview: mic button + text input mount, no console
  errors (status shows CLOSED only because no backend was running).

### Phase 2 — DoD status
- [x] **WAV smoke test — PASSED live** against real Deepgram + real OpenAI.
      Full `/ws` round trip: `ready → listening → stt_partial ("What is the
      weather in") → stt_partial ("What is the weather in Tokyo today?") →
      stt_final → thinking → assistant_delta×N → assistant_done`. Transcription
      was exact; TTFT (stt_final → first assistant token) ≈ **0.85 s**
      (gpt-5-mini + minimal reasoning). Repro: generate a 16 kHz mono 16-bit
      PCM WAV **with ~2 s trailing silence** (endpointing needs the pause to
      fire `speech_final`) and stream it:
      `say -o /tmp/s.aiff "What is the weather in Tokyo today?" && \`
      `afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/s.aiff /tmp/s.wav`, pad with
      2 s of `\x00` frames, then `cd backend && uv run python \`
      `../scripts/ws_client.py --wav /tmp/s_padded.wav` (run the client with
      `python -u` — its stdout buffers when piped). There's still no committed
      `samples/` fixture; a WAV with no trailing silence commits nothing until
      `stop`, so always pad it.
- [ ] **Manual browser mic test** — the one remaining human check. `make
      dev-backend` + `make dev-frontend`, click 🎤, speak, confirm the live
      partial transcript renders then a streamed reply. Can't be run
      headlessly (no mic); everything it depends on is verified via the WAV
      round trip above + the frontend build/typecheck.

### A real bug the live test caught + fixed (commit `3ec4bbf`)
The first live WAV round trip transcribed and committed the turn but the
OpenAI reply was **cancelled mid-stream**: the `--wav` client sends `stop`
after the audio ends, and `_stop_stt` was hard-cancelling the STT consumer
task while it was awaiting `run_agent`. Fixed — `_stop_stt` now calls
`finish()` then waits on `_turn_lock` so an already-committed utterance's reply
completes before teardown. Cancelling a turn on *new* speech (barge-in) stays a
separate, explicit Phase 3 path. Regression test:
`test_stop_does_not_abort_in_flight_turn` (freezes the agent mid-turn, asserts
`stop` doesn't drop the reply). Backend is now **36 tests**.

### Follow-ups / smaller notes for next session
- **`openai` SDK bumped to 2.44.0**, which now raises `OpenAIError` at
  `AsyncOpenAI()` construction if no key is resolvable (env or `.env`). Runtime
  is fine when a key is present; but `Session.__init__` builds the client
  eagerly, so a bare `Session()` with no key raises. The STT tests work around
  it with `os.environ.setdefault("OPENAI_API_KEY", "test-key")` at import.
  Consider making the client lazy or tolerating a missing key in `__init__`.
- `StartEvent.sample_rate` is advisory only — `DeepgramSTT` hardcodes 16 kHz
  per convention; not plumbed through.
- Text-input `_run_turn` isn't under `_turn_lock` (only the STT-commit path is),
  so typing *and* talking at the exact same instant could race `input_items`.
  Harmless in practice for P2; tighten if it ever matters.

---

_Historical (Phase 1) notes below._

## Status: Phase 1 code-complete ✅ (live smoke test pending an OpenAI key)

Phase 0 scaffold plus the full Phase 1 text-chat loop are built and verified.
Committed on `main` (Phase 1's commits: `protocol`, agent loop, session/`/ws`,
frontend chat UI, `ws_client.py`).

Phase 0's four commits on `main`:

```
6a43f1f feat(frontend): Vite + React + TypeScript scaffold
054b078 feat(backend): Postgres persistence + Alembic migrations (notes table)
5b943b9 feat(backend): FastAPI scaffold with config and OpenTelemetry bootstrap
8b9f70b chore: scaffold repo (license, docker, CI, env template)
```

### Phase 1 — what was built (uncommitted working tree)

Backend:
- `protocol.py` — all WS frames (client + server), pydantic discriminated
  union, `parse_client_event()`. This is the one contract; `ws.ts` mirrors it.
- `agent/agent.py` — the streaming Responses tool-use loop (`run_agent`,
  signature `async def run_agent(*, client, input_items, tools, emit,
  on_sentence, tool_executor) -> str`). Mutates `input_items` in place. Empty
  tool list in P1 but the function_call branch is fully written + tested.
- `agent/chunker.py` — pure sync `Chunker` (`feed`/`flush`), abbreviation- and
  decimal-aware.
- `agent/prompts.py` — frozen `SYSTEM_PROMPT` (voice style, no interpolation).
- `session.py` — `Session` orchestrator, text path. `emit()` seam is the single
  server→client path (Phase 6 hooks `EventRecorder` in there). `on_sentence`
  and `tool_executor` are stubs awaiting Phase 3/4.
- `main.py` — `@app.websocket("/ws")`, declared before the static mount.
- Responses **user-turn input shape** used:
  `{"type":"message","role":"user","content":[{"type":"input_text","text":...}]}`.

Frontend:
- `ws.ts` — typed `ClientEvent`/`ServerEvent` mirroring protocol.py,
  `VoiceAssistantClient` (derives ws URL from `window.location`).
- `state.ts` — **pure reducer** (`reducer`, `initialState`), no side effects —
  this is the same reducer Phase 6 Replay will reuse. Don't add effects to it.
- `components/Transcript.tsx`, wired `App.tsx` (useReducer + text input).

Tooling: `scripts/ws_client.py` (`--text` mode; `--wav` TODO for Phase 2).

### Verified this session (no OpenAI key needed — all against `FakeOpenAI`)
- `uv run pytest` → **27 passed** (health + 20 chunker + 6 agent-loop).
- `uv run ruff check .` clean; frontend `npm run typecheck` + `build` clean;
  `oxlint` zero warnings.
- **Full `/ws` round trip** driven through FastAPI TestClient with a fake
  OpenAI: event sequence `ready → state(thinking) → assistant_delta×N →
  assistant_done → state(idle)`, text reconstructs, and the `create()` call
  carried `store=False`, `stream=True`, `reasoning={effort:minimal}`,
  `tools=[]`, `model=gpt-5-mini`. OTel span nesting confirmed
  (`HTTP /ws` → `llm.request`).

### Phase 1 — DoD status
- [x] **One real Responses API call** — ✅ done this session. `OPENAI_API_KEY` is
      in `backend/.env`. Ran `scripts/ws_client.py --text "hello"` against a live
      backend: streamed `ready → thinking → assistant_delta×10 → assistant_done`
      ("Hi — how can I help you today?"), **no 400s** — `store=False`,
      `reasoning={effort:minimal}`, empty tool schema all accepted by the live
      API. TTFT ≈ 1.65 s (gpt-5-mini + minimal reasoning).
- [ ] **Manual browser test** — not yet run headlessly. `make dev-backend` +
      `make dev-frontend`, type a message at http://localhost:5173, confirm the
      reply streams in live. (Everything it depends on is already verified via
      the TestClient round trip + live smoke test; this is the last human check.)

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
2. ✅ Text chat loop end-to-end
3. ✅ Voice in (Deepgram STT, AudioWorklet mic capture) — verified live
   end-to-end; only the human browser-mic check remains
4. ✅ Voice out + barge-in (Deepgram TTS, gapless playback, interrupt handling)
   — built + reviewed on `phase-3-voice-out`, voice-out verified live; pending
   merge to `main` + tag `v0.1.0` + human mic barge-in check
5. ⬜ Tools (weather, web_search, timers, notes) — **you are here next**
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

## Known gotchas hit during Phase 2 (don't rediscover these)

- **`deepgram-sdk` resolved to 7.4.0**, a fully rewritten generated SDK — its
  live-STT surface bears no resemblance to the v3 `DeepgramClient().listen.
  asyncwebsocket.v("1")` + `LiveTranscriptionEvents` callback API you'll find
  in most docs/tutorials and in CLAUDE.md's wording. The real API is
  `AsyncDeepgramClient().listen.v1.connect(...)` (async CM) →
  `AsyncV1SocketClient` with `send_media`/`recv`/`async for`/`send_close_stream`
  and `.type`-discriminated pydantic messages. `providers/deepgram.py` is
  correct against the installed version — verify against the package, not memory.
- **AudioWorklet + Vite:** `audioContext.audioWorklet.addModule(new URL(
  './pcm-worklet.ts', import.meta.url))` does NOT work — Vite only special-cases
  that `new URL` literal inside `new Worker(...)`; passed to `addModule` it
  mis-sniffs `.ts` as `video/mp2t` and inlines the raw, untranspiled TS as a
  data URL, which throws when the worklet evaluates it. Use
  `import pcmWorkletUrl from './pcm-worklet.ts?worker&url'` instead (forces the
  worker-bundling/transpile pipeline; emits a real JS chunk). Already fixed.
- The worklet resampler must rebase its leftover-sample buffer against the last
  index actually used for interpolation, not the loop's overshooting cursor —
  the naive version drifts ~0.8% fast over a sustained capture. Already fixed;
  keep the `lastI0` rebasing if you touch `pcm-worklet.ts`.

## Known gotchas hit during Phase 3 (don't rediscover these)

- **Deepgram 7.4.0 TTS is `client.speak.v1.audio.generate(...)`**, an `async`
  method returning an `AsyncIterator[bytes]` of raw PCM — NOT the WebSocket TTS
  API and NOT any v3 `speak` surface. Verify the signature against the installed
  package (`uv run python -c "import inspect; from deepgram import
  AsyncDeepgramClient; print(inspect.signature(AsyncDeepgramClient(api_key='x')
  .speak.v1.audio.generate))"`), not from memory.
- **The audio contract is raw linear16 / 24000 Hz / mono / `container=none`** —
  no WAV header, just sample bytes. A binary WS frame boundary can split a 2-byte
  sample, so the frontend player carries a leftover odd byte across frames; the
  backend sends it via `ws.send_bytes` (binary), deliberately NOT through
  `emit()` (which stays the single JSON/event-sourcing seam). Keep both halves in
  lockstep if you touch either side.
- **The turn is a cancellable `asyncio.Task` (`self._turn_task`)** launched by
  `_commit_stt_turn` without being awaited, so `_consume_stt` can keep running to
  detect barge-in. Barge-in cancels that task; `_run_turn`'s `except
  CancelledError` branch is what cancels the agent producer and drains the shared
  `_tts_queue`. If you refactor the turn, preserve: (a) the producer is created
  INSIDE the `turn` span's `with` block (so `llm.request`/`tts.synthesize` nest
  under it — asyncio copies the OTel context into the task), and (b) the queue is
  fully drained through `_TURN_END` before the lock releases (else back-to-back
  turns cross-contaminate sentences).
- **Playback AudioContext autoplay:** the browser `AudioContext` starts
  `suspended` until a user gesture; `AudioPlayer` calls `resume()` lazily,
  relying on the mic-button click having already gestured. If playback is ever
  silent-until-second-turn, that's the thing to check.
