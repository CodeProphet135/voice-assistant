#!/usr/bin/env python3
"""CLI WebSocket test client for the voice-assistant backend.

Two mutually exclusive modes:

``--text "hello"``
    Connects, waits for the ``ready`` frame, sends a single ``text_input``
    frame, then prints every event received (each line timestamped) until an
    ``assistant_done`` event arrives, at which point it disconnects and exits.

``--wav path/to/file.wav``
    Connects, waits for ``ready``, sends a ``start`` frame, then streams the
    WAV file's PCM as binary frames in ~20-40ms chunks (with a small sleep
    between chunks to mimic real-time mic capture), sends a ``stop`` frame,
    and prints every event (including ``stt_partial``/``stt_final``) until
    ``assistant_done`` arrives.

Usage (run from the ``backend/`` directory so the ``websockets`` dependency
installed there is on the path)::

    cd backend
    uv run python ../scripts/ws_client.py --text "hello"
    uv run python ../scripts/ws_client.py --wav ../samples/hello_16k.wav
    uv run python ../scripts/ws_client.py --text "hello" --url ws://localhost:8010/ws

``--text`` requires a live server (``make dev-backend``) and a real
``OPENAI_API_KEY`` configured in ``backend/.env`` — this makes an actual
OpenAI Responses API call end to end. ``--wav`` additionally requires a real
``DEEPGRAM_API_KEY`` for the server to transcribe audio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from datetime import UTC, datetime

import websockets

DEFAULT_URL = "ws://localhost:8010/ws"

# 16kHz mono 16-bit PCM, per the established mic-capture convention
# (CLAUDE.md) -- 30ms frames land comfortably in the 20-40ms target range.
WAV_SAMPLE_RATE = 16000
WAV_SAMPLE_WIDTH = 2  # bytes (16-bit)
WAV_CHANNELS = 1
CHUNK_MS = 30


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]


def _print_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[{_timestamp()}] <unparseable frame>: {raw!r}")
        return {}
    event_type = event.get("type", "<unknown>")
    print(f"[{_timestamp()}] {event_type}: {json.dumps(event)}")
    return event


async def run_text_mode(url: str, text: str) -> None:
    async with websockets.connect(url) as ws:
        # Wait for the greeting frame before sending anything.
        while True:
            raw = await ws.recv()
            event = _print_event(raw)
            if event.get("type") == "ready":
                break

        await ws.send(json.dumps({"type": "text_input", "text": text}))

        while True:
            raw = await ws.recv()
            event = _print_event(raw)
            if event.get("type") == "assistant_done":
                break
            if event.get("type") == "error":
                print("Server reported an error — exiting.", file=sys.stderr)
                break


def _read_wav_pcm(path: str) -> bytes:
    """Read a WAV file's raw PCM frames, requiring 16kHz mono 16-bit —
    the format the backend/Deepgram pipeline expects. Raises a clear
    error (rather than silently resampling) if the file doesn't match."""
    with wave.open(path, "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_rate = wav_file.getframerate()

        if (channels, sample_width, frame_rate) != (
            WAV_CHANNELS,
            WAV_SAMPLE_WIDTH,
            WAV_SAMPLE_RATE,
        ):
            raise SystemExit(
                f"error: {path!r} is {frame_rate}Hz, {channels}ch, "
                f"{sample_width * 8}-bit — this client requires "
                f"{WAV_SAMPLE_RATE}Hz mono 16-bit PCM. Re-encode it first "
                f"(e.g. `ffmpeg -i in.wav -ar 16000 -ac 1 -sample_fmt s16 out.wav`)."
            )

        return wav_file.readframes(wav_file.getnframes())


async def _stream_wav_audio(ws: websockets.WebSocketClientProtocol, pcm: bytes) -> None:
    """Send the WAV's PCM as binary frames in ~30ms chunks, with a small
    sleep between chunks to mimic real-time microphone capture."""
    bytes_per_chunk = int(WAV_SAMPLE_RATE * WAV_SAMPLE_WIDTH * WAV_CHANNELS * CHUNK_MS / 1000)
    for offset in range(0, len(pcm), bytes_per_chunk):
        chunk = pcm[offset : offset + bytes_per_chunk]
        await ws.send(chunk)
        await asyncio.sleep(CHUNK_MS / 1000)

    await ws.send(json.dumps({"type": "stop"}))


async def run_wav_mode(url: str, wav_path: str) -> None:
    pcm = _read_wav_pcm(wav_path)

    async with websockets.connect(url) as ws:
        while True:
            raw = await ws.recv()
            event = _print_event(raw)
            if event.get("type") == "ready":
                break

        await ws.send(json.dumps({"type": "start", "sample_rate": WAV_SAMPLE_RATE}))

        sender = asyncio.create_task(_stream_wav_audio(ws, pcm))

        try:
            while True:
                raw = await ws.recv()
                event = _print_event(raw)
                event_type = event.get("type")
                if event_type == "assistant_done":
                    break
                if event_type == "error":
                    print("Server reported an error — exiting.", file=sys.stderr)
                    break
        finally:
            if not sender.done():
                sender.cancel()
            else:
                # Surface any exception raised while streaming audio.
                sender.result()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"WebSocket URL (default: {DEFAULT_URL})"
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--text", help="Send a single text_input frame with this text and exit on assistant_done"
    )
    mode_group.add_argument(
        "--wav",
        help="Stream a 16kHz mono 16-bit PCM WAV file as mic audio and exit on assistant_done",
    )
    args = parser.parse_args()

    if args.text:
        asyncio.run(run_text_mode(args.url, args.text))
    else:
        asyncio.run(run_wav_mode(args.url, args.wav))


if __name__ == "__main__":
    main()
