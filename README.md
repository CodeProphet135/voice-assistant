# Voice Assistant

A real-time, tool-using voice assistant. Talk to it in the browser; it streams your
speech to text, reasons with an LLM (calling tools like weather lookups, web search,
timers, and notes along the way), and speaks its answer back — all over a single
WebSocket, sentence by sentence, with sub-2-second voice-to-voice latency.

> 🚧 Under active development. See [Roadmap](#roadmap) for build status.

![CI](https://github.com/OWNER/voice-assistant/actions/workflows/ci.yml/badge.svg)

<!-- ![demo](docs/demo.gif) -->

## Architecture

<!-- TODO(Phase 5): Mermaid diagram — browser ⇄ WebSocket ⇄ session orchestrator →
     Deepgram STT / OpenAI agent loop / Deepgram TTS, plus Postgres + OpenTelemetry. -->

```
Browser (mic + speaker)
   │  WebSocket (binary PCM + JSON events)
   ▼
FastAPI session orchestrator
   ├─ Deepgram STT  (streaming transcription, endpointing)
   ├─ OpenAI agent  (streaming tool-use loop, Responses API)
   ├─ Deepgram TTS  (sentence-chunked speech synthesis)
   └─ Postgres      (notes, session/event log)
```

## Design Decisions

<!-- TODO(Phase 5): why AudioWorklet over MediaRecorder, why sentence-chunked TTS,
     why a manual streaming tool loop, the provider seam, barge-in approach,
     event sourcing → replay. -->

## Quickstart

```bash
cp .env.example .env   # fill in OPENAI_API_KEY and DEEPGRAM_API_KEY
make db-up              # start Postgres (and Jaeger, with --profile observability)
make migrate
make dev-backend         # FastAPI on :8000
make dev-frontend        # Vite on :5173
```

## Testing

```bash
make test       # backend pytest suite (mocked externals, no API keys required)
make lint       # ruff + tsc
```

## Roadmap

- [x] Phase 0 — Scaffold
- [x] Phase 1 — Text chat loop end-to-end
- [x] Phase 2 — Voice in (STT)
- [x] Phase 3 — Voice out + barge-in
- [ ] Phase 4 — Tools
- [ ] Phase 5 — Polish
- [ ] Phase 6 — Event Timeline + Replay

## License

MIT — see [LICENSE](LICENSE).
