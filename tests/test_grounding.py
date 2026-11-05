from hauditor import GroundingChecker, Source, Verdict

CTX = [
    Source(
        "10k-p41",
        "Total revenue was $4,213 million in fiscal 2024, compared with $3,780 million in fiscal 2023. "
        "Gross margin was 61.2%.",
    )
]


def verdicts(answer, sources=CTX, **kw):
    return [(c.quantity.raw, c.verdict) for c in GroundingChecker(**kw).check(answer, sources)]


def test_rounded_quote_is_grounded():
    assert verdicts("Revenue was about $4.2 billion.") == [("$4.2 billion", Verdict.GROUNDED)]


def test_invented_number_is_unsupported():
    assert verdicts("Revenue was $4.9 billion.") == [("$4.9 billion", Verdict.UNSUPPORTED)]


def test_percent_change_is_derived():
    # (4213 - 3780) / 3780 = 11.46%
    [(_, v)] = verdicts("Revenue grew 11.5% year over year.")
    assert v == Verdict.DERIVED


def test_wrong_percent_change_is_unsupported():
    [(_, v)] = verdicts("Revenue grew 14% year over year.")
    assert v == Verdict.UNSUPPORTED


def test_difference_is_derived():
    [(_, v)] = verdicts("Revenue rose by $433 million.")
    assert v == Verdict.DERIVED


def test_strict_mode_rejects_arithmetic():
    [(_, v)] = verdicts("Revenue rose by $433 million.", allow_derived=False)
    assert v == Verdict.UNSUPPORTED


def test_year_must_be_quoted():
    assert verdicts("In fiscal 2024 margin was 61.2%.") == [
        ("2024", Verdict.GROUNDED),
        ("61.2%", Verdict.GROUNDED),
    ]
    assert verdicts("In fiscal 2022 margin was 61.2%.")[0][1] == Verdict.UNSUPPORTED


def test_table_in_millions():
    table = [Source("t", "(in millions)\nRevenue  4,213\nCOGS  1,635")]
    [(_, v)] = verdicts("Revenue was $4.21B.", table)
    assert v == Verdict.GROUNDED


def test_unit_hint_requires_header():
    plain = [Source("t", "Revenue  4,213")]
    [(_, v)] = verdicts("Revenue was $4.21B.", plain)
    assert v == Verdict.UNSUPPORTED


def test_percent_vs_fraction():
    src = [Source("s", "Churn rate: 0.034")]
    [(_, v)] = verdicts("Churn was 3.4%.", src)
    assert v == Verdict.GROUNDED


def test_sign_implied_by_wording():
    src = [Source("s", "Operating income change: -5.2%")]
    [(_, v)] = verdicts("Operating income fell 5.2%.", src)
    assert v == Verdict.GROUNDED
