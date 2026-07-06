"""The provider seam — a thin async ``Protocol`` demonstrating vendor
swappability.

Deepgram is the only concrete implementation in v1 (``deepgram.py``). This
module exists so the Deepgram SDK's own types never leak past ``providers/``:
``Session`` and the rest of the app only ever see the normalized dataclasses
below. Don't add a second STT provider unless asked — the seam exists to
*demonstrate* swappability, not to be exercised.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transcript:
    """A normalized speech-to-text hypothesis.

    ``is_final`` distinguishes an interim (still-changing) hypothesis from a
    finalized segment; ``speech_final`` additionally marks the end of a
    spoken turn (Deepgram's endpointing decision) and is the primary signal
    ``Session`` uses to commit a turn and launch the agent.
    """

    text: str
    is_final: bool
    speech_final: bool


@dataclass
class SpeechStarted:
    """Normalized turn-start signal (Deepgram ``SpeechStarted``).

    Not acted on until Phase 3 barge-in, but cheap to model now so the event
    union doesn't need to change shape later.
    """


@dataclass
class UtteranceEnd:
    """Normalized turn-end fallback signal (Deepgram ``UtteranceEnd``).

    Fires when the provider decides an utterance is over even if a
    ``Transcript`` with ``speech_final=True`` never arrived (e.g. trailing
    silence after a final-but-not-speech-final segment) — ``Session`` uses
    this as a fallback turn-commit trigger.
    """


SttEvent = Transcript | SpeechStarted | UtteranceEnd


class STTProvider(Protocol):
    """Async speech-to-text provider seam.

    Lifecycle: ``start()`` opens the connection, ``send_audio()`` streams
    mic PCM in, ``finish()`` signals no more audio is coming (flush/close the
    input side), ``events()`` yields normalized events as they arrive, and
    ``aclose()`` tears everything down. Implementations must never let a
    vendor-side error/disconnect propagate as a crash — degrade silently
    (stop yielding events) instead.
    """

    async def start(self) -> None:
        """Open the underlying connection. Must be safe to call lazily —
        no vendor client should be constructed at import time or before
        this is called."""
        ...

    async def send_audio(self, pcm: bytes) -> None:
        """Stream a chunk of raw PCM audio to the provider."""
        ...

    async def finish(self) -> None:
        """Signal that no more audio will be sent; flush/close the input
        side of the connection (the provider may still emit trailing
        events after this)."""
        ...

    def events(self) -> AsyncIterator[SttEvent]:
        """Yield normalized transcript/turn events as they arrive."""
        ...

    async def aclose(self) -> None:
        """Tear down the connection and any background tasks. Safe to call
        even if ``start()`` was never called or already failed."""
        ...
