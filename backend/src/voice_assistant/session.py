"""Per-connection orchestrator.

Phase 2 scope: text-input path (Phase 1) plus streaming speech-to-text
(Deepgram). Phase 3 adds streaming text-to-speech: the turn now runs as a
cancellable task that pipelines agent-produced sentences into a TTS queue
(the structure barge-in, Task 2b, will cancel). Tools (Phase 4) slot into
the remaining stub without changing this file's shape.
"""

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections import deque

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
    TtsCancelEvent,
    TtsEndEvent,
    TtsStartEvent,
    parse_client_event,
)
from voice_assistant.providers.base import (
    SpeechStarted,
    STTProvider,
    Transcript,
    TTSProvider,
    UtteranceEnd,
)
from voice_assistant.providers.deepgram import DeepgramSTT, DeepgramTTS
from voice_assistant.telemetry import get_tracer

_logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

# No tools yet (Phase 4 introduces a registry here).
_TOOLS: list[dict] = []

# Sentinel pushed onto the TTS queue to signal the agent producer is done
# (always enqueued last, even on error, so the drain loop always terminates).
_TURN_END = object()

# An interim transcript needs at least this many words before it's treated
# as substantial enough to barge in on an active turn (see
# docs/bug-self-barge-in-echo.md) -- a lone word is as likely to be a stray
# noise or echo onset as genuine speech.
_MIN_BARGE_IN_WORDS = 2

# Echo-normalization regexes (see Session._is_echo). Separators (dashes/
# slash) become a space so words either side don't fuse together; everything
# else that isn't alphanumeric/space is just dropped.
_ECHO_SEPARATORS_RE = re.compile(r"[-—–/]")
_ECHO_STRIP_RE = re.compile(r"[^a-z0-9 ]")
_ECHO_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_echo(text: str) -> str:
    """Lowercase and strip ``text`` down to bare words for echo comparison:
    dashes/slashes become spaces (so "Nice—hit" -> "nice hit"), remaining
    punctuation (apostrophes, periods, ...) is deleted outright (so "Here's"
    -> "heres"), then whitespace runs collapse to single spaces."""
    text = _ECHO_SEPARATORS_RE.sub(" ", text.lower())
    text = _ECHO_STRIP_RE.sub("", text)
    return _ECHO_WHITESPACE_RE.sub(" ", text).strip()


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

        # TTS (speaker) state. Construction is cheap and keyless-safe (no
        # network I/O until ``synthesize()`` is actually called), so it's
        # always present -- same posture as ``self.stt``'s factory seam.
        self.tts: TTSProvider = self._make_tts_provider()
        self._tts_queue: asyncio.Queue = asyncio.Queue()
        self._turn_task: asyncio.Task | None = None

        # What the assistant has recently spoken (one entry per synthesized
        # sentence), used by ``_is_echo`` to recognize our own TTS audio
        # leaking back into the mic. Never cleared on turn end -- echo
        # transcripts can arrive after tts_end too (playback + STT latency);
        # the bounded length is the eviction policy.
        self._spoken_recent: deque[str] = deque(maxlen=20)

    async def emit(self, event) -> None:
        """The single seam every server→client event flows through.

        Phase 6 will also hand this event to an ``EventRecorder`` for
        append-only persistence here — don't build a second emission path
        elsewhere when that lands.
        """
        await self.ws.send_json(event.model_dump())

    async def on_sentence(self, sentence: str) -> None:
        """The queue producer side of the agent->TTS pipeline: ``run_agent``
        (agent.py) invokes this once per sentence the chunker emits (plus
        once for the trailing flush). ``_run_turn`` is the consumer."""
        await self._tts_queue.put(sentence)

    async def tool_executor(self, name: str, arguments: str, call_id: str) -> str:
        """Tool dispatch stub. The tool list is empty in Phase 1, so this
        should never actually be invoked — Phase 4 replaces it with a real
        registry lookup."""
        raise NotImplementedError(f"No tools are registered yet (attempted: {name!r})")

    def _make_stt_provider(self) -> STTProvider:
        """Factory seam so tests can inject a fake STT provider without
        touching Deepgram or requiring an API key."""
        return DeepgramSTT()

    def _make_tts_provider(self) -> TTSProvider:
        """Factory seam so tests can inject a fake TTS provider without
        touching Deepgram or requiring an API key. Mirrors
        ``_make_stt_provider``."""
        return DeepgramTTS()

    async def _run_agent_producer(self) -> None:
        """Run the agent to completion, then always enqueue the terminating
        sentinel so ``_run_turn``'s drain loop is guaranteed to finish, even
        if the agent raised (``run_agent`` already swallows its own errors
        internally -- this ``finally`` is belt-and-suspenders)."""
        try:
            await run_agent(
                client=self.client,
                input_items=self.input_items,
                tools=_TOOLS,
                emit=self.emit,
                on_sentence=self.on_sentence,
                tool_executor=self.tool_executor,
            )
        finally:
            await self._tts_queue.put(_TURN_END)

    async def _run_turn(self, text: str) -> None:
        """Run one user turn (shared by the text-input and STT commit
        paths) under ``_turn_lock``: append the user message, run the agent
        while pipelining each sentence it produces into TTS synthesis
        streamed to the browser as binary frames, then settle back into
        listening (if the mic is active) or idle.

        This runs as a cancellable task from the STT path (see
        ``_commit_stt_turn``) so that barge-in (Task 2b) can cancel it on
        new speech; the whole turn -- including queue drain -- happens
        inside the lock so back-to-back turns never interleave.
        """
        async with self._turn_lock:
            with _tracer.start_as_current_span("turn"):
                self.input_items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                )
                await self.emit(StateEvent(state="thinking"))

                producer = asyncio.create_task(self._run_agent_producer())
                speaking = False
                index = 0
                try:
                    while True:
                        item = await self._tts_queue.get()
                        if item is _TURN_END:
                            break
                        if not speaking:
                            await self.emit(StateEvent(state="speaking"))
                            speaking = True
                        self._spoken_recent.append(item)
                        await self.emit(TtsStartEvent(sentence_index=index, text=item))
                        with _tracer.start_as_current_span("tts.synthesize") as span:
                            span.set_attribute("sentence_index", index)
                            async for chunk in self.tts.synthesize(item):
                                await self.ws.send_bytes(chunk)
                        index += 1
                    await producer
                    await self.emit(TtsEndEvent())
                    await self.emit(
                        StateEvent(state="listening" if self.stt is not None else "idle")
                    )
                except asyncio.CancelledError:
                    # Barge-in (2b) cancels this task. Cancel the agent
                    # producer, let it unwind, then propagate. Do NOT emit
                    # tts_end/listening here -- the canceller (2b) owns the
                    # post-barge-in state.
                    producer.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await producer
                    # The producer is now guaranteed finished (cancelled or
                    # not) and can no longer enqueue anything, so drain any
                    # sentences/_TURN_END it left behind -- the queue is
                    # shared across turns and stale items here would corrupt
                    # the *next* turn's drain loop (early stale _TURN_END,
                    # or a leftover sentence spoken out of turn).
                    while not self._tts_queue.empty():
                        self._tts_queue.get_nowait()
                    raise

    async def _handle_text_input(self, event: TextInputEvent) -> None:
        await self._run_turn(event.text)

    def _turn_active(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    def _is_echo(self, text: str) -> bool:
        """Guess whether ``text`` (an STT transcript) is actually our own
        TTS audio leaking back into the mic rather than genuine user speech
        (see docs/bug-self-barge-in-echo.md): compare it, normalized,
        against ``self._spoken_recent``."""
        if not self._spoken_recent:
            return False
        normalized = _normalize_for_echo(text)
        if not normalized:
            return False

        joined = _normalize_for_echo(" ".join(self._spoken_recent))
        if normalized in joined:
            return True

        # Fuzzy fallback: Deepgram may mis-transcribe a word of our own
        # echoed audio, so an exact substring match isn't guaranteed even
        # when it really is an echo.
        words = normalized.split()
        joined_words = set(joined.split())
        return sum(w in joined_words for w in words) / len(words) >= 0.8

    async def _barge_in(self) -> None:
        """Cancel the in-flight assistant turn because the user started
        speaking over it. ``_run_turn``'s cancel path (Task 2a) unwinds the
        agent producer + TTS and drains the queue; here we just tear the
        turn down and reset UI state back to listening."""
        task = self._turn_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - never let barge-in teardown crash the consumer
            _logger.warning("Error awaiting cancelled turn during barge-in", exc_info=True)
        self._turn_task = None
        await self.emit(TtsCancelEvent())
        await self.emit(StateEvent(state="listening"))

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
                        stripped = ev.text.strip()
                        if stripped:
                            if self._is_echo(stripped):
                                # Our own TTS leaking into the mic -- drop it
                                # entirely: no barge-in, and don't render it
                                # as a live user bubble either.
                                continue
                            if (
                                self._turn_active()
                                and len(stripped.split()) >= _MIN_BARGE_IN_WORDS
                            ):
                                await self._barge_in()
                            await self.emit(SttPartialEvent(text=ev.text))
                        continue

                    if ev.text.strip() and not self._is_echo(ev.text):
                        self._stt_buffer.append(ev.text)

                    if ev.speech_final:
                        await self._commit_stt_turn()
                elif isinstance(ev, SpeechStarted):
                    # Deepgram's VAD fires on any noise -- including our own
                    # TTS echoing back through the mic -- and carries no text
                    # to check against _spoken_recent, so it can no longer
                    # trigger barge-in by itself; a substantial interim
                    # transcript (above) does that job now.
                    pass
                elif isinstance(ev, UtteranceEnd):
                    # Fallback turn-end signal when speech_final never fires.
                    await self._commit_stt_turn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a bad transcript kill the socket
            _logger.warning("STT consumer stopped unexpectedly", exc_info=True)

    async def _commit_stt_turn(self) -> None:
        """Join the buffered final-transcript pieces into one utterance and
        launch the turn as a tracked task WITHOUT awaiting it, so
        ``_consume_stt`` keeps looping (Task 2b needs that to watch for
        barge-in speech while a turn is running). Overlapping STT turns are
        still serialized by ``_turn_lock`` inside ``_run_turn``."""
        utterance = " ".join(part.strip() for part in self._stt_buffer if part.strip()).strip()
        self._stt_buffer = []
        if not utterance:
            return

        await self.emit(SttFinalEvent(text=utterance))
        self._turn_task = asyncio.create_task(self._run_turn(utterance))

    async def _start_stt(self) -> None:
        await self.emit(StateEvent(state="listening"))
        self.stt = self._make_stt_provider()
        await self.stt.start()
        self._stt_task = asyncio.create_task(self._consume_stt())

    async def _stop_stt(self) -> None:
        provider = self.stt

        if provider is not None:
            # Signal end-of-audio so Deepgram flushes any pending final transcript.
            try:
                await provider.finish()
            except Exception:  # noqa: BLE001 - best-effort, never crash on teardown
                _logger.warning("Error finishing STT provider", exc_info=True)

            # A `stop` must not abort a reply that is already generating for an
            # utterance that already committed: wait for any in-flight turn to
            # finish before tearing the consumer down. (Barge-in — cancelling a
            # turn on *new* speech — is a separate, explicit Phase 3 path.)
            async with self._turn_lock:
                pass

        self.stt = None

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
            try:
                await self.tts.aclose()
            except Exception:  # noqa: BLE001 - best-effort, never crash on teardown
                _logger.warning("Error closing TTS provider", exc_info=True)
