"""Sentence-boundary chunker feeding the TTS pipeline.

Pure sync, CPU-bound string splitting — nothing here needs to be async or
thread-bridged (see CLAUDE.md). The async agent task calls ``feed()`` inline
while consuming the OpenAI stream, then hands each returned sentence to the
TTS queue itself.
"""

import re

# Sentence-final punctuation (one or more of . ! ?), optionally followed by a
# closing quote/paren, then whitespace. The whitespace must already be present
# in the buffer for us to commit to a split — a trailing "." with nothing
# after it yet might still turn out to be part of an abbreviation or a
# decimal number once more text arrives, so it stays buffered until then (or
# until flush()).
_BOUNDARY_RE = re.compile(r"[.!?]+[\"')\]]?\s+")

# Common abbreviations whose trailing "." must not be treated as a sentence
# boundary. Matched case-insensitively against the word immediately preceding
# the boundary.
_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "st.",
    "vs.",
    "e.g.",
    "i.e.",
    "etc.",
}


def _is_false_boundary(buffer: str, punct_start: int, punct_end: int) -> bool:
    """Return True if the punctuation run ending at ``punct_end`` (starting at
    ``punct_start``) is NOT a real sentence boundary — i.e. it's part of a
    known abbreviation or a decimal number like '3.14'.

    Note: a *glued* decimal such as "3.14" never reaches here in the first
    place, because ``_BOUNDARY_RE`` requires whitespace right after the
    punctuation and there's none between the '.' and '14'. The guard below
    exists for the same buffer arriving split across two ``feed()`` calls in
    a way that still leaves a digit immediately before the dot, e.g. a
    trailing "...costs 3." followed later by " out of 5." — we don't want to
    speak "costs 3." as a whole sentence when it's plausibly a decimal
    still being typed. We treat any digit immediately before a lone '.' as
    non-terminal.
    """
    before = buffer[:punct_start]
    punct = buffer[punct_start:punct_end]

    if punct == "." and before[-1:].isdigit():
        return True

    # Abbreviation: the word touching the punctuation, lowercased. Allow
    # internal dots so multi-dot abbreviations like "e.g." and "i.e." are
    # captured as a single token rather than split at their first dot.
    word_match = re.search(r"[A-Za-z]+(?:\.[A-Za-z]+)*$", before)
    if word_match:
        word = (word_match.group(0) + punct).lower()
        if word in _ABBREVIATIONS:
            return True

    return False


class Chunker:
    """Accumulates streamed text and yields complete sentences as they close.

    Usage:
        chunker = Chunker()
        for delta in stream:
            for sentence in chunker.feed(delta):
                await tts_queue.put(sentence)
        trailing = chunker.flush()
        if trailing:
            await tts_queue.put(trailing)
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        """Feed a new text chunk; return zero or more newly-completed sentences."""
        self._buffer += delta
        sentences: list[str] = []
        search_from = 0

        while True:
            match = _BOUNDARY_RE.search(self._buffer, search_from)
            if not match:
                break

            # The punctuation run is everything matched minus the trailing
            # whitespace (and optional closing quote/paren).
            punct_match = re.match(r"[.!?]+", self._buffer[match.start() :])
            assert punct_match is not None
            punct_start = match.start()
            punct_end = match.start() + punct_match.end()

            if _is_false_boundary(self._buffer, punct_start, punct_end):
                search_from = match.end()
                continue

            boundary_end = match.end()
            sentence = self._buffer[:boundary_end].strip()
            if sentence:
                sentences.append(sentence)
            self._buffer = self._buffer[boundary_end:]
            search_from = 0

        return sentences

    def flush(self) -> str | None:
        """Return any buffered remainder (stripped), clearing the buffer.

        Returns None if nothing is buffered (or it's only whitespace).
        """
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None
