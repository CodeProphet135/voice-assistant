#!/usr/bin/env python3
"""Browser-mic simulator for debugging the cut-off-TTS issue.

Mimics what the real frontend does, unlike ws_client.py --wav:
  - sends `start`, streams the question WAV in 30ms chunks,
  - then KEEPS the mic open, streaming continuous silence frames
    (the browser worklet never stops sending while the mic is on),
  - concurrently receives events + binary TTS frames,
  - optionally (--echo) downsamples received TTS audio 24k->16k and feeds
    it back as mic input, simulating speaker->mic echo with no AEC,
  - optionally (--interrupt-wav) streams a second spoken WAV as mic input
    once TTS playback starts, simulating a GENUINE user barge-in (use this
    to verify legitimate interruption still works after an echo fix).

Exits after tts_end + state event, or on socket close / timeout.
"""

import argparse
import array
import asyncio
import json
import sys
import wave
from datetime import datetime

import websockets

CHUNK_MS = 30
SR = 16000
BYTES_PER_CHUNK = SR * 2 * CHUNK_MS // 1000  # 960


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000/ws")
    ap.add_argument("--wav", required=True)
    ap.add_argument("--echo", action="store_true", help="feed TTS audio back into the mic")
    ap.add_argument(
        "--interrupt-wav",
        help="16kHz mono s16 WAV streamed as mic input once TTS starts (genuine barge-in)",
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    with wave.open(args.wav, "rb") as w:
        question_pcm = w.readframes(w.getnframes())

    interrupt_pcm = b""
    if args.interrupt_wav:
        with wave.open(args.interrupt_wav, "rb") as w:
            interrupt_pcm = w.readframes(w.getnframes())

    tts_started = asyncio.Event()  # set on first binary TTS frame
    echo_buf = bytearray()  # 16k PCM awaiting playback into the mic
    audio_frames = 0
    audio_bytes = 0
    done = asyncio.Event()

    async with websockets.connect(args.url, max_size=None) as ws:

        async def mic_pump() -> None:
            """Stream question, then silence (or echo / an interruption)
            until done."""
            offset = 0
            int_offset = 0
            while not done.is_set():
                if offset < len(question_pcm):
                    chunk = question_pcm[offset : offset + BYTES_PER_CHUNK]
                    offset += BYTES_PER_CHUNK
                elif interrupt_pcm and tts_started.is_set() and int_offset < len(interrupt_pcm):
                    if int_offset == 0:
                        log("streaming interruption utterance into the mic")
                    chunk = interrupt_pcm[int_offset : int_offset + BYTES_PER_CHUNK]
                    int_offset += BYTES_PER_CHUNK
                elif args.echo and echo_buf:
                    n = min(BYTES_PER_CHUNK, len(echo_buf))
                    chunk = bytes(echo_buf[:n])
                    del echo_buf[:n]
                    if len(chunk) < BYTES_PER_CHUNK:
                        chunk += b"\x00" * (BYTES_PER_CHUNK - len(chunk))
                else:
                    chunk = b"\x00" * BYTES_PER_CHUNK
                try:
                    await ws.send(chunk)
                except Exception as exc:
                    log(f"MIC SEND FAILED: {type(exc).__name__}: {exc}")
                    return
                await asyncio.sleep(CHUNK_MS / 1000)

        async def receiver() -> None:
            nonlocal audio_frames, audio_bytes
            tts_ended = False
            while True:
                try:
                    raw = await ws.recv()
                except Exception as exc:
                    log(f"SOCKET CLOSED while receiving: {type(exc).__name__}: {exc}")
                    done.set()
                    return
                if isinstance(raw, bytes):
                    audio_frames += 1
                    audio_bytes += len(raw)
                    if audio_frames == 1:
                        log(f"first binary TTS frame ({len(raw)} bytes)")
                        tts_started.set()
                    if args.echo:
                        # 24k mono s16 -> 16k mono s16 (nearest-neighbor, fine for VAD)
                        samples = array.array("h", raw[: len(raw) - (len(raw) % 2)])
                        out = array.array(
                            "h", (samples[i * 3 // 2] for i in range(len(samples) * 2 // 3))
                        )
                        echo_buf.extend(out.tobytes())
                    continue
                event = json.loads(raw)
                etype = event.get("type")
                log(f"{etype}: {json.dumps(event)[:200]}")
                if etype == "tts_end":
                    tts_ended = True
                    log(
                        f"TTS COMPLETE: {audio_frames} frames, {audio_bytes} bytes "
                        f"(~{audio_bytes / (24000 * 2):.2f}s of 24k audio)"
                    )
                if etype == "tts_cancel":
                    log(
                        f"TTS CANCELLED after {audio_frames} frames, {audio_bytes} bytes "
                        f"(~{audio_bytes / (24000 * 2):.2f}s of 24k audio)"
                    )
                # after tts ends/cancels, hang around 5s to observe follow-on turns
                if tts_ended and etype == "state" and event.get("state") in ("listening", "idle"):
                    await asyncio.sleep(5)
                    done.set()
                    return

        # wait for ready
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            log(f"{event.get('type')}: {json.dumps(event)[:120]}")
            if event.get("type") == "ready":
                break

        await ws.send(json.dumps({"type": "start", "sample_rate": SR}))
        pump = asyncio.create_task(mic_pump())
        try:
            await asyncio.wait_for(receiver(), timeout=args.timeout)
        except TimeoutError:
            log(f"TIMEOUT after {args.timeout}s: {audio_frames} frames, {audio_bytes} bytes")
        done.set()
        pump.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
