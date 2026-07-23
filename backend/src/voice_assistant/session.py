"""Per-connection orchestrator: owns one WebSocket's lifecycle and drives
each user turn through the full voice pipeline.

A turn runs as a cancellable task that appends the user message, runs the
agent loop, and pipelines every sentence the agent produces into TTS
synthesis streamed back to the browser as binary audio. Speech-to-text
(Deepgram) feeds the same turn machinery as typed input, and barge-in cancels
the in-flight task the moment the user speaks over the assistant. Every
server->client event flows through the single ``emit()`` seam, which both
sends on the socket and hands the event to the append-only event log.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass

from fastapi import WebSocket
from opentelemetry import trace
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from voice_assistant.agent.agent import run_agent
from voice_assistant.agent.history import truncate_history
from voice_assistant.agent.tools import registry
from voice_assistant.agent.tools.registry import ToolContext
from voice_assistant.config import settings
from voice_assistant.events import EventRecorder
from voice_assistant.protocol import (
    ErrorEvent,
    PlaybackFinishedEvent,
    ReadyEvent,
    SpeechStartedEvent,
    StartEvent,
    StateEvent,
    StopEvent,
    SttFinalEvent,
    SttPartialEvent,
    TextInputEvent,
    TimerFiredEvent,
    TtsCancelEvent,
    TtsEndEvent,
    TtsStartEvent,
    parse_client_event,
)
from voice_assistant.providers.base import (
    SpeechStarted,
    SttClosed,
    STTProvider,
    Transcript,
    TTSProvider,
    UtteranceEnd,
)
from voice_assistant.providers.deepgram import DeepgramSTT, DeepgramTTS
from voice_assistant.telemetry import get_tracer

_logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

# Tool schemas come from the registry; importing ``registry`` runs the tools
# package __init__, which imports each tool module so it self-registers before
# ``definitions()`` is read here.
_TOOLS: list[dict] = registry.definitions()

# Sentinel pushed onto the TTS queue to signal the agent producer is done
# (always enqueued last, even on error, so the drain loop always terminates).
_TURN_END = object()


@dataclass
class _TimerHandle:
    """A live countdown timer: the background task plus the metadata the
    list_timers/cancel_timer tools need to describe and address it."""

    task: asyncio.Task
    label: str | None
    deadline: float  # time.monotonic() when the timer fires

# Cap on client-owned conversation history re-sent to OpenAI each turn. Oldest
# whole turns past this are trimmed (agent/history.py); the system prompt rides
# in instructions= and is never part of this list, so it's never dropped.
MAX_INPUT_ITEMS = 40

# An interim transcript with at least this many words barges in on an active
# turn whenever it isn't classified as echo. Three, not two: live echo runs showed
# Deepgram mishears short echo fragments badly enough ("A fun" came back as
# "The fun") that the echo threshold alone can't screen them.
_MIN_BARGE_IN_WORDS = 3

# A transcript whose echo score reaches this is our own TTS audio leaking
# back into the mic. 0.6, not higher: live runs showed misheard echo
# fragments score >= 60% word overlap ("A fun fact. Rome" misheard from
# "A fun fact: Roman..." scores 75%) while genuine phrases stay <= ~30% --
# clean separation.
_ECHO_THRESHOLD = 0.6

# Strict tier for 2-word interims (live run 3: Deepgram can stop at a 2-word
# interim and jump straight to the final, so waiting for a third word can
# mean never barging at all). Fragments that short can't be reliably
# classified at _ECHO_THRESHOLD -- misheard echo "The fun" scores 0.5 while
# genuine "wait stop" scores 0.0 -- so a 2-word interim barges only when its
# echo score is near zero. 1-word interims never barge.
_TWO_WORD_BARGE_MAX_SCORE = 0.4

# TTS audio sent to the browser is linear16 @ 24kHz mono (providers/
# deepgram.py _TTS_SAMPLE_RATE), i.e. 48,000 PCM bytes per second of
# playback. Used to estimate when the browser finishes playing what we've
# sent (synthesis streams faster than real time, so playback outlives the
# send loop and even tts_end).
_TTS_BYTES_PER_SEC = 48_000

# How long after playback end an echo transcript can still arrive. Past the
# playback horizon plus this tail, a transcript physically cannot be echo, so
# the echo guard must stand down: users answering a question like "do you
# mean X or Y?" naturally repeat the assistant's own words, and live session
# 5ccbd307 showed the guard silently dropping every such answer 30+ seconds
# after playback ended. Two tiers, depending on how the horizon was set:
#
# - Fallback (``_ECHO_GUARD_TAIL_S``): the horizon is a server-side ESTIMATE
#   (bytes / 48kB/s accrued at send time), so the tail must absorb estimate
#   error (client receive/scheduling delay) on top of the mic-capture ->
#   Deepgram -> transcript latency.
# - Confirmed (``_ECHO_GUARD_CONFIRMED_TAIL_S``): the browser reported actual
#   playback end (``playback_finished``), so only the capture->transcript
#   pipeline remains: worklet framing (30ms) + WS transit + Deepgram
#   endpointing (300ms) + finalization. Live E2E measurement (harness
#   streaming real speech to the live backend) put the FINAL transcript of
#   trailing audio ~0.5s after that audio's last mic frame; 1.0s is that
#   worst case with ~2x margin. Echo of the last-played audio genuinely
#   arrives after playback ends, so this cannot be zero.
_ECHO_GUARD_TAIL_S = 2.0
_ECHO_GUARD_CONFIRMED_TAIL_S = 1.0

# Echo-normalization regexes (see Session._echo_score). Separators (dashes/
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
        self._session_uuid = uuid.uuid4()
        self.session_id = self._session_uuid.hex
        self.ws = ws
        self.input_items: list[dict] = []

        # Pass the configured key explicitly; falling back to None keeps the
        # SDK's own OPENAI_API_KEY env lookup in play. The client is built
        # eagerly here, so with a key from neither source the SDK raises at
        # construction rather than lazily on first use (see the known
        # limitation in TECH_DEBT.md); tests supply a dummy key via the env.
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=settings.openai_api_key or None)

        # STT (mic) state. ``stt`` is None whenever no capture is active, so
        # the text-only chat path (and its tests) never touch Deepgram.
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

        # Background countdown timers (set_timer tool), keyed by timer_id, so
        # the timer tools can list/cancel them and teardown can cancel any
        # still pending — nothing leaks on disconnect.
        self._timer_tasks: dict[str, _TimerHandle] = {}

        # What the assistant has recently spoken (one entry per synthesized
        # sentence), used by ``_is_echo`` to recognize our own TTS audio
        # leaking back into the mic. Never cleared on turn end -- echo
        # transcripts can arrive after tts_end too (playback + STT latency);
        # ``_echo_possible`` time-gates when this is consulted.
        self._spoken_recent: deque[str] = deque(maxlen=20)

        # Estimated monotonic time when the browser finishes playing every
        # TTS byte sent so far (see ``_note_tts_audio_sent``). Echo can only
        # exist while audio is (or may still be) playing, so the echo guard
        # is inert past this horizon plus the applicable tail. The estimate
        # governs until the browser confirms actual playback end with a
        # ``playback_finished`` frame (``_handle_playback_finished``), which
        # re-anchors the horizon and arms the shorter confirmed tail.
        self._playback_horizon = 0.0
        self._playback_end_confirmed = False
        # Total binary TTS bytes sent this connection, mirrored by the
        # client's received-bytes counter -- used to detect stale
        # ``playback_finished`` frames (drain reports racing newer audio).
        self._tts_bytes_sent = 0

        self._current_turn_id: uuid.UUID | None = None
        self._recorder = self._make_event_recorder()

    async def emit(self, event) -> None:
        """The single seam every server→client event flows through. Record
        for append-only persistence FIRST, then send to the WS. Recording
        first makes ``seq`` assignment synchronous (so concurrent emits from
        the turn task and the STT consumer can't interleave their seq order)
        and guarantees a failed WS send never drops the event from the
        durable log -- the log is a faithful superset of what was delivered."""
        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else None
        span_id = format(ctx.span_id, "016x") if ctx.is_valid else None
        self._recorder.record(
            event, turn_id=self._current_turn_id, trace_id=trace_id, span_id=span_id
        )
        await self.ws.send_json(event.model_dump())

    async def on_sentence(self, sentence: str) -> None:
        """The queue producer side of the agent->TTS pipeline: ``run_agent``
        (agent.py) invokes this once per sentence the chunker emits (plus
        once for the trailing flush). ``_run_turn`` is the consumer."""
        await self._tts_queue.put(sentence)

    async def tool_executor(self, name: str, arguments: str, call_id: str) -> str:
        """Dispatch a tool call through the registry, under a ``tool.execute``
        span that nests inside the current ``turn`` span (same pattern as
        ``llm.request``/``tts.synthesize``). ``call_id`` is unused by the
        registry but kept in the signature ``run_agent`` calls with."""
        with _tracer.start_as_current_span("tool.execute") as span:
            span.set_attribute("tool.name", name)
            return await registry.execute(name, arguments, ToolContext(session=self))

    def _make_stt_provider(self) -> STTProvider:
        """Factory seam so tests can inject a fake STT provider without
        touching Deepgram or requiring an API key."""
        return DeepgramSTT()

    def _make_tts_provider(self) -> TTSProvider:
        """Factory seam so tests can inject a fake TTS provider without
        touching Deepgram or requiring an API key. Mirrors
        ``_make_stt_provider``."""
        return DeepgramTTS()

    def _make_event_recorder(self) -> EventRecorder:
        """Factory seam so tests can inject a fake recorder."""
        return EventRecorder(self._session_uuid)

    def _new_turn_id(self) -> uuid.UUID:
        self._current_turn_id = uuid.uuid4()
        return self._current_turn_id

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
        ``_commit_stt_turn``) so that barge-in can cancel it on new speech;
        the whole turn -- including queue drain -- happens inside the lock so
        back-to-back turns never interleave.
        """
        async with self._turn_lock:
            with _tracer.start_as_current_span("turn"):
                # Echo can only come from THIS turn's TTS (or the tail after
                # it ends, before the next turn starts) -- carrying prior
                # turns' vocabulary forward let common words a much earlier
                # answer happened to use ("are", "you", "sure", ...) make a
                # genuine barge-in on unrelated content score as echo.
                self._spoken_recent.clear()
                self.input_items = truncate_history(self.input_items, MAX_INPUT_ITEMS)
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
                                self._note_tts_audio_sent(len(chunk))
                                await self.ws.send_bytes(chunk)
                        index += 1
                    await producer
                    await self.emit(TtsEndEvent())
                    await self.emit(
                        StateEvent(state="listening" if self.stt is not None else "idle")
                    )
                    self._current_turn_id = None
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
        self._new_turn_id()
        self._recorder.note_title(event.text)
        await self._run_turn(event.text)

    def _turn_active(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    def _echo_score(self, text: str) -> float:
        """Match strength of ``text`` (an STT transcript) against what the
        assistant recently spoke: 1.0 on a substring hit, otherwise the
        fuzzy word-overlap ratio, 0.0 when there is nothing to compare."""
        if not self._spoken_recent:
            return 0.0
        normalized = _normalize_for_echo(text)
        if not normalized:
            return 0.0

        joined = _normalize_for_echo(" ".join(self._spoken_recent))
        if normalized in joined:
            return 1.0

        # Space-insensitive substring: Deepgram segments words differently
        # than our TTS text (live run: spoken "under water" came back as
        # "underwater"), and one such difference must not defeat the guard.
        # Only for transcripts >= 6 compact chars -- shorter ones produce
        # trivial matches (e.g. "a bit" inside "habitually").
        joined_compact = joined.replace(" ", "")
        compact = normalized.replace(" ", "")
        if len(compact) >= 6 and compact in joined_compact:
            return 1.0

        # Fuzzy fallback: Deepgram may mis-transcribe a word of our own
        # echoed audio, so an exact substring match isn't guaranteed even
        # when it really is an echo. A word also counts as matched if it's
        # long enough (>= 4 chars, to avoid trivial hits) and appears inside
        # the space-stripped spoken text -- same segmentation tolerance as
        # above, applied per word.
        words = normalized.split()
        joined_words = set(joined.split())
        matched = sum(
            w in joined_words or (len(w) >= 4 and w in joined_compact) for w in words
        )
        return matched / len(words)

    def _is_echo(self, text: str) -> bool:
        """Whether ``text`` is our own TTS audio leaking back into the mic
        rather than genuine user speech."""
        return self._echo_score(text) >= _ECHO_THRESHOLD

    def _note_tts_audio_sent(self, num_bytes: int) -> None:
        """Advance ``_playback_horizon`` by the playback duration of a TTS
        chunk just sent. Playback is contiguous while the browser's buffer
        is non-empty, so each chunk extends the current horizon; a fresh
        burst after the buffer drained restarts the clock at now. Any new
        audio also invalidates a previously confirmed playback end -- the
        estimate (+ its wider tail) governs again until the browser reports
        this audio's drain."""
        now = time.monotonic()
        self._playback_horizon = (
            max(self._playback_horizon, now) + num_bytes / _TTS_BYTES_PER_SEC
        )
        self._playback_end_confirmed = False
        self._tts_bytes_sent += num_bytes

    def _handle_playback_finished(self, event: PlaybackFinishedEvent) -> None:
        """The browser reports its playback buffer drained: every TTS byte it
        received has finished playing (or was flushed). Re-anchor the echo
        guard to this ACTUAL playback end and arm the shorter confirmed tail.

        Two safety properties, so a buggy/malicious/stale client frame can
        never suppress genuine user speech:
        - A frame acknowledging fewer bytes than we've sent is stale (audio
          still in flight -- e.g. a drain report racing the next turn's
          audio) and is ignored outright.
        - The horizon may move up to ``now`` (actual end can lag the
          estimate by network/scheduling delay) but never so far that the
          confirmed window (horizon + confirmed tail) outlives the fallback
          window (estimate + fallback tail) -- the signal only ever shortens
          the guard."""
        if event.received_bytes < self._tts_bytes_sent:
            return
        self._playback_horizon = min(
            time.monotonic(),
            self._playback_horizon + (_ECHO_GUARD_TAIL_S - _ECHO_GUARD_CONFIRMED_TAIL_S),
        )
        self._playback_end_confirmed = True

    def _echo_possible(self) -> bool:
        """Whether our own TTS audio could still be coming back through the
        mic right now -- i.e. whether ``_echo_score`` is worth consulting. A
        turn in flight may be synthesizing more audio at any moment; after
        that, echo can only arrive until buffered playback ends plus the
        capture->transcript latency tail (the short one when the browser
        confirmed actual playback end, the wider one while the horizon is
        only a server-side estimate)."""
        tail = (
            _ECHO_GUARD_CONFIRMED_TAIL_S
            if self._playback_end_confirmed
            else _ECHO_GUARD_TAIL_S
        )
        return self._turn_active() or time.monotonic() <= self._playback_horizon + tail

    async def _barge_in(self) -> None:
        """Cancel the in-flight assistant turn because the user started
        speaking over it. ``_run_turn``'s cancel path unwinds the agent
        producer + TTS and drains the queue; here we just tear the
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
        # tts_cancel flushes the browser's playback buffer (player.ts), so
        # any horizon accrued for still-buffered audio is void -- echo can
        # only linger for the latency tail from this moment. This reset is
        # itself an estimate (the flush happens when the client processes
        # tts_cancel), so drop any earlier confirmation; the flush fires the
        # client's drain report, which re-confirms moments later.
        self._playback_horizon = time.monotonic()
        self._playback_end_confirmed = False
        await self.emit(StateEvent(state="listening"))
        self._current_turn_id = None

    def schedule_timer(self, seconds: int, label: str | None) -> str:
        """Start a tracked countdown task and return its id. Called by the
        ``set_timer`` tool; the task fires ``_fire_timer`` when it elapses."""
        timer_id = uuid.uuid4().hex
        self._timer_tasks[timer_id] = _TimerHandle(
            task=asyncio.create_task(self._fire_timer(timer_id, seconds, label)),
            label=label,
            deadline=time.monotonic() + seconds,
        )
        return timer_id

    def list_timers(self) -> list[dict]:
        """Snapshot of the still-pending timers for the ``list_timers`` tool:
        ``{"timer_id", "label", "remaining_seconds"}`` per entry."""
        now = time.monotonic()
        return [
            {
                "timer_id": timer_id,
                "label": handle.label,
                "remaining_seconds": max(0, round(handle.deadline - now)),
            }
            for timer_id, handle in self._timer_tasks.items()
        ]

    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel a pending timer by id; False if no such timer is running."""
        handle = self._timer_tasks.pop(timer_id, None)
        if handle is None:
            return False
        handle.task.cancel()
        return True

    async def _fire_timer(self, timer_id: str, seconds: int, label: str | None) -> None:
        """Wait out the timer, then emit ``timer_fired`` and speak a fixed
        phrase through the existing TTS path -- all under ``_turn_lock`` and a
        single fresh turn id, so the notification never talks over an in-flight
        turn and every recorded event (incl. ``timer_fired``) shares one
        turn_id. Waiting for the lock also prevents a concurrent conversation
        turn's ``_current_turn_id`` from being clobbered."""
        try:
            await asyncio.sleep(seconds)
            phrase = f"Your {label} timer is done." if label else "Your timer is done."
            async with self._turn_lock:
                self._new_turn_id()
                try:
                    with _tracer.start_as_current_span("turn"):
                        await self.emit(TimerFiredEvent(timer_id=timer_id, label=label or ""))
                        await self.emit(StateEvent(state="speaking"))
                        await self.emit(TtsStartEvent(sentence_index=0, text=phrase))
                        with _tracer.start_as_current_span("tts.synthesize") as span:
                            span.set_attribute("sentence_index", 0)
                            async for chunk in self.tts.synthesize(phrase):
                                self._note_tts_audio_sent(len(chunk))
                                await self.ws.send_bytes(chunk)
                        await self.emit(TtsEndEvent())
                        await self.emit(
                            StateEvent(state="listening" if self.stt is not None else "idle")
                        )
                finally:
                    self._current_turn_id = None
        except asyncio.CancelledError:
            raise
        finally:
            self._timer_tasks.pop(timer_id, None)

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
                            # Past the playback window echo is physically
                            # impossible -- don't let overlap with what we
                            # said earlier (e.g. the user answering "do you
                            # mean X or Y?" with "X") read as echo.
                            score = (
                                self._echo_score(stripped)
                                if self._echo_possible()
                                else 0.0
                            )
                            word_count = len(stripped.split())
                            if score >= _ECHO_THRESHOLD:
                                # Diagnostics for an open barge-in echo-
                                # classification investigation; kept until it's
                                # pinned down from a live repro, then removed.
                                _logger.warning(
                                    "BARGE DEBUG interim DROPPED-echo turn_active=%s "
                                    "score=%.2f words=%d text=%r",
                                    self._turn_active(), score, word_count, stripped,
                                )
                                # Our own TTS leaking into the mic -- drop it
                                # entirely: no barge-in, and don't render it
                                # as a live user bubble either.
                                continue
                            will_barge = self._turn_active() and (
                                word_count >= _MIN_BARGE_IN_WORDS
                                or (
                                    word_count == 2
                                    and score < _TWO_WORD_BARGE_MAX_SCORE
                                )
                            )
                            _logger.warning(
                                "BARGE DEBUG interim %s turn_active=%s score=%.2f "
                                "words=%d text=%r",
                                "BARGE" if will_barge else "shown-no-barge",
                                self._turn_active(), score, word_count, stripped,
                            )
                            if will_barge:
                                await self._barge_in()
                            await self.emit(SttPartialEvent(text=ev.text))
                        continue

                    final_stripped = ev.text.strip()
                    if final_stripped:
                        final_score = (
                            self._echo_score(ev.text) if self._echo_possible() else 0.0
                        )
                        final_is_echo = final_score >= _ECHO_THRESHOLD
                        _logger.warning(
                            "BARGE DEBUG final turn_active=%s speech_final=%s "
                            "is_echo=%s score=%.2f text=%r",
                            self._turn_active(), ev.speech_final, final_is_echo,
                            final_score, final_stripped,
                        )
                        if not final_is_echo:
                            self._stt_buffer.append(ev.text)

                    if ev.speech_final:
                        await self._commit_stt_turn()
                elif isinstance(ev, SpeechStarted):
                    # VAD fires on any noise incl. our own TTS echo during
                    # SPEAKING; emit only when no turn is active so recorded
                    # speech_started marks a genuine user-turn onset. Still a
                    # no-op for barge-in (interim transcripts own that).
                    if self._turn_active():
                        _logger.warning("BARGE DEBUG speech_started (VAD) during active turn")
                    else:
                        await self.emit(SpeechStartedEvent())
                elif isinstance(ev, UtteranceEnd):
                    # Fallback turn-end signal when speech_final never fires.
                    _logger.warning(
                        "BARGE DEBUG utterance_end turn_active=%s buffer=%r",
                        self._turn_active(), self._stt_buffer,
                    )
                    await self._commit_stt_turn()
                elif isinstance(ev, SttClosed):
                    # STT dropped and bounded reconnect was exhausted. Surface
                    # it and settle to idle; detach the (dead) provider without
                    # calling _stop_stt (that would cancel-and-await this very
                    # task). The user restarts the mic to retry.
                    await self.emit(
                        ErrorEvent(
                            message="Speech recognition disconnected — please restart the mic."
                        )
                    )
                    await self.emit(StateEvent(state="idle"))
                    provider, self.stt = self.stt, None
                    self._stt_task = None
                    if provider is not None:
                        with contextlib.suppress(Exception):  # noqa: BLE001
                            await provider.aclose()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a bad transcript kill the socket
            _logger.warning("STT consumer stopped unexpectedly", exc_info=True)

    async def _commit_stt_turn(self) -> None:
        """Join the buffered final-transcript pieces into one utterance and
        launch the turn as a tracked task WITHOUT awaiting it, so
        ``_consume_stt`` keeps looping (needed to watch for barge-in speech
        while a turn is running). Overlapping STT turns are
        still serialized by ``_turn_lock`` inside ``_run_turn``."""
        utterance = " ".join(part.strip() for part in self._stt_buffer if part.strip()).strip()
        self._stt_buffer = []
        if not utterance:
            return

        # Echo finals never reach the buffer (dropped in _consume_stt), so a
        # non-empty utterance committing while a turn is still speaking is
        # genuine user speech -- stop talking before answering it. Needed
        # because Deepgram can jump from a 2-word interim straight to the
        # final (live run 3), leaving the interim barge gate never fired.
        # Also guarantees the old turn task is cancelled and awaited before
        # self._turn_task is reassigned below.
        if self._turn_active():
            await self._barge_in()

        self._new_turn_id()
        self._recorder.note_title(utterance)
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
            # turn on *new* speech — is a separate, explicit path.)
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
        elif isinstance(event, PlaybackFinishedEvent):
            self._handle_playback_finished(event)

    async def run(self) -> None:
        """Main connection loop: greet, then dispatch incoming frames until
        the socket closes."""
        await self._recorder.start()
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
            # Cancel any pending countdown timers so a dropped socket can't
            # leave background tasks running (or trying to emit on a dead ws).
            for handle in list(self._timer_tasks.values()):
                handle.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await handle.task
            self._timer_tasks.clear()
            # A dropped socket must not leak the STT task/connection.
            await self._stop_stt()
            try:
                await self.tts.aclose()
            except Exception:  # noqa: BLE001 - best-effort, never crash on teardown
                _logger.warning("Error closing TTS provider", exc_info=True)
            await self._recorder.stop()
