"""Per-connection orchestrator.

Phase 2 scope: text-input path (Phase 1) plus streaming speech-to-text
(Deepgram). TTS (Phase 3) and tools (Phase 4) slot into the remaining stubs
below without changing this file's shape.
"""

import asyncio
import json
import logging
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
    SttFinalEvent,
    SttPartialEvent,
    TextInputEvent,
    parse_client_event,
)
from voice_assistant.providers.base import STTProvider, Transcript, UtteranceEnd
from voice_assistant.providers.deepgram import DeepgramSTT

_logger = logging.getLogger(__name__)

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

        # STT (mic) state. ``stt`` is None whenever no capture is active, so
        # the text-only Phase 1 path (and its tests) never touch Deepgram.
        self.stt: STTProvider | None = None
        self._stt_task: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()
        self._stt_buffer: list[str] = []

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

    def _make_stt_provider(self) -> STTProvider:
        """Factory seam so tests can inject a fake STT provider without
        touching Deepgram or requiring an API key."""
        return DeepgramSTT()

    async def _run_turn(self, text: str) -> None:
        """Run one user turn (shared by the text-input and STT commit
        paths): append the user message, run the agent to completion, then
        settle back into listening (if the mic is active) or idle."""
        self.input_items.append(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        await self.emit(StateEvent(state="thinking"))
        await run_agent(
            client=self.client,
            input_items=self.input_items,
            tools=_TOOLS,
            emit=self.emit,
            on_sentence=self.on_sentence,
            tool_executor=self.tool_executor,
        )
        await self.emit(StateEvent(state="listening" if self.stt is not None else "idle"))

    async def _handle_text_input(self, event: TextInputEvent) -> None:
        await self._run_turn(event.text)

    async def _consume_stt(self) -> None:
        """Drain normalized STT events, surfacing partial/final transcripts
        and committing a turn once Deepgram signals the utterance is done.

        Guarded so a bad transcript or a stray exception here never kills
        the WebSocket — mirrors the guard style in agent.py.
        """
        assert self.stt is not None
        try:
            async for ev in self.stt.events():
                if isinstance(ev, Transcript):
                    if not ev.is_final:
                        if ev.text.strip():
                            await self.emit(SttPartialEvent(text=ev.text))
                        continue

                    if ev.text.strip():
                        self._stt_buffer.append(ev.text)

                    if ev.speech_final:
                        await self._commit_stt_turn()
                elif isinstance(ev, UtteranceEnd):
                    # Fallback turn-end signal when speech_final never fires.
                    await self._commit_stt_turn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a bad transcript kill the socket
            _logger.warning("STT consumer stopped unexpectedly", exc_info=True)

    async def _commit_stt_turn(self) -> None:
        """Join the buffered final-transcript pieces into one utterance and
        launch the agent, guarded so only one turn runs at a time."""
        utterance = " ".join(part.strip() for part in self._stt_buffer if part.strip()).strip()
        self._stt_buffer = []
        if not utterance:
            return

        async with self._turn_lock:
            await self.emit(SttFinalEvent(text=utterance))
            await self._run_turn(utterance)

    async def _start_stt(self) -> None:
        await self.emit(StateEvent(state="listening"))
        self.stt = self._make_stt_provider()
        await self.stt.start()
        self._stt_task = asyncio.create_task(self._consume_stt())

    async def _stop_stt(self) -> None:
        provider = self.stt
        self.stt = None

        if provider is not None:
            try:
                await provider.finish()
            except Exception:  # noqa: BLE001 - best-effort, never crash on teardown
                _logger.warning("Error finishing STT provider", exc_info=True)

        if self._stt_task is not None:
            self._stt_task.cancel()
            try:
                await self._stt_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._stt_task = None

        if provider is not None:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001 - best-effort, never crash on teardown
                _logger.warning("Error closing STT provider", exc_info=True)

    async def _dispatch(self, raw: str) -> None:
        event = parse_client_event(json.loads(raw))
        if isinstance(event, TextInputEvent):
            await self._handle_text_input(event)
        elif isinstance(event, StartEvent):
            await self._start_stt()
        elif isinstance(event, StopEvent):
            await self._stop_stt()
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
                    audio = message.get("bytes")
                    if audio is not None and self.stt is not None:
                        await self.stt.send_audio(audio)
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
        finally:
            # A dropped socket must not leak the STT task/connection.
            await self._stop_stt()
