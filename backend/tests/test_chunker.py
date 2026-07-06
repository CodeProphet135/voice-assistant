"""Pure sync tests for agent/chunker.py — no pytest-asyncio needed."""

from voice_assistant.agent.chunker import Chunker


def test_single_sentence_feed_returns_nothing_until_boundary() -> None:
    chunker = Chunker()
    assert chunker.feed("Hello there") == []
    assert chunker.feed(". ") == ["Hello there."]


def test_multi_sentence_single_feed() -> None:
    chunker = Chunker()
    sentences = chunker.feed("Hi there. How are you? Great, thanks! ")
    assert sentences == ["Hi there.", "How are you?", "Great, thanks!"]


def test_incremental_feed_completes_sentence_across_calls() -> None:
    chunker = Chunker()
    out = []
    for chunk in ["Hel", "lo wor", "ld. ", "Bye", "."]:
        out.extend(chunker.feed(chunk))
    # "Bye." has no trailing space yet, so it stays buffered until flush().
    assert out == ["Hello world."]
    assert chunker.flush() == "Bye."


def test_flush_returns_none_when_empty() -> None:
    chunker = Chunker()
    assert chunker.flush() is None


def test_flush_returns_none_after_all_sentences_consumed() -> None:
    chunker = Chunker()
    chunker.feed("Complete sentence. ")
    assert chunker.flush() is None


def test_flush_strips_whitespace() -> None:
    chunker = Chunker()
    chunker.feed("trailing text   ")
    assert chunker.flush() == "trailing text"


def test_flush_returns_none_for_whitespace_only_buffer() -> None:
    chunker = Chunker()
    chunker.feed("   ")
    assert chunker.flush() is None


def test_exclamation_and_question_marks() -> None:
    chunker = Chunker()
    assert chunker.feed("Really?! ") == ["Really?!"]


def test_quoted_sentence_boundary() -> None:
    chunker = Chunker()
    out = chunker.feed('She said "hello." Then left. ')
    assert out == ['She said "hello."', "Then left."]


class TestAbbreviations:
    def test_mr(self) -> None:
        chunker = Chunker()
        out = chunker.feed("I spoke to Mr. Smith about it. ")
        assert out == ["I spoke to Mr. Smith about it."]

    def test_mrs(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Mrs. Jones arrived early. ")
        assert out == ["Mrs. Jones arrived early."]

    def test_ms(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Ms. Lee called. ")
        assert out == ["Ms. Lee called."]

    def test_dr(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Dr. Patel will see you now. ")
        assert out == ["Dr. Patel will see you now."]

    def test_st(self) -> None:
        chunker = Chunker()
        out = chunker.feed("It's on Main St. near the corner. ")
        assert out == ["It's on Main St. near the corner."]

    def test_vs(self) -> None:
        chunker = Chunker()
        out = chunker.feed("It's Alice vs. Bob tonight. ")
        assert out == ["It's Alice vs. Bob tonight."]

    def test_eg(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Bring fruit, e.g. apples, for the trip. ")
        assert out == ["Bring fruit, e.g. apples, for the trip."]

    def test_ie(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Use the default, i.e. leave it blank. ")
        assert out == ["Use the default, i.e. leave it blank."]

    def test_etc(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Bring pens, paper, etc. for the meeting. ")
        assert out == ["Bring pens, paper, etc. for the meeting."]


class TestDecimals:
    def test_decimal_number_not_split(self) -> None:
        chunker = Chunker()
        out = chunker.feed("Pi is about 3.14 in most calculations. ")
        assert out == ["Pi is about 3.14 in most calculations."]

    def test_price_decimal_not_split(self) -> None:
        chunker = Chunker()
        out = chunker.feed("It costs $19.99 at the store. ")
        assert out == ["It costs $19.99 at the store."]

    def test_decimal_split_across_feeds(self) -> None:
        chunker = Chunker()
        out = []
        out.extend(chunker.feed("The price is 3"))
        out.extend(chunker.feed(".14 dollars. "))
        assert out == ["The price is 3.14 dollars."]
