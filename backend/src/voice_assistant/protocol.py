"""Typed WebSocket frame schemas — the single contract between client and server.

Frames are JSON text messages, each tagged by a ``type`` discriminator. Binary
WebSocket frames (not modelled here) carry raw audio: client→server microphone
PCM (Phase 2) and server→client synthesized TTS PCM (Phase 3).

This is *one* contract spanning every phase. Audio/STT/TTS/timer frames are
defined now even though the code that emits them lands in later phases, so the
protocol never has to change shape underneath the frontend reducer.

``frontend/src/ws.ts`` mirrors this file — keep the two in lockstep.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

# --------------------------------------------------------------------------- #
# Client → server frames
# --------------------------------------------------------------------------- #


class StartEvent(BaseModel):
    """Open a conversational turn context. Sent when the user starts a session
    or (Phase 2) presses the mic button. ``sample_rate`` is advisory for audio."""

    type: Literal["start"] = "start"
    sample_rate: int = 16000


class StopEvent(BaseModel):
    """Close the current turn / stop capture."""

    type: Literal["stop"] = "stop"


class TextInputEvent(BaseModel):
    """A typed user message (Phase 1). Commits a turn and launches the agent."""

    type: Literal["text_input"] = "text_input"
    text: str


class PlaybackFinishedEvent(BaseModel):
    """The browser's audio player drained its queue: every TTS byte it has
    received so far finished playing (or was discarded by a ``tts_cancel``
    flush). Anchors the echo guard to ACTUAL playback end instead of the
    server-side estimate. ``received_bytes`` is the client's cumulative count
    of binary TTS bytes received on this connection; the server ignores the
    signal when it has sent more than that (audio still in flight, e.g. a
    drain report racing the next turn's audio), so a stale — or malicious —
    frame can only ever shorten the echo-guard window, never extend it."""

    type: Literal["playback_finished"] = "playback_finished"
    received_bytes: int = 0


ClientEvent = Annotated[
    StartEvent | StopEvent | TextInputEvent | PlaybackFinishedEvent,
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientEvent] = TypeAdapter(ClientEvent)


def parse_client_event(data: dict) -> ClientEvent:
    """Validate a decoded JSON text frame into a typed client event."""
    return _client_adapter.validate_python(data)


# --------------------------------------------------------------------------- #
# Server → client frames
# --------------------------------------------------------------------------- #

# The pipeline state machine surfaced to the UI:
#   idle → listening → thinking → speaking → listening
State = Literal["idle", "listening", "thinking", "speaking"]


class ReadyEvent(BaseModel):
    """Sent once when the socket is accepted and the session is initialised."""

    type: Literal["ready"] = "ready"
    session_id: str


class StateEvent(BaseModel):
    """A pipeline state-machine transition."""

    type: Literal["state"] = "state"
    state: State


class SttPartialEvent(BaseModel):
    """Interim (non-final) speech-to-text hypothesis; replaces the prior partial."""

    type: Literal["stt_partial"] = "stt_partial"
    text: str


class SttFinalEvent(BaseModel):
    """Finalized transcript for the committed user turn."""

    type: Literal["stt_final"] = "stt_final"
    text: str


class SpeechStartedEvent(BaseModel):
    """Deepgram VAD detected the user beginning to speak (Phase 6). Emitted
    only when no turn is active, so it marks a genuine user-turn onset (not
    the assistant's own TTS echo). A Timeline/Replay signal; the live reducer
    ignores it."""

    type: Literal["speech_started"] = "speech_started"


class AssistantDeltaEvent(BaseModel):
    """One streamed chunk of assistant text (token-level), appended in the UI."""

    type: Literal["assistant_delta"] = "assistant_delta"
    text: str


class AssistantDoneEvent(BaseModel):
    """End of the assistant's turn; carries the full concatenated final text."""

    type: Literal["assistant_done"] = "assistant_done"
    text: str


class LlmRequestEvent(BaseModel):
    """A snapshot of the exact serialized ``input`` sent to the Responses API
    for one tool-loop iteration (Phase 6). The payoff of the ``store=False``
    design — fully serializable conversation state per request. Surfaced in
    the Event Inspector; the live reducer ignores it."""

    type: Literal["llm_request"] = "llm_request"
    input: list[dict]
    model: str
    iteration: int


class ToolCallEvent(BaseModel):
    """The agent decided to invoke a tool. ``arguments`` is the raw JSON string
    produced by the model (may be partial/empty until the call completes)."""

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: str = ""


class ToolResultEvent(BaseModel):
    """A tool finished; ``output`` is the string handed back to the model.
    ``arguments`` is the *final* JSON string the model produced for the call
    (the streamed-open ``tool_call`` frame carries it empty, so consumers
    backfill from here). Optional/defaulted so older persisted payloads that
    predate this field still validate."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    arguments: str = ""
    output: str


class TtsStartEvent(BaseModel):
    """A sentence is about to be streamed as binary TTS audio (Phase 3)."""

    type: Literal["tts_start"] = "tts_start"
    sentence_index: int
    text: str


class TtsEndEvent(BaseModel):
    """All audio for the current turn has been sent (Phase 3)."""

    type: Literal["tts_end"] = "tts_end"


class TtsCancelEvent(BaseModel):
    """Barge-in: discard any buffered/playing audio immediately (Phase 3)."""

    type: Literal["tts_cancel"] = "tts_cancel"


class TimerFiredEvent(BaseModel):
    """A ``set_timer`` tool timer elapsed (Phase 5)."""

    type: Literal["timer_fired"] = "timer_fired"
    timer_id: str
    label: str


class ErrorEvent(BaseModel):
    """A recoverable error surfaced to the UI."""

    type: Literal["error"] = "error"
    message: str


ServerEvent = (
    ReadyEvent
    | StateEvent
    | SpeechStartedEvent
    | SttPartialEvent
    | SttFinalEvent
    | AssistantDeltaEvent
    | AssistantDoneEvent
    | LlmRequestEvent
    | ToolCallEvent
    | ToolResultEvent
    | TtsStartEvent
    | TtsEndEvent
    | TtsCancelEvent
    | TimerFiredEvent
    | ErrorEvent
)
