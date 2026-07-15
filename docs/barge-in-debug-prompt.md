# Barge-In Debugging Handoff Prompt

> Paste this whole file into a fresh Claude Code session. It contains the task
> **and** all the context from a prior investigation so you do **not** need to
> re-research the codebase. Read it fully, then start from the "Your task"
> section.

---

## Your task

A user reported that **barge-in fails**: while the voice assistant is speaking a
long answer, the user talks over it to interrupt, but the assistant **keeps
speaking the entire response** and only registers the interruption **after** it
finishes. We already root-caused *where* the signal is lost to two candidate
hypotheses (H2 and H3 below) but could not distinguish them from the recorded
event log, because the losing path was silent. **Diagnostic logging has already
been added** to make it observable. The user is about to reproduce the bug.

**Your job:** when the user pastes the `BARGE DEBUG` log lines from their
reproduction, read them, decide whether it's **H2** or **H3**, and implement the
correct fix. Then remove the temporary logging.

## Ground rules

- **Follow the `superpowers:systematic-debugging` skill.** Evidence before
  fixes. Do not propose or write a fix until the log lines tell you H2 vs H3.
- **Do not re-run the whole investigation.** The facts below are already
  confirmed. Verify a specific point only if a log line contradicts it.
- The diagnostic logging is **temporary** — plan to remove it once diagnosed
  (see "Cleanup").
- This is a solo portfolio project (see `CLAUDE.md`). Match existing style; keep
  the fix focused; no over-engineering.

---

## Reproduction (logging is already in place)

The user will:
1. Ensure the backend reloaded the logging change (it runs `uvicorn --reload`).
2. Start a fresh session, mic on.
3. Ask for a **long** answer (e.g. "Tell me about the Roman Empire in detail").
4. **While it speaks**, say a short interruption over it ("hey, stop" / "wait,
   hello").
5. Copy the `BARGE DEBUG` lines from the backend terminal (they show up as
   `[backend] ... BARGE DEBUG ...`).

The logging lives in `_consume_stt` in
`backend/src/voice_assistant/session.py`. Every transcript/VAD event during the
double-talk window now emits a `WARNING` line. The exact formats:

- **Interim dropped as echo** (`session.py:481`):
  `BARGE DEBUG interim DROPPED-echo turn_active=<bool> score=<f> words=<n> text=<repr>`
- **Interim shown / barged** (`session.py:497`):
  `BARGE DEBUG interim <BARGE|shown-no-barge> turn_active=<bool> score=<f> words=<n> text=<repr>`
- **Final segment** (`session.py:517`):
  `BARGE DEBUG final turn_active=<bool> speech_final=<bool> is_echo=<bool> score=<f> text=<repr>`
- **VAD fired during an active turn** (`session.py:539`):
  `BARGE DEBUG speech_started (VAD) during active turn`
- **UtteranceEnd fallback** (`session.py:544`):
  `BARGE DEBUG utterance_end turn_active=<bool> buffer=<repr>`

> **Note (2026-07-14):** a separate, unrelated bug was fixed in this same file
> since this doc was written — see
> [`bug-echo-guard-post-playback-drop.md`](bug-echo-guard-post-playback-drop.md).
> It added `_playback_horizon` / `_echo_possible()` and now gates both
> `_echo_score` call sites in `_consume_stt` behind
> `if self._echo_possible() else 0.0`. This does **not** change the double-talk
> diagnosis below — `_echo_possible()` is always `True` while a turn is active,
> so the H2/H3 logic and log formats are unaffected — but the surrounding code
> will look different from a literal reading of this doc. Line numbers
> throughout this doc were refreshed on the same date to match; re-verify them
> if `session.py` changes again.

## How to read the logs → diagnosis

Look only at the lines emitted **while the user was speaking over the assistant**
(`turn_active=True`).

| Pattern during the barge attempt | Diagnosis | Fix branch |
|---|---|---|
| `interim BARGE ... text='hey stop'` appears | It **did** barge — the failure is **intermittent**, not total. Investigate timing/threshold near the boundary. | Gate tuning |
| `interim DROPPED-echo ... score>=0.60 text='<your words>'` | **H2** — Deepgram transcribed the user, but the mix scored as echo and **our echo guard dropped it**. | **H2 fix (our code)** |
| `final ... is_echo=True ... text='<your words>'` | **H2** — same, but on the final path. | **H2 fix (our code)** |
| `speech_started (VAD) during active turn` present, but **no** interim/final line carries the user's words | **H3** — Deepgram's VAD heard *something*, but no usable transcript of the user existed to act on; the **browser's echo canceller removed the user's voice** before Deepgram could transcribe it. | **H3 fix (acoustic)** |
| **Nothing at all** during the barge (no VAD, no interim) | Audio isn't reaching Deepgram during playback. Re-check the send path (should be ruled out — see Confirmed facts). | Investigate transport |

Common real-world outcome to expect: a short interruption ("hey stop") over a
loud answer gets **dominated by the assistant's echo**, so Deepgram transcribes
mostly the assistant's words → high echo score → dropped. That surfaces as **H2**
(dropped-echo lines carrying garbled/assistant-like text) and/or **H3** (VAD but
no clean transcript). The prior long, distinctive interruptions ("Actually,
forget about that, tell me ten sentences about science") succeeded precisely
because enough *novel* words punched through.

---

## The two fix branches

### H2 — our echo guard over-drops genuine barges during double-talk

Root problem: `_is_echo` / `_echo_score` (`session.py:298`, `:336`) drop **any**
interim whose word-overlap with recently-spoken TTS reaches
`_ECHO_THRESHOLD = 0.6`. During double-talk the mic captures *assistant echo +
user voice*, so Deepgram's transcript is a blend that can exceed 0.6 even though
it contains the user's genuine interruption. The guard can't tell "pure echo"
from "echo with a real barge mixed in."

Fix directions to weigh (pick based on what the logs actually show):
- **Detect novel content, not just overlap.** An echo transcript matches a
  *contiguous span* of `_spoken_recent`; a real barge introduces words that are
  **not** in what we just said. Consider barging when the transcript contains
  ≥N words absent from `_spoken_recent`, even if overall overlap is high.
- **Asymmetric test.** Pure echo is a *subset* of spoken text (high precision
  against spoken content). A barge adds unexplained words. Score "fraction of the
  transcript NOT explained by recent speech" and barge when that fraction clears
  a bar.
- Do **not** just raise/lower the single 0.6 threshold blindly — that trades the
  self-barge-in-echo bug (see `docs/bug-self-barge-in-echo.md`) back in. The
  existing thresholds were tuned against real echo runs; preserve that.

### H3 — the browser's echo canceller removes the user's voice

Root problem: mic capture uses `echoCancellation: true` + `noiseSuppression:
true` (`frontend/src/audio/mic.ts:34-35`). During loud playback these can
suppress a quieter near-end (user) voice, so Deepgram never receives an
intelligible transcript to act on. This is **acoustic**, outside our transcript
logic.

Fix directions to weigh:
- **Duck the assistant while the user speaks.** On a speech signal during
  playback, briefly lower TTS playback gain so the user's voice dominates the mic
  and transcribes cleanly, then let the normal transcript-based barge fire.
  Requires coordinating frontend playback gain (`frontend/src/audio/player.ts`)
  with a backend signal. **Challenge:** Deepgram VAD (`SpeechStarted`) fires on
  the assistant's *own* echo constantly during playback (that's why VAD is
  currently ignored for barge — `session.py` `SpeechStarted` handler), so a naive
  "duck on VAD" ducks on echo. You'll need a gate (e.g. duck briefly, confirm a
  non-echo transcript follows, else un-duck).
- **Reconsider `noiseSuppression`/`echoCancellation` settings** for the capture
  stream — but note removing echoCancellation reintroduces heavy self-echo into
  Deepgram. Measure, don't assume.
- Respect the `CLAUDE.md` rule "turn detection is Deepgram's job — no custom
  VAD." Ducking is an *acoustic* aid, not a turn detector; frame it that way.

---

## Crucial context (already established — trust this)

### System overview
Real-time voice assistant. Browser mic (AudioWorklet → 16kHz mono Int16 PCM) →
WebSocket → FastAPI backend. Backend: Deepgram STT (streaming) → OpenAI Responses
API agent loop → Deepgram TTS (Aura REST, per sentence) → browser speaker. Every
server→client event flows through `Session.emit()` and is persisted append-only
(event sourcing); the frontend live UI and the Replay view are the *same* pure
reducer over that event stream (`frontend/src/state.ts`).

### How barge-in works today
`Session._consume_stt` (`backend/src/voice_assistant/session.py:452`) drains
normalized STT events (`Transcript`, `SpeechStarted`, `UtteranceEnd`,
`SttClosed`) and decides:

- **Interim** (`Transcript.is_final == False`):
  - Compute `score = _echo_score(text)`.
  - If `score >= _ECHO_THRESHOLD` (0.6) → **drop silently** (assumed echo). This
    is the path that was invisible before the new logging.
  - Else **barge** iff a turn is active AND
    (`words >= _MIN_BARGE_IN_WORDS` (3) OR (`words == 2` AND
    `score < _TWO_WORD_BARGE_MAX_SCORE` (0.4))). Then emit `stt_partial`.
  - **1-word interims never barge** (deliberate — too weak / noise-prone).
- **Final** (`is_final == True`): append to buffer if not echo; on
  `speech_final` → `_commit_stt_turn` (may barge + commit).
- **SpeechStarted (VAD):** emitted only when **no** turn is active — VAD fires on
  the assistant's own TTS echo during speaking, so it is intentionally **not** a
  barge trigger. Transcript content owns barge detection.
- **UtteranceEnd:** fallback commit when `speech_final` never fires.

`_barge_in` (`session.py:386`): cancels the in-flight turn task, drains the TTS
queue, emits `tts_cancel` + `state=listening`. (Since 2026-07-14 it also resets
`_playback_horizon` to now — see the note above; not relevant to H2/H3.)

### Key constants
- `session.py`: `_MIN_BARGE_IN_WORDS = 3` (:79), `_ECHO_THRESHOLD = 0.6` (:86),
  `_TWO_WORD_BARGE_MAX_SCORE = 0.4` (:94).
- `providers/deepgram.py`: `_ENDPOINTING_MS = 300` (:35),
  `_UTTERANCE_END_MS = 1000` (:36). Model `nova-3`, `encoding=linear16`,
  `sample_rate=16000`, `vad_events=true`, `interim_results=true`.
- Deepgram `endpointing=300` = ms of **acoustic silence** before it sends a
  final with `speech_final=true` (primary turn-end). `utterance_end_ms=1000` = ms
  gap between **word timestamps** before a separate `UtteranceEnd` (backstop when
  acoustic endpointing misses, e.g. noisy rooms).

### Confirmed facts (do not re-verify unless a log contradicts)
1. **Mic audio flows during playback.** Frames are sent unconditionally while the
   mic is on: `MicButton.tsx:40` → `client.sendAudio` (`frontend/src/ws.ts`,
   only checks socket open). There is **no** mic gate during TTS. ⇒ "mic muted
   during playback" is ruled out.
2. **The failing turn never barged.** In the reported session the long answer
   ended with a natural `tts_end`, **not** `tts_cancel` — the backend never even
   attempted to stop. Zero `stt_partial` events during its ~14.5s playback.
3. **Echo-dropped interims were logged nowhere** before this change — that was
   the observability gap. The new `BARGE DEBUG` logging closes it.
4. **Longer interruptions succeeded** in the same session (three earlier turns
   barged on "Actually, forget about that, tell me about X"); the short "Hey.
   Hello." did not. Length/distinctiveness of the interruption is the
   differentiator.

### The reported session
`http://localhost:5173/sessions/53f7471b-8d17-4461-8605-b3d946080ebc`
Fetch its raw event log any time with:
```bash
curl -s "http://localhost:8000/api/sessions/53f7471b-8d17-4461-8605-b3d946080ebc/events" | python3 -m json.tool | less
```
The failing turn is `turn_id` prefix `a2c504c9` ("What all can you do?"), which
ends in `tts_end` with no `stt_partial` during playback. The user's "Hey. Hello."
only committed as the *next* turn (`4a6c4fd3`), ~9s after the answer ended.
(Note: recorded events cannot show the dropped interims — that's why live repro
with the new logging is required.)

### Key file : line map
| What | Location |
|---|---|
| STT consumer / barge decision | `backend/src/voice_assistant/session.py:452` (`_consume_stt`) |
| Echo score / echo test | `session.py:322` (`_echo_score`), `:360` (`_is_echo`) |
| Barge teardown | `session.py:386` (`_barge_in`) |
| Commit turn (+ barge on mid-turn final) | `session.py:571` (`_commit_stt_turn`) |
| Barge/echo constants | `session.py:79, 86, 94` |
| Playback-horizon echo gate (2026-07-14, unrelated bug) | `session.py` `_playback_horizon`, `_note_tts_audio_sent`, `_echo_possible` |
| Deepgram STT config | `backend/src/voice_assistant/providers/deepgram.py:35-36, 84-87` |
| Deepgram msg → normalized event | `providers/deepgram.py:137` (`_normalize`) |
| Normalized event types | `backend/src/voice_assistant/providers/base.py` (`Transcript` has `is_final`, `speech_final`) |
| Mic capture (echoCancellation/noiseSuppression) | `frontend/src/audio/mic.ts:31-58` |
| Mic → WS send (unconditional) | `frontend/src/components/MicButton.tsx:40`, `frontend/src/ws.ts` (`sendAudio`) |
| TTS playback (gain lives here for ducking) | `frontend/src/audio/player.ts` |
| Reducer (frontend + replay) | `frontend/src/state.ts` (`tts_cancel` closes the assistant bubble) |
| Prior echo investigation | `docs/bug-self-barge-in-echo.md` |

### Non-negotiable conventions (from `CLAUDE.md`) that constrain any fix
- **Turn detection is Deepgram's job — no custom VAD.** Keep
  `endpointing=300`, `utterance_end_ms=1000`, `vad_events=true`. An acoustic duck
  is allowed; a home-grown turn detector is not.
- OpenAI **Responses API** only (`store=False`, app owns the input-item list).
- Tool loop is manual; chunker is a pure sync class. Not relevant to this bug but
  don't disturb.
- Deepgram is the only provider; the provider seam is a thin `Protocol` — don't
  add a second provider.
- Frontend has no state library; UI is a `useReducer` over the event stream. Any
  new frontend signal (e.g. a duck command) must flow through the existing
  ws/event plumbing, and the reducer must stay pure (it's reused by Replay).

---

## Secondary issue — DO NOT conflate with this bug

Separately, a **lone filler barge that commits as its own turn** was found: when
Deepgram finalizes just "Actually," (`speech_final`) mid-turn, `_commit_stt_turn`
both barges AND commits that one word as a full user turn, producing a spurious
"You started to say something…" reply. A design was drafted for it (decouple
"stop" from "respond"; a ~1000ms post-barge settle window that coalesces
fragments and discards single-word barges). **That is a different problem** from
"barge-in never fired." Do not merge the two. If you want to address it, do it as
a separate change after this one is fixed and verified.

---

## Useful commands
```bash
# Focused tests (must stay green after any fix):
cd backend && uv run pytest tests/test_session_barge_in.py \
  tests/test_session_echo_guard.py tests/test_session_stt.py -q

# Full suite + typecheck + lint:
make test
make lint

# Fetch a session's raw event log:
curl -s "http://localhost:8000/api/sessions/<SESSION_ID>/events" | python3 -m json.tool
```

## Cleanup (after diagnosis)
The `BARGE DEBUG` lines are temporary instrumentation in `_consume_stt`
(`session.py`, five `_logger.warning("BARGE DEBUG ...")` sites at approx
`:481, :497, :517, :539, :544`). Once the root cause is confirmed and the real
fix is in, **remove them** (or gate behind a debug flag if you want to keep them
around) so the code path returns to silent-drop behavior. Re-run the focused
tests after removal.

## Definition of done
- Log lines classified as H2 or H3 with the reasoning stated.
- A single, focused fix implemented against that root cause.
- A regression test added that reproduces the failure at the unit level
  (e.g. a double-talk transcript that should barge but currently doesn't).
- `make test` and `make lint` green.
- Temporary `BARGE DEBUG` logging removed.
- Fix verified end-to-end (drive the real app; confirm the interruption now
  stops the assistant), not just via tests.
