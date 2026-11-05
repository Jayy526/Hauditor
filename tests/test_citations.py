from hauditor import CitationChecker, CitationVerdict, Claim, LexicalJudge, Source, extract_claims
from hauditor.audit import Record, audit
from hauditor.citations import JudgeResult

SOURCES = [
    Source("10k-p41", "Total revenue was $4,213 million in fiscal 2024, up from $3,780 million in fiscal 2023, "
                      "driven by growth in subscription services."),
    Source("10k-p44", "Gross margin was 61.2% in fiscal 2024. The company employs about 12,400 people."),
]


def check(answer, sources=SOURCES, judge=None):
    claims = extract_claims(answer, [s.id for s in sources])
    return CitationChecker(judge=judge).check(claims, sources)


# --- extraction --------------------------------------------------------------

def test_marker_inside_sentence():
    [c] = extract_claims("Revenue was $4.2 billion [10k-p41]. Nice.")
    assert c.text == "Revenue was $4.2 billion."
    assert c.source_ids == ["10k-p41"]


def test_marker_after_full_stop_attaches_to_previous_sentence():
    claims = extract_claims("Revenue rose. [1] Margin held. [2]")
    assert [(c.text, c.source_ids) for c in claims] == [("Revenue rose.", ["1"]), ("Margin held.", ["2"])]


def test_multiple_ids_and_footnotes():
    [c] = extract_claims("Both grew [1, 2][^3].")
    assert c.source_ids == ["1", "2", "3"]


def test_markdown_links_and_editorial_brackets_ignored():
    assert extract_claims("See [the filing](https://sec.gov). He said [sic] it rose.") == []


def test_named_source_counts_when_known():
    [c] = extract_claims("It rose [annual-report].", ["annual-report"])
    assert c.source_ids == ["annual-report"]


# --- verdicts ----------------------------------------------------------------

def test_supported_citation():
    [r] = check("Revenue grew to $4.2 billion in fiscal 2024, driven by subscription services [10k-p41].")
    assert r.verdict == CitationVerdict.SUPPORTED


def test_numeric_marker_resolves_by_position():
    [r] = check("Gross margin was 61.2% in fiscal 2024 [2].")
    assert r.verdict == CitationVerdict.SUPPORTED


def test_fabricated_source():
    [r] = check("Revenue grew to $4.2 billion [10k-p99].")
    assert r.verdict == CitationVerdict.FABRICATED
    assert r.missing_ids == ["10k-p99"]


def test_partly_fabricated():
    [r] = check("Revenue grew to $4.2 billion in fiscal 2024 [10k-p41, 7].")
    assert r.verdict == CitationVerdict.FABRICATED
    assert r.missing_ids == ["7"]


def test_misattributed_number():
    [r] = check("Gross margin was 61.2% [10k-p41].")
    assert r.verdict == CitationVerdict.WRONG_NUMBERS
    assert "10k-p44" in r.detail


def test_invented_number_with_real_citation():
    [r] = check("Revenue grew to $4.9 billion [10k-p41].")
    assert r.verdict == CitationVerdict.WRONG_NUMBERS
    assert "no retrieved source" in r.detail


def test_text_not_supported():
    [r] = check("The company announced a major acquisition in Europe [10k-p44].")
    assert r.verdict == CitationVerdict.NOT_SUPPORTED


def test_custom_judge_is_used():
    class Never:
        name = "never"

        def judge(self, claim, passage):
            return JudgeResult(False, 0.0, "nope")

    [r] = check("Revenue grew in fiscal 2024 [10k-p41].", judge=Never())
    assert r.verdict == CitationVerdict.NOT_SUPPORTED
    assert r.detail == "never: nope"


def test_lexical_judge_handles_inflection():
    res = LexicalJudge().judge("Subscriptions drove growth.", "driven by growth in subscription services")
    assert res.supported


# --- integration with audit --------------------------------------------------

def test_markers_are_not_counted_as_numbers():
    rec = Record("r", "", "Margin was 61.2% [12].", [Source(str(i), "x") for i in range(1, 12)]
                 + [Source("12", "Gross margin was 61.2%.")])
    [res] = audit([rec]).results
    assert [n.quantity.raw for n in res.numbers] == ["61.2%"]
    assert res.citations[0].verdict == CitationVerdict.SUPPORTED


def test_structured_citations_override_markers():
    rec = Record.from_dict({
        "answer": "Gross margin was 61.2%.",
        "contexts": [{"id": "a", "text": "Gross margin was 61.2%."}],
        "citations": [{"claim": "Gross margin was 61.2%.", "source_ids": ["b"]}],
    })
    [res] = audit([rec]).results
    assert res.citations[0].verdict == CitationVerdict.FABRICATED
    assert res.hallucinated


def test_answer_without_citations_has_no_citation_checks():
    [res] = audit([Record("r", "", "Margin was 61.2%.", [Source("a", "Margin was 61.2%.")])]).results
    assert res.citations == []
    assert not res.hallucinated


def test_claim_dataclass_roundtrip():
    c = Claim("x", ["1"])
    assert c.source_ids == ["1"]
