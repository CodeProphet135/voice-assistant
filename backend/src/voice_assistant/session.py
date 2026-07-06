"""Per-connection orchestrator.

Phase 1 scope: the text-input path only. Audio (Phase 2 STT, Phase 3 TTS) and
tools (Phase 4) slot into the stubs below without changing this file's shape.
"""

import json
import uuid

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from voice_assistant.agent.agent import run_agent
from voice_assistant.config import settings
from voice_assistant.protocol import (
    ErrorEvent,
    ReadyEvent,
    StartEvent,
    StateEvent,
    StopEvent,
    TextInputEvent,
    parse_client_event,
)

# No tools yet (Phase 4 introduces a registry here).
_TOOLS: list[dict] = []


class Session:
    """Owns one WebSocket connection's lifecycle and conversation state."""

    def __init__(self, ws: WebSocket) -> None:
        self.session_id = uuid.uuid4().hex
        self.ws = ws
        self.input_items: list[dict] = []

        # The SDK reads OPENAI_API_KEY from the environment on its own, but we
        # pass it explicitly from settings for consistency with the rest of
        # the app's config surface. An empty key still constructs a client
        # fine — failures surface as ErrorEvents at call time, not here.
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=settings.openai_api_key or None)

    async def emit(self, event) -> None:
        """The single seam every server→client event flows through.

        Phase 6 will also hand this event to an ``EventRecorder`` for
        append-only persistence here — don't build a second emission path
        elsewhere when that lands.
        """
        await self.ws.send_json(event.model_dump())

    async def on_sentence(self, sentence: str) -> None:
        """TTS sink stub. Phase 3 replaces this with Deepgram synthesis
        pushed onto a queue; for now there's no audio path, so this is a
        no-op placeholder that keeps the agent loop's shape stable."""

    async def tool_executor(self, name: str, arguments: str, call_id: str) -> str:
        """Tool dispatch stub. The tool list is empty in Phase 1, so this
        should never actually be invoked — Phase 4 replaces it with a real
        registry lookup."""
        raise NotImplementedError(f"No tools are registered yet (attempted: {name!r})")

    async def _handle_text_input(self, event: TextInputEvent) -> None:
        await self.emit(StateEvent(state="thinking"))
        self.input_items.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": event.text}],
            }
        )
        await run_agent(
            client=self.client,
            input_items=self.input_items,
            tools=_TOOLS,
            emit=self.emit,
            on_sentence=self.on_sentence,
            tool_executor=self.tool_executor,
        )
        await self.emit(StateEvent(state="idle"))

    async def _dispatch(self, raw: str) -> None:
        event = parse_client_event(json.loads(raw))
        if isinstance(event, TextInputEvent):
            await self._handle_text_input(event)
        elif isinstance(event, StartEvent):
            await self.emit(StateEvent(state="listening"))
        elif isinstance(event, StopEvent):
            await self.emit(StateEvent(state="idle"))

    async def run(self) -> None:
        """Main connection loop: greet, then dispatch incoming frames until
        the socket closes."""
        await self.emit(ReadyEvent(session_id=self.session_id))
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is None:
                    # Binary frames (mic PCM) arrive from Phase 2 onward;
                    # Phase 1 has no audio path yet, so ignore them.
                    continue
                await self._dispatch(text)
        except (WebSocketDisconnect, ConnectionClosed):
            pass
        except Exception as exc:  # noqa: BLE001 - last-resort guard, see below
            # Best-effort error notification before the socket goes away —
            # never let an unexpected exception here crash the ASGI worker.
            try:
                await self.emit(ErrorEvent(message=f"Internal error: {exc}"))
            except Exception:  # noqa: BLE001
                pass
