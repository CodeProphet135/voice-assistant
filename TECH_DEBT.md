# Tech Debt

Known gaps and non-blocking issues, extracted from the project's session
history. Nothing here blocks running the app; fix opportunistically.

## Features
- **`web_search` built-in tool isn't wired up.** Deferred since Phase 4 —
  OpenAI's built-in web_search tool was never added to the tool registry.

## Correctness
- **Replaying a text-input turn shows no "You" bubble.**
  `Session._handle_text_input` runs the turn but never `emit()`s a
  user-message event, unlike voice turns (which emit `stt_final`). A fix
  would emit a recorded user-utterance event shaped like `stt_final` so the
  existing reducer picks it up — no new event type needed.
- **Echo guard can still eat a very fast overlapping reply.** The unbounded
  version of this bug (genuine user speech dropped as echo long after
  playback) is fixed — `_spoken_recent` clears per turn and the guard is
  time-gated to the playback horizon + 2s tail (commit `9000460`; the full
  incident write-ups were removed from `docs/` but live in git history at
  that commit). Residual: an answer that heavily reuses the assistant's
  words *within ~2s* of playback end can still be misclassified; inherent
  to timing-based gating.
- **Double-talk barge-in is still open.** Interrupting *while* the
  assistant is speaking can fail: the mic picks up TTS echo mixed with the
  user's voice, so either the blended transcript scores as echo and our
  guard drops it (H2 — fix in our scoring, e.g. barge on novel words) or
  the browser's echo canceller suppresses the user's voice before Deepgram
  ever hears it (H3 — acoustic fix, e.g. duck playback). Temporary
  `BARGE DEBUG` warning logs in `_consume_stt` (session.py) distinguish the
  two from a live repro: `DROPPED-echo` lines carrying the user's words ⇒
  H2; VAD lines with no transcript of the user ⇒ H3. Keep the logging until
  diagnosed; the full handoff doc is in git history at `9000460`
  (`docs/barge-in-debug-prompt.md`).
- **Minor lock-acquisition race in `_commit_stt_turn`.** On a same-tick
  `stop`, state can settle to `idle` instead of `listening`.
- **Text-input path isn't under `_turn_lock`.** Only the STT-commit path is
  serialized; typing and speaking at the same instant could race
  `input_items`. Low-probability, not yet hit in practice.
- **A dropped event batch is detected but not recovered.** `EventRecorder`
  now retries transient write failures and self-disables + re-probes after
  repeated failures (see `events.py`), and `list_events` logs a warning and
  the Event Inspector shows a banner when persisted `seq` values have a gap
  (`gaps.py` / `gaps.ts`). But a batch that is still permanently lost after
  all retries has no recovery path — there's no re-synthesis, backfill, or
  operator alert beyond the log line and the UI banner.
- **start.sh** prints the green "ready" URLs after a blind `sleep 2`, even if backend/frontend crashed immediately (bad import, port already taken) — the user sees "ready" for a dead server. Fix by capturing each job's PID and checking it's still alive before printing ready
-  **Minor issues with start.sh**: (1) `read` under set -e aborts ungracefully on closed stdin/non-interactive invocation, (2) sed key substitution isn't metacharacter-safe for keys containing |, &, \, (3) color codes print raw escape sequences when output is redirected to a file.

## Robustness
- **`Session.__init__` builds the OpenAI client eagerly**, so constructing a
  bare `Session()` with no `OPENAI_API_KEY` raises immediately instead of
  failing lazily on first use (or with a clearer error).

## Unused / half-plumbed
- **`StartEvent.sample_rate` is accepted but ignored** — `DeepgramSTT`
  hardcodes 16kHz regardless of what the client sends.

## Test coverage
- **No automated browser/mic end-to-end test.** Every phase's mic-capture
  and speaker-playback path (including barge-in) was verified manually by a
  human at a real browser; there's no headless coverage for that surface.

## Docs / assets
- **README is missing the Jaeger waterfall screenshot and demo GIF** —
  needs a human at a real browser with a live session to capture.
