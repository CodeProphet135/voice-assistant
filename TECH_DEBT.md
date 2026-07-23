# Known Limitations & Future Work

A deliberate ledger of what this project does *not* do yet, and why. Nothing
here blocks running the app — each item is a conscious trade-off made to keep a
solo, phased build focused. It's kept honest and current so the gaps are known
rather than discovered.

## Correctness

- **A replayed text-only turn shows no user bubble.** Typed turns run the agent
  but don't emit a recorded user-message event, so the Replay view has nothing
  to render on the "you" side (voice turns emit `stt_final` and replay fine).
  The fix is small: emit a recorded user-utterance event shaped like
  `stt_final` so the existing reducer picks it up — no new event type needed.

- **The echo guard has a ~1 second residual window.** While the assistant is
  speaking, its own TTS can leak back through the mic. A word-overlap score
  combined with a time gate anchored to browser-reported playback end screens
  that echo out, but an answer that heavily reuses the assistant's own words
  *within ~1s* of playback ending can still be misclassified. This is inherent
  to timing-based gating; closing it fully calls for content-aware scoring
  (e.g. barging only on genuinely novel words).

- **A same-tick stop can settle the UI to `idle` instead of `listening`.** A
  narrow lock-acquisition ordering in the STT commit path — cosmetic and
  corrected on the next state transition.

## Reliability

- **A permanently dropped event batch has no backfill.** The recorder retries
  transient write failures with backoff, and self-disables then periodically
  re-probes after repeated failures; the read side detects and flags `seq` gaps
  in the Event Inspector. But a batch still lost after every retry is gone —
  there's no re-synthesis or operator alert beyond the log line and the UI
  banner. Acceptable because recording is best-effort by design and never
  blocks the live conversation.

- **Constructing a `Session` requires an OpenAI key up front.** The OpenAI
  client is built eagerly in `Session.__init__`, so with no key in settings or
  the environment the SDK raises there rather than failing lazily on first use.
  A factory-seam client (like the STT/TTS providers already use) would give a
  cleaner error path.

## Developer experience

- **`start.sh` reports "ready" after a fixed wait.** It prints the ready URLs
  after a short sleep without confirming each dev server actually came up, so a
  server that died on boot (bad import, port already taken) can still be
  announced as ready. Capturing each background PID and liveness-checking it
  before printing would fix it.

- **`start.sh` has a few shell-robustness rough edges** on non-interactive or
  redirected invocation: `read` under `set -e` on closed stdin, `sed` key
  substitution that isn't metacharacter-safe, and raw color escapes when output
  is piped to a file.

## Unused / half-wired

- **`StartEvent.sample_rate` is accepted but ignored** — the STT provider
  hardcodes 16 kHz regardless of what the client advertises.

## Test coverage

- **No headless browser/mic end-to-end test.** The mic-capture and
  speaker-playback paths (including barge-in) are verified manually in a real
  browser; there's no automated coverage for that surface.

## Future features

- **Built-in `web_search` tool.** OpenAI's hosted `web_search` tool isn't in
  the registry yet — a natural next tool to wire in.
