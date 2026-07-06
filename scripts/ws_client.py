#!/usr/bin/env python3
"""CLI WebSocket test client for the voice-assistant backend.

Connects to the backend's ``/ws`` endpoint, waits for the ``ready`` frame,
sends a single ``text_input`` frame, then prints every event received
(each line timestamped) until an ``assistant_done`` event arrives, at which
point it disconnects and exits.

Usage (run from the ``backend/`` directory so the ``websockets`` dependency
installed there is on the path)::

    cd backend
    uv run python ../scripts/ws_client.py --text "hello"
    uv run python ../scripts/ws_client.py --text "hello" --url ws://localhost:8000/ws

Requires a live server (``make dev-backend``) and a real ``OPENAI_API_KEY``
configured in ``backend/.env`` — this makes an actual OpenAI Responses API
call end to end.

TODO(Phase 2): add a ``--wav <path>`` mode that streams 16kHz mono PCM audio
as binary frames instead of a single text_input, asserting on stt_partial /
stt_final events the way --text asserts on assistant_delta / assistant_done.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

import websockets

DEFAULT_URL = "ws://localhost:8000/ws"


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"WebSocket URL (default: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--text", help="Send a single text_input frame with this text and exit on assistant_done"
    )
    # TODO(Phase 2): --wav <path> to stream 16kHz mono PCM as mic audio instead.
    args = parser.parse_args()

    if not args.text:
        parser.error("--text is required (--wav mode lands in Phase 2)")

    asyncio.run(run_text_mode(args.url, args.text))


if __name__ == "__main__":
    main()
