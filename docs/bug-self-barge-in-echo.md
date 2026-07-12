# Bug: TTS cuts off mid-reply — assistant barges in on itself (speaker echo)

**Status:** diagnosed, not yet fixed (2026-07-11)
**Symptom:** during the manual browser mic test, the assistant starts speaking
but the audio stops after ~1 second and never finishes the reply. The vite
terminal shows `ws proxy error: Error: write EPIPE` around the same time.

## Root cause (reproduced deterministically)

The assistant's own TTS audio leaks from the speakers into the open mic.
Deepgram hears it as new user speech and the barge-in path in
`backend/src/voice_assistant/session.py` (`_consume_stt` → `_barge_in()`)
cancels the in-flight turn — the assistant interrupts itself.

It's worse than a one-off cutoff: the echo also gets **transcribed and
committed as a user turn**, so the assistant starts replying to its own words
in a loop. Reproduced session log (echo simulation, real Deepgram + OpenAI):

```
tts_start #0  "Here's a fun one: Roman concrete was so durable because..."
  +0.55s   tts_cancel                      (after ~1.2s of audio)   ← echo barged in
stt_final  "Here's a fun one."             ← its own voice, committed as user turn
tts_start #0  "Nice—hit me with it!"       ← replying to itself
  +0.52s   tts_cancel
stt_final  "Nice hit."
tts_start #0  "Thanks — glad you liked it!"
  +0.54s   tts_cancel
... (loop continues)
```

### Why `echoCancellation: true` doesn't save us

`frontend/src/audio/mic.ts` requests `echoCancellation: true`, but Chrome's
AEC only uses WebRTC / `<audio>`-element playback as its reference signal.
Audio played through the **Web Audio API** (`AudioBufferSourceNode`, which is
exactly what `frontend/src/audio/player.ts` uses for gapless PCM playback) is
**not included in the AEC reference** — long-standing Chromium issue
[687574](https://issues.chromium.org/issues/40488062). With speakers instead
of headphones, the assistant's voice reaches Deepgram nearly unattenuated.

## How it was reproduced / ruled out

A browser-identical mic simulator (start → stream a spoken question WAV →
keep the mic streaming while receiving TTS) was driven through the vite proxy
against live Deepgram + OpenAI:

1. **Mic streams silence during playback** → reply completes perfectly
   (2 sentences, `tts_end`, ~14.5s audio, no errors). Pipeline is healthy.
2. **Mic streams the received TTS audio back (echo)** → cutoff + self-reply
   loop, 100% of turns. This is the bug.

So the backend, Deepgram integration, chunker, and player are all fine — the
trigger is purely acoustic feedback plus a hair-trigger barge-in.

## The EPIPE is a separate, secondary symptom

`write EPIPE` in the vite ws proxy means vite wrote mic frames into the
backend WS socket after the backend side had closed. Tested triggers:

- Barge-in / self-interruption: does **not** produce it (socket stays open).
- Browser tab dying abruptly mid-TTS: does **not** produce it.
- Backend process going away mid-session (e.g. uvicorn `--reload` restart,
  crash, or Ctrl-C) while the mic keeps streaming: **reproduces** this error
  class (`read ECONNRESET` / `write EPIPE` depending on timing).

So the EPIPE just marks the moment the backend process restarted or was
stopped while the browser mic was still on — teardown noise, not the cause of
the cutoff. (If a backend traceback appeared in the uvicorn terminal at that
timestamp, that would be worth a separate look; none was reproduced here.)

## Quick confirmation

Repeat the browser test wearing **headphones** — self-barge-in should vanish
and replies should play to completion.

## Fix options (rough order of value)

1. **Make barge-in less hair-triggered while `speaking`** (backend-only,
   easily unit-tested): ignore `SpeechStarted` (Deepgram VAD fires on any
   noise) and require an interim transcript with some substance (e.g. ≥2
   words) before cancelling the turn.
2. **Echo-transcript guard** (backend-only): if an interim/final transcript
   closely matches the sentence currently being synthesized (the text is
   known — it's on the TTS queue / in `TtsStartEvent`), it's our own voice:
   drop it instead of barging in or committing it as a user turn. This also
   kills the talking-to-itself loop.
3. **Route TTS playback through a path Chrome's AEC can see** (frontend, the
   proper fix for the acoustic layer): pipe the player's output through
   `AudioContext.createMediaStreamDestination()` into an `<audio>` element —
   the standard workaround for Chromium issue 687574. Needs a human mic test
   to verify.

Options 1+2 make barge-in robust regardless of the user's audio setup;
option 3 attacks the echo itself. They compose.

## Reproduction (scripted — no mic or browser needed)

[`scripts/mic_sim.py`](../scripts/mic_sim.py) mimics the browser mic session
exactly: sends `start`, streams a spoken WAV in 30ms 16kHz s16 mono chunks,
then **keeps the mic streaming** (silence) while receiving TTS — with two
optional perturbations: `--echo` feeds received TTS audio back into the mic
(24k→16k) simulating speaker→mic echo, and `--interrupt-wav` streams a second
utterance once TTS starts, simulating a genuine user barge-in.

Prerequisites: real `OPENAI_API_KEY` + `DEEPGRAM_API_KEY` in `backend/.env`,
backend running (`make dev-backend`; going through the vite proxy at
`ws://localhost:5173/ws` works too but isn't needed).

```bash
# 1. Generate the spoken inputs (no trailing-silence padding needed — unlike
#    ws_client.py, mic_sim keeps streaming silence after the WAV ends, which
#    is what lets Deepgram endpointing fire).
say -o /tmp/q.aiff "Tell me a fun fact about the Roman Empire."
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/q.aiff /tmp/q16.wav
say -o /tmp/i.aiff "Wait, stop. What is your name?"
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/i.aiff /tmp/i16.wav

cd backend

# 2. Baseline (healthy): open mic streams silence during playback.
uv run python ../scripts/mic_sim.py --wav /tmp/q16.wav

# 3. THE BUG: echo the TTS audio back into the mic.
uv run python ../scripts/mic_sim.py --wav /tmp/q16.wav --echo

# 4. Genuine barge-in (must keep working after any fix).
uv run python ../scripts/mic_sim.py --wav /tmp/q16.wav --interrupt-wav /tmp/i16.wav
```

Observed on the buggy code (2026-07-11, live Deepgram + OpenAI):

- **Baseline**: reply plays to completion — `tts_start` per sentence, all
  frames, `TTS COMPLETE ... ~14.5s`, `tts_end`, state `listening`. No cancel.
- **Echo**: `tts_cancel` ~0.5–0.8s after the first binary frame, **every
  turn**, followed by the echo committing as a user turn (`stt_final:
  "Here's a fun one."`) and the assistant replying to itself in a loop.
- **Interrupt**: `tts_cancel` ~0.4s after the interruption starts (fires on
  Deepgram's `SpeechStarted`, i.e. before any partial arrives), then
  `stt_final: "Wait. Stop. What is your name?"` and a fresh reply that plays
  to completion.

## Verification after a fix

Scripted (each run prints per-event timestamps; grep out `assistant_delta`
for readability):

1. **Baseline run** must be unchanged: completes with `TTS COMPLETE` +
   `tts_end`, zero `tts_cancel`.
2. **Echo run** is the acceptance test: expect **no `tts_cancel`**, `TTS
   COMPLETE` + `tts_end`, and — critically — **no self-committed turn** (no
   `stt_final` containing the assistant's own words, no second unprompted
   `tts_start` afterwards). The script idles ~5s after `tts_end` precisely to
   catch a late self-reply.
3. **Interrupt run** guards against over-correcting: barge-in on a genuine
   interruption must still fire (a `tts_cancel`, then `stt_final` with the
   interruption text, then a completed fresh reply). Note the current cancel
   latency baseline is ~0.4s via `SpeechStarted`; if the fix drops
   `SpeechStarted` in favor of substantial interims (fix option 1), expect
   ~1.0–1.5s instead — confirm it stays comfortably under the ~2s where it
   would feel broken to a user.
4. `cd backend && uv run pytest -q` — the barge-in unit tests
   (`test_session_barge_in.py`) must stay green, plus whatever new tests the
   fix adds.

Human check (the only part that needs a real mic): run `make dev-backend` +
`make dev-frontend`, click 🎤, **use built-in speakers at normal volume**
(headphones would mask the bug), let the assistant answer something long —
it must finish speaking. Then actually talk over it — it must stop quickly
and answer the interruption. If fix option 3 (AEC-visible playback) is
implemented, repeat with external speakers turned up.
