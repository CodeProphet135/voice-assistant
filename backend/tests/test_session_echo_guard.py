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


def _make_gated_session(
    fake_ws: FakeWebSocket, release: asyncio.Event, sentence: str = SPOKEN_SENTENCE
):
    client = BargeInClient()
    client.responses.script_gated(
        [make_text_delta_event(sentence)],
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


# --- 2. Genuine >=3-word interim while speaking still barges in ------------


async def test_genuine_interim_while_speaking_still_barges_in() -> None:
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(fake_ws, release)

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    fake_stt.push(Transcript(text="wait stop please", is_final=False, speech_final=False))
    await _drive(20)

    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    partial_events = [e for e in fake_ws.sent if e["type"] == "stt_partial"]
    assert {"type": "stt_partial", "text": "wait stop please"} in partial_events

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
    # Shares "fun"/"fact" with the spoken sentence but well under the 60%
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
    # 4/5 words match (80% >= the 0.6 threshold) even though it's not a
    # substring.
    assert session._is_echo("nice hit me with sit") is True


def test_is_echo_fuzzy_word_substitution_below_threshold() -> None:
    session = _bare_session()
    session._spoken_recent.append("Nice hit me with it")
    # Only 2/5 words match (40% < the 0.6 threshold) -- must not classify
    # as echo.
    assert session._is_echo("nice sit see with sat") is False


# Exact spoken sentence + echo interim from live mic_sim.py --echo run 1
# that slipped past the first version of the guard: Deepgram compounded
# "under water" into "underwater", so the transcript was neither a
# (space-sensitive) substring of the spoken text nor above the fuzzy
# word-overlap threshold.
_LIVE_SPOKEN_SENTENCE = (
    "The Romans used a concrete recipe that actually got stronger under water, "
    "which is why many of their harbors and sea walls have survived for two "
    "thousand years."
)

# Exact spoken sentence from live run 2: Deepgram MISHEARD the echo ("A fun"
# -> "The fun", "Roman" -> "Rome"), so even segmentation tolerance couldn't
# push the overlap past the original 0.8 threshold -- the observed clean
# separation (misheard echo >=60%, genuine phrases <=~30%) is why the
# threshold is 0.6.
_LIVE_SPOKEN_SENTENCE_2 = (
    "A fun fact: Roman concrete used volcanic ash and seawater to make harbors "
    "that actually got stronger over time, and some of those structures still "
    "survive after two thousand years."
)


def test_is_echo_survives_word_segmentation_differences() -> None:
    """Regression (live echo run 1): one word-segmentation difference in
    Deepgram's transcription of our own audio must not defeat the guard."""
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE)
    assert session._is_echo("got stronger underwater, which") is True


def test_is_echo_segmentation_tolerance_does_not_catch_genuine_speech() -> None:
    """Inverse guard: the space-insensitive matching must not classify a
    genuine phrase that merely shares a compound word with the spoken text
    as echo ("underwater" alone matches space-stripped "under water", but
    that's 1/4 words -- far below the threshold)."""
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE)
    assert session._is_echo("what about underwater cities") is False


def test_is_echo_misheard_final_meets_lowered_threshold() -> None:
    """Regression (live echo run 2): Deepgram misheard "Roman" as "Rome" in
    the echoed final "A fun fact. Rome" -- 3/4 words overlap (75%), which
    the original 0.8 threshold let through to commit as a user turn."""
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE_2)
    assert session._is_echo("A fun fact. Rome") is True


def test_is_echo_misheard_short_interim_stays_unclassified() -> None:
    """The live run-2 interim "The fun" (misheard "A fun") scores only 1/2
    (50%) -- below even the lowered threshold. That's fine: fragments this
    short can't be reliably text-classified, which is why 2-word interims
    only barge when their echo score is near zero (the strict tier -- see
    the integration tests below)."""
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE_2)
    assert session._is_echo("The fun") is False


def test_is_echo_compact_substring_requires_min_length() -> None:
    """The space-insensitive substring branch only applies to compact
    transcripts >= 6 chars, guarding trivial matches: "a bit" (compact
    "abit") appears inside "habitually" but is plainly not an echo."""
    session = _bare_session()
    session._spoken_recent.append("Habitually speaking.")
    assert session._is_echo("a bit") is False


# --- 9. Live-run regressions (integration) ---------------------------------


async def test_misheard_short_echo_interim_does_not_barge() -> None:
    """Live run 2: the echo interim "The fun" isn't classifiable as echo
    (50% overlap), so it IS surfaced as a partial -- but as a 2-word interim
    whose echo score (0.5) is above the strict tier's near-zero bar, it must
    not cancel the turn."""
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(
        fake_ws, release, sentence=f"{_LIVE_SPOKEN_SENTENCE_2} "
    )

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    fake_stt.push(Transcript(text="The fun", is_final=False, speech_final=False))
    await _drive(20)

    assert not any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    partial_events = [e for e in fake_ws.sent if e["type"] == "stt_partial"]
    assert {"type": "stt_partial", "text": "The fun"} in partial_events
    assert session._turn_active()

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


async def test_misheard_echo_final_does_not_commit() -> None:
    """Live run 2: the echoed final "A fun fact. Rome" (misheard "Roman")
    committed as a user turn at the 0.8 threshold -- at 0.6 it must be
    dropped: no stt_final, no new agent call."""
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(
        fake_ws, release, sentence=f"{_LIVE_SPOKEN_SENTENCE_2} "
    )

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)
    calls_before = len(client.responses.calls)
    stt_final_count_before = len([e for e in fake_ws.sent if e["type"] == "stt_final"])

    fake_stt.push(Transcript(text="A fun fact. Rome", is_final=True, speech_final=True))
    await _drive(20)

    stt_final_count_after = len([e for e in fake_ws.sent if e["type"] == "stt_final"])
    assert stt_final_count_after == stt_final_count_before, "the misheard echo must not commit"
    assert len(client.responses.calls) == calls_before

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


async def test_resegmented_echo_interim_is_dropped() -> None:
    """Live run 1: the echo interim "got stronger underwater, which"
    (Deepgram compounded "under water") is echo via the space-insensitive
    substring check -- dropped entirely: no barge-in, no partial."""
    fake_ws = FakeWebSocket()
    release = asyncio.Event()
    session, fake_stt, fake_tts, client = _make_gated_session(
        fake_ws, release, sentence=f"{_LIVE_SPOKEN_SENTENCE} "
    )

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    fake_stt.push(
        Transcript(text="got stronger underwater, which", is_final=False, speech_final=False)
    )
    await _drive(20)

    assert not any(e["type"] == "tts_cancel" for e in fake_ws.sent)
    assert not any(e["type"] == "stt_partial" for e in fake_ws.sent)
    assert session._turn_active()

    release.set()
    await _drive(30)
    fake_ws.queue_disconnect()
    await run_task


# --- 10. Addendum 2 (live run 3): genuine final barges; strict 2-word tier --


async def test_genuine_final_while_speaking_barges_in_then_commits() -> None:
    """Live run 3: Deepgram's interims stopped at 2 words and jumped straight
    to the final "Wait. Stop. What is your name?", so the interim gate never
    fired and the assistant spoke over the user to completion. A genuine
    (non-echo) final committing while a turn is active must barge in first
    (tts_cancel before stt_final), then answer the interruption."""
    fake_ws = FakeWebSocket()
    release = asyncio.Event()  # never set: the gated reply only ends via barge-in
    session, fake_stt, fake_tts, client = _make_gated_session(fake_ws, release)

    run_task = await _start_turn_with_spoken_sentence(fake_ws, session, fake_stt, release)

    # Fresh scripted turn for the interruption's reply.
    client.responses.script(
        [
            make_text_delta_event("I am "),
            make_text_delta_event("Aura."),
            make_completed_event(output=[]),
        ]
    )
    fake_stt.push(
        Transcript(text="Wait. Stop. What is your name?", is_final=True, speech_final=True)
    )
    await _drive(40)

    interrupt_final = {"type": "stt_final", "text": "Wait. Stop. What is your name?"}
    assert any(e["type"] == "tts_cancel" for e in fake_ws.sent), "expected a tts_cancel event"
    assert interrupt_final in fake_ws.sent
    cancel_index = next(i for i, e in enumerate(fake_ws.sent) if e["type"] == "tts_cancel")
    assert cancel_index < fake_ws.sent.index(interrupt_final), (
        "the in-flight turn must be cancelled BEFORE the interruption commits"
    )

    # The interruption got a fresh reply; the gated one never completed.
    assert len(client.responses.calls) == 2
    done_events = [e for e in fake_ws.sent if e["type"] == "assistant_done"]
    assert len(done_events) == 1
    assert done_events[0]["text"] == "I am Aura."

    fake_ws.queue_disconnect()
    await run_task


def test_echo_score_substring_hit_is_full_strength() -> None:
    session = _bare_session()
    session._spoken_recent.append("Here is a fun fact about Rome.")
    assert session._echo_score("fun fact about") == 1.0


def test_echo_score_compact_substring_hit_is_full_strength() -> None:
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE)
    assert session._echo_score("got stronger underwater, which") == 1.0


def test_echo_score_empty_spoken_recent_is_zero() -> None:
    assert _bare_session()._echo_score("anything at all") == 0.0


def test_echo_score_live_worked_examples() -> None:
    """The two live fragments the strict 2-word tier separates: misheard
    echo "The fun" scores 0.5 (blocked from barging), genuine "wait stop"
    scores 0.0 (barges)."""
    session = _bare_session()
    session._spoken_recent.append(_LIVE_SPOKEN_SENTENCE_2)
    assert session._echo_score("The fun") == 0.5
    assert session._echo_score("wait stop") == 0.0
