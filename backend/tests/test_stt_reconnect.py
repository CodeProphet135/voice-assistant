"""Reconnect behavior for DeepgramSTT (Phase 5) + the session-level handling
of a terminal SttClosed event. No real Deepgram SDK or key is involved — the
socket + connect step are faked via monkeypatching the provider's open seam.
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from types import SimpleNamespace

from conftest import FakeEventRecorder, FakeOpenAI, FakeTTSProvider, FakeWebSocket

from voice_assistant.providers.base import SttClosed, Transcript
from voice_assistant.providers.deepgram import DeepgramSTT
from voice_assistant.session import Session


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


def _results_msg(text: str):
    alt = SimpleNamespace(transcript=text)
    channel = SimpleNamespace(alternatives=[alt])
    return SimpleNamespace(
        type="Results", channel=channel, is_final=True, speech_final=False
    )


async def _drain(stt: DeepgramSTT, n: int) -> list:
    out = []
    gen = stt.events()
    for _ in range(n):
        out.append(await asyncio.wait_for(gen.__anext__(), timeout=1.0))
    return out


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
        pass

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
