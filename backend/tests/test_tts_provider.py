"""Tests for the Deepgram TTS provider (Phase 3 voice OUT).

Uses a fake Deepgram client injected via ``DeepgramTTS._make_client()`` —
mirrors the injection style used for ``Session._make_stt_provider`` in
test_session_stt.py. No Deepgram key or real network I/O is involved.
"""

from voice_assistant.providers.deepgram import DeepgramTTS


class FakeAudioClient:
    """Stand-in for ``client.speak.v1.audio`` — ``generate()`` captures its
    kwargs and re-yields a scripted list of byte chunks (or raises)."""

    def __init__(self, chunks: list[bytes] | None = None, error: Exception | None = None) -> None:
        self._chunks = chunks if chunks is not None else []
        self._error = error
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        for chunk in self._chunks:
            yield chunk


class FakeSpeakV1:
    def __init__(self, audio: FakeAudioClient) -> None:
        self.audio = audio


class FakeSpeak:
    def __init__(self, audio: FakeAudioClient) -> None:
        self.v1 = FakeSpeakV1(audio)


class FakeDeepgramClient:
    """Stand-in for ``AsyncDeepgramClient`` exposing just ``.speak``."""

    def __init__(self, audio: FakeAudioClient) -> None:
        self.speak = FakeSpeak(audio)


def make_tts(audio: FakeAudioClient) -> DeepgramTTS:
    tts = DeepgramTTS()
    fake_client = FakeDeepgramClient(audio)
    tts._make_client = lambda: fake_client  # noqa: SLF001 - test injection seam
    return tts


async def test_synthesize_yields_chunks_in_order() -> None:
    audio = FakeAudioClient(chunks=[b"\x01\x02", b"\x03\x04"])
    tts = make_tts(audio)

    chunks = [chunk async for chunk in tts.synthesize("Hi.")]

    assert chunks == [b"\x01\x02", b"\x03\x04"]


async def test_synthesize_calls_generate_with_expected_kwargs() -> None:
    audio = FakeAudioClient(chunks=[b"\x01"])
    tts = make_tts(audio)

    [_ async for _ in tts.synthesize("Hi.")]

    assert len(audio.calls) == 1
    call = audio.calls[0]
    assert call["text"] == "Hi."
    assert call["encoding"] == "linear16"
    assert call["sample_rate"] == 24000
    assert call["container"] == "none"
    assert call["model"] == tts._model  # noqa: SLF001 - asserting configured model used


async def test_synthesize_degrades_silently_on_vendor_error() -> None:
    audio = FakeAudioClient(error=RuntimeError("boom"))
    tts = make_tts(audio)

    chunks = [chunk async for chunk in tts.synthesize("Hi.")]

    assert chunks == []


async def test_bare_construction_requires_no_key() -> None:
    # Must not raise even with no Deepgram key set anywhere.
    tts = DeepgramTTS()
    assert tts is not None


async def test_aclose_is_safe_to_call() -> None:
    tts = DeepgramTTS()
    await tts.aclose()
