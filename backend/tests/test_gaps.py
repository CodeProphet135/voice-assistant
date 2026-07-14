from voice_assistant.gaps import SeqGap, find_seq_gaps


def test_no_gaps_for_empty_or_single_element():
    assert find_seq_gaps([]) == []
    assert find_seq_gaps([5]) == []


def test_no_gaps_for_contiguous_seq():
    assert find_seq_gaps([0, 1, 2, 3]) == []


def test_single_gap_detected():
    assert find_seq_gaps([0, 1, 4, 5]) == [SeqGap(after_seq=1, before_seq=4, missing_count=2)]


def test_multiple_gaps_detected():
    assert find_seq_gaps([0, 2, 3, 7]) == [
        SeqGap(after_seq=0, before_seq=2, missing_count=1),
        SeqGap(after_seq=3, before_seq=7, missing_count=3),
    ]
