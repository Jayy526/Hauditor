import pytest

from hauditor.numbers import Kind, extract


def one(text, **kw):
    qs = extract(text, **kw)
    assert len(qs) == 1, qs
    return qs[0]


@pytest.mark.parametrize(
    "text,value,kind",
    [
        ("Revenue was $4.2 billion.", 4.2e9, Kind.CURRENCY),
        ("Revenue was $4.2B.", 4.2e9, Kind.CURRENCY),
        ("Revenue was 4.2 billion dollars.", 4.2e9, Kind.PLAIN),
        ("Margins hit 12.5%.", 12.5, Kind.PERCENT),
        ("Margins hit 12.5 percent.", 12.5, Kind.PERCENT),
        ("They shipped 1,204,300 units.", 1204300, Kind.PLAIN),
        ("Net loss of (342) for the year", -342, Kind.PLAIN),
        ("Change: -3.4%", -3.4, Kind.PERCENT),
        ("Rates rose 50 bps.", 0.5, Kind.PERCENT),
        ("Founded in 1998.", 1998, Kind.YEAR),
        ("Cost was €350k.", 350e3, Kind.CURRENCY),
    ],
)
def test_extract_values(text, value, kind):
    q = one(text)
    assert q.value == pytest.approx(value)
    assert q.kind == kind


def test_small_ints_skipped_by_default():
    assert extract("There are 3 reasons and 2 risks.") == []
    assert len(extract("There are 3 reasons.", ignore_small_ints=0)) == 1


def test_small_int_with_unit_kept():
    assert one("Growth was 3%.").value == 3


def test_hyphenated_ranges_not_negative():
    qs = extract("Fiscal 2023-2024 results")
    assert [q.value for q in qs] == [2023, 2024]


def test_identifiers_not_numbers():
    assert extract("Model GPT4 and Q3 and v2.1beta") == []


def test_tolerance_reflects_precision():
    q = one("$4.2 billion")
    assert q.matches(4.213e9)
    assert q.matches(4.15e9)
    assert not q.matches(4.3e9)
    assert one("4,213").tolerance == 0.5


def test_bare_m_needs_currency():
    # "5 m" might be metres; "$5m" is money.
    assert one("$5m").value == 5e6
    assert one("ran 25 m").value == 25
