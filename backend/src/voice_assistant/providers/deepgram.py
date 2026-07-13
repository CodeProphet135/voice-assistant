"""Deepgram streaming STT — the only concrete ``STTProvider`` in v1.

Uses the installed ``deepgram-sdk`` 7.x generated client, which is a very
different surface from the older v3-style SDK: ``client.listen.v1.connect()``
is an *async context manager* yielding an ``AsyncV1SocketClient``, and
received messages are pydantic models discriminated by a ``.type`` string
(``"Results"``, ``"SpeechStarted"``, ``"UtteranceEnd"``, ``"Metadata"``)
rather than callback-registered event classes. None of that vendor shape
leaks past this file — ``Session`` only ever sees ``providers.base`` types.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from deepgram import AsyncDeepgramClient

from voice_assistant.config import settings
from voice_assistant.providers.base import (
    SpeechStarted,
    SttClosed,
    SttEvent,
    Transcript,
    UtteranceEnd,
)

_logger = logging.getLogger(__name__)

# Established conventions (CLAUDE.md) — turn detection is Deepgram's job, no
# custom VAD.
_ENCODING = "linear16"
_SAMPLE_RATE = 16000
_CHANNELS = 1
_ENDPOINTING_MS = 300
_UTTERANCE_END_MS = 1000

# Bounded auto-reconnect on an unexpected mid-conversation socket drop.
_MAX_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAYS = [0.5, 1.0, 2.0]  # seconds, indexed by (attempt - 1)

# TTS conventions (CLAUDE.md) — Aura REST, synthesized per sentence.
_TTS_ENCODING = "linear16"
_TTS_SAMPLE_RATE = 24000
_TTS_CONTAINER = "none"


class DeepgramSTT:
    """Concrete ``STTProvider`` wrapping a live Deepgram streaming socket.

    Constructed cheaply (no network I/O, no client) so it can be created
    unconditionally; the actual connection is only opened in ``start()``,
    which means the text-chat path and unit tests never need a Deepgram API
    key.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.deepgram_api_key
        self._model = model if model is not None else settings.deepgram_stt_model
        self._client: AsyncDeepgramClient | None = None
        self._connect_cm = None
        self._socket = None
        self._queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._closing = False
        self._reconnect_delays = _RECONNECT_DELAYS

    async def start(self) -> None:
        """Open the Deepgram streaming socket and start the background reader
        task that translates raw SDK messages into normalized events and
        transparently reconnects on an unexpected drop."""
        self._closing = False
        await self._open_socket()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _open_socket(self) -> None:
        self._client = AsyncDeepgramClient(api_key=self._api_key)
        self._connect_cm = self._client.listen.v1.connect(
            model=self._model,
            encoding=_ENCODING,
            sample_rate=_SAMPLE_RATE,
            channels=_CHANNELS,
            language="en",
            interim_results=True,
            endpointing=_ENDPOINTING_MS,
            utterance_end_ms=_UTTERANCE_END_MS,
            vad_events=True,
            punctuate=True,
        )
        self._socket = await self._connect_cm.__aenter__()

    async def _close_socket(self) -> None:
        cm = self._connect_cm
        self._connect_cm = None
        self._socket = None
        if cm is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort teardown
                await cm.__aexit__(None, None, None)

    async def _read_loop(self) -> None:
        """Consume raw Deepgram messages onto the queue, transparently
        reconnecting on an unexpected socket end. On intentional stop
        (``finish``/``aclose`` set ``_closing``) it exits cleanly; when bounded
        reconnect is exhausted it enqueues a terminal ``SttClosed``."""
        attempt = 0
        while not self._closing:
            try:
                async for msg in self._socket:
                    attempt = 0  # any received message resets the budget
                    event = self._normalize(msg)
                    if event is not None:
                        await self._queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - vendor socket errors must not crash the caller
                _logger.warning("Deepgram STT socket reader errored", exc_info=True)

            if self._closing:
                break
            if attempt >= _MAX_RECONNECT_ATTEMPTS:
                _logger.error("Deepgram STT reconnect exhausted")
                await self._queue.put(SttClosed())
                break
            delay = self._reconnect_delays[min(attempt, len(self._reconnect_delays) - 1)]
            attempt += 1
            _logger.warning("Deepgram STT dropped; reconnecting (attempt %d)", attempt)
            await asyncio.sleep(delay)
            await self._close_socket()
            try:
                await self._open_socket()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reopen failed; loop retries or exhausts
                _logger.warning("Deepgram STT reconnect open failed", exc_info=True)

    @staticmethod
    def _normalize(msg: object) -> SttEvent | None:
        msg_type = getattr(msg, "type", None)
        if msg_type == "Results":
            alternatives = msg.channel.alternatives if msg.channel else []
            text = alternatives[0].transcript if alternatives else ""
            return Transcript(
                text=text,
                is_final=bool(msg.is_final),
                speech_final=bool(msg.speech_final),
            )
        if msg_type == "SpeechStarted":
            return SpeechStarted()
        if msg_type == "UtteranceEnd":
            return UtteranceEnd()
        # "Metadata" and anything unrecognized: ignore for transcript purposes.
        return None

    async def send_audio(self, pcm: bytes) -> None:
        if self._socket is None:
            return
        try:
            await self._socket.send_media(pcm)
        except Exception:  # noqa: BLE001 - never let a send failure crash the caller
            _logger.warning("Failed to send audio to Deepgram", exc_info=True)

    async def finish(self) -> None:
        # Mark intentional stop first so the reader treats the coming socket
        # end as expected (no reconnect), not as a drop.
        self._closing = True
        if self._socket is None:
            return
        with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort close signal
            await self._socket.send_close_stream()

    async def events(self):
        """Drain normalized events as they arrive. Stops naturally once the
        reader task finishes and the queue is empty for the caller's
        current iteration (callers own their own loop/cancellation)."""
        while True:
            event = await self._queue.get()
            yield event

    async def aclose(self) -> None:
        self._closing = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):  # noqa: BLE001
                await self._reader_task
            self._reader_task = None
        await self._close_socket()


class DeepgramTTS:
    """Concrete ``TTSProvider`` wrapping Deepgram's Aura REST speak endpoint.

    Constructed cheaply (no network I/O, no client) so it can be created
    unconditionally; the ``AsyncDeepgramClient`` is only built lazily inside
    ``synthesize()``, which means a bare ``DeepgramTTS()`` never needs a
    Deepgram API key at construction time.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.deepgram_api_key
        self._model = model if model is not None else settings.deepgram_tts_model

    def _make_client(self) -> AsyncDeepgramClient:
        return AsyncDeepgramClient(api_key=self._api_key)

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Synthesize one sentence via the Aura REST endpoint, yielding raw
        PCM chunks as they arrive. Never let a vendor-side error propagate —
        degrade silently (stop yielding), same as the STT reader's guard."""
        try:
            client = self._make_client()
            audio_iter = client.speak.v1.audio.generate(
                text=text,
                model=self._model,
                encoding=_TTS_ENCODING,
                sample_rate=_TTS_SAMPLE_RATE,
                container=_TTS_CONTAINER,
            )
            async for chunk in audio_iter:
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - vendor errors must not crash the caller
            _logger.warning("Deepgram TTS synthesis failed", exc_info=True)

    async def aclose(self) -> None:
        """No persistent connection to tear down (each ``synthesize()`` call
        builds its own short-lived client), but kept for seam symmetry with
        ``STTProvider`` and to absorb any future stateful client."""
        return
