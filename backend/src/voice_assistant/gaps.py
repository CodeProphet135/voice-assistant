"""Detect gaps in a session's persisted seq sequence.

``EventRecorder._write_batch`` (events.py) retries transient failures, but a
batch can still be permanently dropped after repeated failures. ``seq`` is
assigned in-memory before a batch is attempted and is never reused, so a
dropped batch always leaves a real, computable hole in the persisted ``seq``
column for that session -- this scans an ordered list of persisted ``seq``
values for exactly that."""

from dataclasses import dataclass


@dataclass
class SeqGap:
    after_seq: int
    before_seq: int
    missing_count: int


def find_seq_gaps(seqs: list[int]) -> list[SeqGap]:
    """``seqs`` must already be sorted ascending (e.g. a DB query ordered by
    seq). Returns one SeqGap per non-contiguous jump."""
    gaps = []
    for prev, nxt in zip(seqs, seqs[1:], strict=False):
        if nxt - prev > 1:
            gaps.append(SeqGap(after_seq=prev, before_seq=nxt, missing_count=nxt - prev - 1))
    return gaps
