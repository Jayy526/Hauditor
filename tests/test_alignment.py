from hauditor import GroundingChecker, Source, Verdict

SRC = [Source("s", "Net income was $61.8 million in fiscal 2025, compared with $48.5 million in fiscal 2024.")]
SEG = [Source("s", "The warehouse segment generated $524.0 million of revenue, and the field services "
                   "segment generated $288.4 million.")]


def verdicts(answer, sources=SRC, **kw):
    return [(c.quantity.raw, c.verdict) for c in GroundingChecker(**kw).check(answer, sources)]


def test_swapped_years_are_misaligned():
    v = dict(verdicts("Net income was $48.5 million in fiscal 2025, compared with $61.8 million in fiscal 2024."))
    assert v["$48.5 million"] == Verdict.MISALIGNED
    assert v["$61.8 million"] == Verdict.MISALIGNED


def test_reordered_honest_answer_is_fine():
    v = verdicts("In fiscal 2024, net income stood at $48.5 million. By fiscal 2025 it was $61.8 million.")
    assert all(verdict == Verdict.GROUNDED for _, verdict in v)


def test_answer_without_year_is_fine():
    assert verdicts("Net income was $61.8 million.") == [("$61.8 million", Verdict.GROUNDED)]


def test_swapped_segments_are_misaligned():
    v = dict(verdicts("The warehouse segment generated $288.4 million of revenue.", SEG))
    assert v["$288.4 million"] == Verdict.MISALIGNED


def test_correct_segment_is_fine():
    assert verdicts("Field services generated $288.4 million.", SEG) == [("$288.4 million", Verdict.GROUNDED)]


def test_value_used_correctly_in_any_source_wins():
    other = Source("t", "Net income was $48.5 million in fiscal 2025.")
    v = dict(verdicts("Net income was $48.5 million in fiscal 2025.", SRC + [other]))
    assert v["$48.5 million"] == Verdict.GROUNDED


def test_alignment_can_be_turned_off():
    v = dict(verdicts("Net income was $48.5 million in fiscal 2025.", align=False))
    assert v["$48.5 million"] == Verdict.GROUNDED


def test_misaligned_counts_as_hallucination_in_audit():
    from hauditor.audit import Record, audit

    [res] = audit([Record("r", "", "Net income was $48.5 million in fiscal 2025 [s].", SRC)]).results
    assert res.hallucinated
    assert res.citations[0].verdict.value == "wrong_numbers"
