"""Reconnect behavior for DeepgramSTT (Phase 5) + the session-level handling
of a terminal SttClosed event. No Deepgram key or network I/O is involved —
the socket + connect step are faked via monkeypatching the provider's open
seam. The *messages* those fake sockets yield are real SDK models, because
``_normalize`` narrows on the concrete classes: a duck-typed stand-in would
drift from the SDK and leave this suite green while production dropped every
transcript.
"""

import asyncio
import logging
import os
from typing import cast

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest
from conftest import FakeEventRecorder, FakeOpenAI, FakeTTSProvider, FakeWebSocket
from deepgram.core.unchecked_base_model import construct_type
from deepgram.listen.v1 import (
    ListenV1Metadata,
    ListenV1Results,
    ListenV1ResultsChannel,
    ListenV1ResultsChannelAlternativesItem,
    ListenV1ResultsMetadata,
    ListenV1ResultsMetadataModelInfo,
    ListenV1SpeechStarted,
    ListenV1UtteranceEnd,
)
from deepgram.listen.v1.socket_client import V1SocketClientResponse

from voice_assistant.providers.base import (
    SpeechStarted,
    SttClosed,
    Transcript,
    UtteranceEnd,
)
from voice_assistant.providers.deepgram import DeepgramSTT
from voice_assistant.session import Session

# Required by the SDK model but irrelevant to normalization — kept in one
# place so the meaningful fields stay legible in _results_msg().
_METADATA = ListenV1ResultsMetadata(
    request_id="00000000-0000-0000-0000-000000000000",
    model_uuid="00000000-0000-0000-0000-000000000000",
    model_info=ListenV1ResultsMetadataModelInfo(name="nova-3", version="1", arch="test"),
)


class _FakeSocket:
    """Async-iterable that yields N Results messages then ends (simulating a
    dropped/closed Deepgram socket)."""

    def __init__(self, transcripts: list[str]) -> None:
        self._transcripts = transcripts

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for t in self._transcripts:
            yield _results_msg(t)


def _results_msg(text: str) -> ListenV1Results:
    """A real ``ListenV1Results`` — validated construction, so this fake can't
    drift from the schema ``_normalize`` reads."""
    return ListenV1Results(
        channel_index=[0, 1],
        duration=1.0,
        start=0.0,
        is_final=True,
        speech_final=False,
        channel=ListenV1ResultsChannel(
            alternatives=[
                ListenV1ResultsChannelAlternativesItem(
                    transcript=text, confidence=0.99, words=[]
                )
            ]
        ),
        metadata=_METADATA,
    )


async def _drain(stt: DeepgramSTT, n: int) -> list:
    out = []
    gen = stt.events()
    for _ in range(n):
        out.append(await asyncio.wait_for(gen.__anext__(), timeout=1.0))
    return out


# --- Message normalization --------------------------------------------------


def _from_wire(payload: dict) -> V1SocketClientResponse:
    """Parse a raw payload through the SDK's own wire path — construct_type on
    an UncheckedBaseModel — rather than hand-rolling a half-built model, so
    these stay true to what the socket actually yields."""
    return construct_type(
        # construct_type declares type_ as Type[Any]; the discriminated union
        # it is called with in socket_client.py isn't a class object.
        type_=cast(type, V1SocketClientResponse),
        object_=payload,
    )


def test_normalize_results_reads_first_alternative() -> None:
    event = DeepgramSTT()._normalize(_results_msg("hello there"))  # noqa: SLF001
    assert event == Transcript(text="hello there", is_final=True, speech_final=False)


def test_normalize_maps_turn_signals() -> None:
    stt = DeepgramSTT()
    speech_started = ListenV1SpeechStarted(channel=[0, 1], timestamp=1.5)
    utterance_end = ListenV1UtteranceEnd(channel=[0, 1], last_word_end=2.5)

    assert stt._normalize(speech_started) == SpeechStarted()  # noqa: SLF001
    assert stt._normalize(utterance_end) == UtteranceEnd()  # noqa: SLF001


def test_normalize_ignores_metadata_and_binary_frames() -> None:
    stt = DeepgramSTT()
    metadata = ListenV1Metadata(
        transaction_key="deprecated",
        request_id="00000000-0000-0000-0000-000000000000",
        sha256="",
        created="2026-07-25T00:00:00.000Z",
        duration=1.0,
        channels=1,
    )

    assert stt._normalize(metadata) is None  # noqa: SLF001
    assert stt._normalize(b"\x00\x01") is None  # noqa: SLF001


def test_normalize_empty_alternatives_is_silent() -> None:
    # Schema-valid, just carries no hypothesis: empty transcript, no warning.
    msg = _from_wire({"type": "Results", "channel": {"alternatives": []}})

    stt = DeepgramSTT()
    assert stt._normalize(msg) == Transcript(  # noqa: SLF001
        text="", is_final=False, speech_final=False
    )
    assert stt._warned_schema_drift is False  # noqa: SLF001


def test_normalize_survives_required_fields_arriving_as_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The SDK does not validate, so a field the schema declares as required
    # can arrive as None. Session._consume_stt calls ev.text.strip(), so a
    # None reaching Transcript.text would raise there rather than here.
    for payload in (
        {"type": "Results", "is_final": True},  # channel absent
        {"type": "Results", "channel": {}},  # alternatives absent
        {"type": "Results", "channel": {"alternatives": [{}]}},  # transcript absent
        {"type": "Results", "channel": {"alternatives": [{"transcript": None}]}},
    ):
        caplog.clear()  # each payload must warn on its own
        with caplog.at_level(logging.WARNING):
            event = DeepgramSTT()._normalize(_from_wire(payload))  # noqa: SLF001

        assert isinstance(event, Transcript), payload
        assert event.text == "", payload
        assert "declares as required" in caplog.text, payload


def test_normalize_preserves_turn_end_signal_on_malformed_payload() -> None:
    # A broken final message must still carry speech_final through, or the
    # buffered utterance strands until UtteranceEnd fires.
    msg = _from_wire({"type": "Results", "is_final": True, "speech_final": True})

    assert DeepgramSTT()._normalize(msg) == Transcript(  # noqa: SLF001
        text="", is_final=True, speech_final=True
    )


def test_schema_drift_warning_is_logged_once_per_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Interim results arrive several times a second; warning on each would
    # bury the signal.
    stt = DeepgramSTT()
    msg = _from_wire({"type": "Results", "channel": {"alternatives": [{}]}})

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            stt._normalize(msg)  # noqa: SLF001

    warnings = [r for r in caplog.records if "declares as required" in r.getMessage()]
    assert len(warnings) == 1


# --- Reconnect --------------------------------------------------------------


async def test_reconnects_on_unexpected_socket_end() -> None:
    # Each opened socket yields one transcript then ends; the reader should
    # reopen and keep producing until we've seen transcripts from 2 sockets.
    sockets = [_FakeSocket(["first"]), _FakeSocket(["second"]), _FakeSocket(["third"])]
    opened = {"n": 0}

    stt = DeepgramSTT(api_key="x")

    async def fake_open() -> None:
        stt._socket = sockets[opened["n"]]  # noqa: SLF001 - test seam
        opened["n"] += 1

    async def fake_close() -> None:
        pass

    stt._open_socket = fake_open  # noqa: SLF001
    stt._close_socket = fake_close  # noqa: SLF001
    stt._reconnect_delays = [0.0, 0.0, 0.0]  # noqa: SLF001 - no backoff in tests

    await stt.start()
    events = await _drain(stt, 2)
    assert [e.text for e in events if isinstance(e, Transcript)] == ["first", "second"]
    assert opened["n"] >= 2
    await stt.aclose()


async def test_emits_sttclosed_when_reconnect_exhausted() -> None:
    stt = DeepgramSTT(api_key="x")
    opened = {"n": 0}

    async def fake_open() -> None:
        # First open succeeds with an immediately-ending socket; every reopen
        # raises to simulate the endpoint being unreachable.
        if opened["n"] == 0:
            stt._socket = _FakeSocket([])  # noqa: SLF001 - ends immediately
            opened["n"] += 1
            return
        raise RuntimeError("connection refused")

    async def fake_close() -> None:
        # Mirror the real _close_socket: a failed reopen therefore leaves the
        # reader with no socket at all, which must still count as a drop.
        stt._socket = None  # noqa: SLF001

    stt._open_socket = fake_open  # noqa: SLF001
    stt._close_socket = fake_close  # noqa: SLF001
    stt._reconnect_delays = [0.0, 0.0, 0.0]  # noqa: SLF001

    await stt.start()
    gen = stt.events()
    ev = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert isinstance(ev, SttClosed)
    await stt.aclose()


async def test_finish_prevents_reconnect() -> None:
    stt = DeepgramSTT(api_key="x")
    opened = {"n": 0}

    async def fake_open() -> None:
        stt._socket = _FakeSocket(["only"])  # noqa: SLF001
        opened["n"] += 1

    async def fake_close() -> None:
        pass

    stt._open_socket = fake_open  # noqa: SLF001
    stt._close_socket = fake_close  # noqa: SLF001

    await stt.start()
    # Mark intentional stop before the socket's single message drains out.
    await stt.finish()
    await asyncio.sleep(0.05)
    # Only the initial open happened; no reconnect after the socket ended.
    assert opened["n"] == 1
    await stt.aclose()


# --- Session-level handling of a terminal SttClosed -------------------------


class _ClosingSTT:
    """FakeSTTProvider variant that emits a single SttClosed then blocks."""

    def __init__(self) -> None:
        self.closed = False
        self._q: asyncio.Queue = asyncio.Queue()
        self._q.put_nowait(SttClosed())

    async def start(self) -> None: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def finish(self) -> None: ...

    async def events(self):
        while True:
            yield await self._q.get()

    async def aclose(self) -> None:
        self.closed = True


async def test_session_surfaces_sttclosed_and_goes_idle() -> None:
    fake_ws = FakeWebSocket()
    session = Session(fake_ws)
    session.client = FakeOpenAI()
    session._recorder = FakeEventRecorder()  # noqa: SLF001 - keep DB out of fake-ws timing
    session.tts = FakeTTSProvider()
    closing = _ClosingSTT()
    session._make_stt_provider = lambda: closing  # noqa: SLF001

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await asyncio.sleep(0.05)
    fake_ws.queue_disconnect()
    await asyncio.wait_for(run_task, timeout=1.0)

    types = [m["type"] for m in fake_ws.sent]
    assert "error" in types
    # An idle state frame was emitted after the error.
    assert any(m["type"] == "state" and m["state"] == "idle" for m in fake_ws.sent)
    assert session.stt is None
    assert closing.closed is True
