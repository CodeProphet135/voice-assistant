"""Tests for the echo guard (fix option 2, docs/bug-self-barge-in-echo.md):
transcripts that match what the assistant is currently/recently speaking are
our own TTS audio leaking back into the mic, not genuine user speech, and
must be dropped instead of barging in or committing as a user turn.

Reuses the barge-in fake harness (BargeInResponses/BargeInClient/make_session
from test_session_barge_in.py, itself built on the FakeSTTProvider/
_GatedStream fakes from test_session_stt.py) plus conftest.py's
make_text_turn for plain (non-gated) scripted turns.
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from conftest import FakeWebSocket, make_completed_event, make_text_delta_event, make_text_turn
from test_session_barge_in import BargeInClient, _drive, make_session
from test_session_stt import FakeSTTProvider

from voice_assistant.providers.base import Transcript
from voice_assistant.session import Session, _normalize_for_echo

# The sentence the gated turn "speaks" before freezing -- a complete sentence
# (terminal punctuation + trailing space) so the chunker flushes it to the
# TTS queue immediately, before the stream blocks on `release`.
SPOKEN_SENTENCE = "Here is a fun fact about Rome. "


async def _start_turn_with_spoken_sentence(
    fake_ws: FakeWebSocket,
    session: Session,
    fake_stt: FakeSTTProvider,
    release: asyncio.Event,
    utterance: str = "tell me a fun fact",
) -> asyncio.Task:
    """Boot the session and commit an utterance whose gated agent reply has
    already spoken one full sentence (``SPOKEN_SENTENCE``, present in
    ``session._spoken_recent``) before freezing mid-stream."""
    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    fake_stt.push(Transcript(text=utterance, is_final=True, speech_final=True))
    await _drive(30)

    assert session._turn_active(), "expected the gated turn to still be running"
    assert any(e["type"] == "tts_start" for e in fake_ws.sent), (
        "expected the spoken sentence to have reached the TTS queue before the stream gated"
    )
    return run_task


def _make_gated_session(fake_ws: FakeWebSocket, release: asyncio.Event):
    client = BargeInClient()
    client.responses.script_gated(
        [make_text_delta_event(SPOKEN_SENTENCE)],
        release,
        [make_text_delta_event("more "), make_completed_event(output=[])],
    )
    return make_session(fake_ws, client) + (client,)


# --- 1. Echo interim while speaking -----------------------------------------


async def test_echo_interim_while_speaking_is_dropped_silently() -> None:
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(fake_ws, release)

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    fake_stt.push(
        Transcript(text="here is a fun fact about rome", is_final=False, speech_final=False)
    )
    await _drive(20)

    assert not any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    assert not any(e["type"] == "stt_partial" for e in fake_ws.sent)
    assert session._turn_active()

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


# --- 2. Genuine >=2-word interim while speaking still barges in ------------


async def test_genuine_interim_while_speaking_still_barges_in() -> None:
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(fake_ws, release)

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    fake_stt.push(Transcript(text="wait stop", is_final=False, speech_final=False))
    await _drive(20)

    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    partial_events = [e for e in fake_ws.sent if e["type"] == "stt_partial"]
    assert {"type": "stt_partial", "text": "wait stop"} in partial_events

    fake_ws.queue_disconnect()
    await run_task


# --- 3. Echo FINAL matching spoken text: no commit --------------------------


async def test_echo_final_while_speaking_does_not_commit() -> None:
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(fake_ws, release)

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)
    calls_before = len(client.responses.calls)
    # Committing the utterance that started this (gated) turn already
    # produced one stt_final -- baseline before pushing the echo.
    stt_final_count_before = len([e for e in fake_ws.sent if e["type"] == "stt_final"])

    fake_stt.push(
        Transcript(text="here is a fun fact about rome", is_final=True, speech_final=True)
    )
    await _drive(20)

    stt_final_count_after = len([e for e in fake_ws.sent if e["type"] == "stt_final"])
    assert stt_final_count_after == stt_final_count_before, "the echo must not commit"
    assert len(client.responses.calls) == calls_before, "the echo must not launch a new turn"
    assert session._turn_active(), "the original (echoed-at) turn is still the active one"

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


# --- 4. Late echo final AFTER the turn completed: still dropped -----------


async def test_late_echo_final_after_turn_completes_is_dropped() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    client.responses.script(make_text_turn("Here is a fun fact about Rome."))
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    fake_stt.push(Transcript(text="tell me a fun fact", is_final=True, speech_final=True))
    await _drive(40)

    assert any(e["type"] == "tts_end" for e in fake_ws.sent)
    assert not session._turn_active()
    calls_before = len(client.responses.calls)
    stt_final_count_before = len([e for e in fake_ws.sent if e["type"] == "stt_final"])

    # Late echo arrives after the turn already finished (playback + STT
    # latency separate the tts_end from when the echo lands) -- must still
    # be dropped, not committed as a fresh turn.
    fake_stt.push(
        Transcript(text="here is a fun fact about rome", is_final=True, speech_final=True)
    )
    await _drive(20)

    stt_final_count_after = len([e for e in fake_ws.sent if e["type"] == "stt_final"])
    assert stt_final_count_after == stt_final_count_before
    assert len(client.responses.calls) == calls_before

    fake_ws.queue_disconnect()
    await run_task


# --- 5. Genuine final after a completed turn commits a fresh turn ---------


async def test_genuine_final_after_turn_completes_commits_fresh_turn() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    client.responses.script(make_text_turn("Here is a fun fact about Rome."))
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    fake_stt.push(Transcript(text="tell me a fun fact", is_final=True, speech_final=True))
    await _drive(40)
    assert any(e["type"] == "tts_end" for e in fake_ws.sent)

    client.responses.script(
        [
            make_text_delta_event("Sure "),
            make_text_delta_event("thing"),
            make_completed_event(output=[]),
        ]
    )
    fake_stt.push(Transcript(text="tell me another one", is_final=True, speech_final=True))
    await _drive(40)

    stt_final_events = [e for e in fake_ws.sent if e["type"] == "stt_final"]
    assert stt_final_events[-1] == {"type": "stt_final", "text": "tell me another one"}
    assert len(stt_final_events) == 2, "both the original commit and this fresh one landed"
    done_events = [e for e in fake_ws.sent if e["type"] == "assistant_done"]
    assert len(done_events) == 2
    assert done_events[-1]["text"] == "Sure thing"

    fake_ws.queue_disconnect()
    await run_task


# --- 6. Fuzzy echo: mis-transcribed punctuation/casing is still dropped ---


async def test_fuzzy_echo_final_with_punctuation_differences_is_dropped() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    release = asyncio.Event()
    client.responses.script_gated(
        [make_text_delta_event("Nice—hit me with it! ")],
        release,
        [make_text_delta_event("more "), make_completed_event(output=[])],
    )
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    run_task = await _start_turn_with_spoken_sentence(
        fake_ws, session, fake_stt, release, utterance="nice"
    )
    calls_before = len(client.responses.calls)
    stt_final_count_before = len([e for e in fake_ws.sent if e["type"] == "stt_final"])

    # Deepgram's own mis-transcription of the assistant's echoed voice --
    # different casing/punctuation than what was actually spoken.
    fake_stt.push(Transcript(text="nice hit", is_final=True, speech_final=True))
    await _drive(20)

    stt_final_count_after = len([e for e in fake_ws.sent if e["type"] == "stt_final"])
    assert stt_final_count_after == stt_final_count_before
    assert len(client.responses.calls) == calls_before

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


# --- 7. Non-echo phrase sharing a couple of words is NOT dropped ----------


async def test_non_echo_phrase_sharing_words_commits_normally() -> None:
    fake_ws = FakeWebSocket()
    client = BargeInClient()
    client.responses.script(make_text_turn("Here is a fun fact about Rome."))
    session, fake_stt, fake_tts = make_session(fake_ws, client)

    fake_ws.queue_text('{"type": "start", "sample_rate": 16000}')
    run_task = asyncio.create_task(session.run())
    await _drive(3)

    fake_stt.push(Transcript(text="tell me a fun fact", is_final=True, speech_final=True))
    await _drive(40)
    assert any(e["type"] == "tts_end" for e in fake_ws.sent)

    client.responses.script(
        [make_text_delta_event("Sure "), make_completed_event(output=[])]
    )
    # Shares "fun"/"fact" with the spoken sentence but well under the 80%
    # word-overlap threshold -- must commit as a genuine new turn.
    fake_stt.push(Transcript(text="tell me another fun fact", is_final=True, speech_final=True))
    await _drive(40)

    stt_final_events = [e for e in fake_ws.sent if e["type"] == "stt_final"]
    assert stt_final_events[-1] == {"type": "stt_final", "text": "tell me another fun fact"}
    assert len(stt_final_events) == 2

    fake_ws.queue_disconnect()
    await run_task


# --- 8. Unit tests for the normalizer / _is_echo helper --------------------


def test_normalize_for_echo_separators_vs_apostrophes() -> None:
    assert _normalize_for_echo("Here's a fun one.") == "heres a fun one"
    assert _normalize_for_echo("Nice—hit me with it!") == "nice hit me with it"


def _bare_session() -> Session:
    return Session(FakeWebSocket())


def test_is_echo_worked_examples_from_brief() -> None:
    session = _bare_session()
    session._spoken_recent.append(
        "Here's a fun one: Roman concrete was so durable because it self-heals."
    )
    session._spoken_recent.append("Nice—hit me with it!")

    assert session._is_echo("Here's a fun one.") is True
    assert session._is_echo("Nice hit.") is True
    assert session._is_echo("Wait. Stop. What is your name?") is False
    assert session._is_echo("Tell me another fun fact") is False


def test_is_echo_empty_spoken_recent_returns_false() -> None:
    session = _bare_session()
    assert session._is_echo("anything at all") is False


def test_is_echo_empty_normalized_transcript_returns_false() -> None:
    session = _bare_session()
    session._spoken_recent.append("Hello there.")
    assert session._is_echo("...") is False


def test_is_echo_fuzzy_word_substitution_meets_threshold() -> None:
    session = _bare_session()
    session._spoken_recent.append("Nice hit me with it")
    # 4/5 words match (80%) -- meets the fuzzy fallback threshold even
    # though it's not a substring.
    assert session._is_echo("nice hit me with sit") is True


def test_is_echo_fuzzy_word_substitution_below_threshold() -> None:
    session = _bare_session()
    session._spoken_recent.append("Nice hit me with it")
    # Only 2/5 words match (40%) -- must not classify as echo.
    assert session._is_echo("nice sit see with sat") is False
