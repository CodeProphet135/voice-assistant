# Bug: echo guard silently drops user speech after playback has ended

**Status:** FIXED (2026-07-14). Verified with unit regression tests and a
real end-to-end run (synthesized speech streamed to the live backend over a
real WebSocket).

**Symptom:** the assistant asks a clarifying question ("Do you mean
association football, the game most people call soccer, or American
football?"). The user answers by (naturally) repeating some of the
assistant's own words — "Soccer.", "I mean soccer", "I mean association
football." None of these register. The live UI shows a stuck partial bubble
("I mean,") and the session sits in `listening`/`idle` forever with no
`stt_final`, even though the backend logs show Deepgram *did* hear the user
(`speech_started` fires repeatedly).

This is **not** the barge-in bug documented in
[`barge-in-debug-prompt.md`](barge-in-debug-prompt.md) /
[`bug-self-barge-in-echo.md`](bug-self-barge-in-echo.md) — those are about
the assistant failing to stop talking when interrupted *during* playback.
This bug happens well *after* `tts_end`, when the assistant is silent and
just waiting for the user's answer. Both bugs involve the same echo-guard
machinery, so don't conflate them.

## Root cause

`Session._echo_score` / `_is_echo` (`backend/src/voice_assistant/session.py`)
compare every incoming transcript against `_spoken_recent` — the assistant's
last ~20 spoken sentences — to detect our own TTS leaking back into the mic.
That comparison **never expired**. It ran on every transcript for as long as
`_spoken_recent` held matching text, regardless of how much real time had
passed since the assistant actually stopped making sound.

A user answering a clarifying question naturally reuses the assistant's own
words. Running the real scoring function against the reported session's
data confirmed every one of the user's answers scored at or above the 0.6
echo threshold and was silently dropped:

| User said | echo score | dropped? |
|---|---|---|
| "Soccer" | 1.00 | yes |
| "I mean soccer" | 0.67 | yes |
| "I mean," (interim) | 0.50 | no (shown, but its final was dropped) |
| "I mean association football" | 0.75 | yes |

The recorded event log for the failing session
(`5ccbd307-e359-4c07-9295-6fbd85690370`) showed the smoking gun: eleven
`speech_started` events after `tts_end` (Deepgram heard the user every
time), but only one surviving `stt_partial` and **zero** `stt_final` events
— every final transcript was being classified as echo and silently
discarded.

## The fix

Time-gate the echo guard to the window where echo can physically exist,
instead of letting it run forever based only on content overlap.

- `_note_tts_audio_sent()` tracks a **playback horizon**: the estimated
  monotonic time the browser will finish playing all TTS bytes sent so far.
  Computed from bytes ÷ 48,000 bytes/sec (linear16 @ 24kHz, the backend→
  browser TTS format) — necessary because Deepgram synthesizes faster than
  real time, so browser playback outlives both the send loop and `tts_end`.
- `_echo_possible()`: true while a turn is active (still synthesizing), or
  until `playback_horizon + _ECHO_GUARD_TAIL_S` (2s slack for mic-capture →
  Deepgram → transcript latency). Once past that, echo is physically
  impossible, so `_echo_score` isn't even consulted — the transcript is
  treated as genuine speech no matter what words it contains.
- `_barge_in()` resets the horizon to "now" on cancel, since `tts_cancel`
  tells the browser to flush its buffered audio (`player.ts:flush()`) —
  any horizon computed for now-discarded audio is void.

Behavior *during* playback (the 0.6 threshold, the fuzzy substring/word
matching, the 2-word strict tier) is untouched — this only changes when the
guard is consulted, not how it scores.

## Verification

- **Unit regression tests** — `backend/tests/test_session_echo_guard.py`,
  section 11 (`test_user_repeating_assistant_words_after_playback_commits`,
  `test_echo_final_during_lingering_playback_after_turn_is_dropped`):
  reproduces the exact live-session scenario (red before the fix, green
  after), plus confirms echo *during* lingering playback is still correctly
  dropped.
- Full suite green: `make test` (backend pytest + frontend typecheck),
  `make lint`.
- **Real end-to-end run** against the live backend (not just mocked tests):
  a throwaway harness synthesized real speech via the Deepgram TTS REST API
  and streamed it as raw 16kHz PCM mic frames over a real WebSocket
  connection to `ws://localhost:8000/ws`, mixed with silence frames to
  simulate an idle mic — exactly like a real browser. It asked the
  clarifying question, waited out the actual computed playback horizon, then
  spoke an answer reusing the assistant's own words. Result: `stt_partial` →
  `stt_final` → a fresh turn, exactly as expected.

## Known residual limitation

An answer given in the first ~1s after the assistant stops speaking may
still fall inside the genuine playback+tail window and could be classified
as echo if it heavily overlaps spoken content. This is inherent to the
approach (we can't distinguish "genuine but fast" from "actual echo" purely
from timing) and is a much narrower window than the original bug (unbounded
vs. ~1s). It's related to, but not fixed by, the separate open double-talk
barge-in investigation below.

## Related, still-open work

The double-talk barge-in investigation in
[`barge-in-debug-prompt.md`](barge-in-debug-prompt.md) is **unaffected by
this fix** and still open — it concerns the echo guard dropping genuine
interruptions *while the assistant is actively speaking* (H2) vs. the
browser's echo canceller removing the user's voice before Deepgram ever
transcribes it (H3). The temporary `BARGE DEBUG` logging in `_consume_stt`
should stay in place until that investigation concludes with a live repro;
don't remove it as part of unrelated cleanup.

## Key file : line map

| What | Location |
|---|---|
| Echo score / echo test | `backend/src/voice_assistant/session.py` (`_echo_score`, `_is_echo`) |
| Playback horizon tracking | `session.py` (`_note_tts_audio_sent`, `_playback_horizon`) |
| Time gate predicate | `session.py` (`_echo_possible`) |
| Call sites gated by the predicate | `session.py` (`_consume_stt`, interim + final transcript handling) |
| Horizon reset on cancel | `session.py` (`_barge_in`) |
| Constants | `session.py` (`_ECHO_THRESHOLD = 0.6`, `_TTS_BYTES_PER_SEC = 48_000`, `_ECHO_GUARD_TAIL_S = 2.0`) |
| Regression tests | `backend/tests/test_session_echo_guard.py` (section 11) |
| Frontend playback buffer flush (why horizon resets on barge-in) | `frontend/src/audio/player.ts` (`flush()`) |
