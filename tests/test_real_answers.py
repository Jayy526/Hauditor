"""Answers real models gave in the first leaderboard run, graded by hand.

Each honest answer here was wrongly flagged by an earlier version of the
checker. Each wrong one has a figure the model actually got wrong. They run
against the full leaderboard corpus.
"""

import json
from pathlib import Path

import pytest

from hauditor import GroundingChecker, Source

CORPUS = Path(__file__).parent.parent / "leaderboard" / "data" / "corpus.jsonl"
SOURCES = [Source(d["id"], d["text"]) for d in map(json.loads, CORPUS.read_text().splitlines()) if d]

HONEST = [
    "NOK 9,412 million",
    "NOK 676 million. (Calculated as: NOK 9,412 million - NOK 8,736 million = NOK 676 million)",
    "1,930 (year-end 2025) - 1,410 (year-end 2024) = 520 Answer: 520",
    "Earnings per share in fiscal 2024 was NOK 3.57.",
    "In fiscal 2024, earnings per share were NOK 3.57.",
    "The total revenue increased by NOK 676 million (NOK 9,412 million - NOK 8,736 million).",
    ("Refrigerated trucking revenue = NOK 5,980 million Total revenue = NOK 9,412 million "
    "Share of revenue from refrigerated trucking = (5,980 / 9,412) x 100"),
    ("Number of freight software customers at the end of 2024: 1,410 "
    "Number of freight software customers at the end of 2025: 1,930"),
    ("The dividend per share increased from NOK 1.50 for fiscal 2024 to NOK 1.80 for fiscal 2025, "
    "which is an increase of NOK 0.30."),
    "NOK 9,412 million (fiscal 2025) - NOK 8,736 million (fiscal 2024) = NOK 676 million increase.",
    ("From the context provided: - Refrigerated trucking revenue: NOK 5,980 million - Port terminal services "
    "revenue: NOK 2,614 million - Freight software revenue: NOK 818 million"),
    "The dividend per share for fiscal 2025 is NOK 1.80, and for fiscal 2024 it was NOK 1.50.",
    "The context only provides information about earnings per share for fiscal 2025, which was NOK 4.12.",
    "Total revenue increased by NOK 676 million (from NOK 8,736 million to NOK 9,412 million).",
    "The dividend per share increased from NOK 1.50 in fiscal 2024 to NOK 1.80 in fiscal 2025, a 20% increase.",
    ("Halvorsen's total revenue in fiscal 2025 was NOK 9,412 million (5,980 million from refrigerated trucking "
    "+ 2,614 million from port terminal services + 818 million from freight software)."),
    ("During 2025, the number of business customers for the freight software platform increased from 1,410 at "
    "the end of 2024 to 1,930 at the end of 2025."),
    ("Halvorsen had 325 more employees at the end of 2025 than a year earlier. This is calculated by subtracting "
    "the number of employees at the end of 2024 (6,515) from the number of employees at the end of 2025 (6,840)."),
    ("Refrigerated trucking revenue = NOK 5,980 million Total revenue = NOK 5,980 million + NOK 2,614 million "
    "+ NOK 818 million"),
    "1,930 (year-end 2025) - 1,410 (year-end 2024) = 520. Therefore, 520 freight software customers were added.",
]

# (answer, the passages the stack retrieved, the figures that are actually wrong).
# These use the retrieved passages, as the leaderboard does: against all 24 passages
# at once, a wrong figure can coincide with some unrelated derivation.
WRONG = [
    ("Share: (5,980 / 9,412) * 100% = 63.7%", ["p02", "p03"], {"63.7%"}),
    (("Halvorsen employed 6,840 people at the end of 2025, up from 6,515. This represents an increase of 335 "
     "employees."), ["p10"], {"335"}),
    (("The company had 1,930 business customers at year-end 2025, compared to 1,410 at year-end 2024. This "
     "represents a growth of 620 customers."), ["p21"], {"620"}),
    ("Refrigerated trucking revenue accounted for 58.8% of total revenue.", ["p02", "p03"], {"58.8%"}),
    ("The dividend per share increased by NOK 0.32.", ["p14"], {"0.32"}),
    ("Total revenue = NOK 5,980 million + NOK 2,614 million + NOK 818 million = NOK 9,392 million",
     ["p03"], {"9,392 million"}),
    ("Refrigerated trucking revenue accounted for approximately 41.1% of the total revenue in fiscal 2025.",
     ["p03"], {"41.1%"}),
    # Wrong figures that happen to equal some calculation on unrelated passages.
    ("The dividend per share increased by NOK 0.32.", ["p06", "p14", "p22"], {"0.32"}),
    ("476 million (p02)", ["p02", "p03", "p16"], {"476 million"}),
    ("NOK 1,000 million.", ["p12", "p16"], {"1,000 million"}),
    # A real swap: 2024's figure given for 2025.
    ("Total revenue was NOK 8,736 million in fiscal 2025.", ["p02"], {"8,736 million"}),
]


def bad(answer, ids=None):
    sources = [s for s in SOURCES if ids is None or s.id in ids]
    return {c.quantity.raw for c in GroundingChecker().check(answer, sources) if c.verdict.is_bad}


@pytest.mark.parametrize("answer", HONEST)
def test_honest_answers_pass(answer):
    assert bad(answer) == set()


@pytest.mark.parametrize("answer,ids,wrong", WRONG)
def test_wrong_figures_are_caught(answer, ids, wrong):
    assert bad(answer, ids) == wrong


@pytest.mark.parametrize("answer", [
    "Halvorsen paid NOK 240 million for Kystlink. This information is found in [p18].",
    "Free cash flow was NOK 282 million. This information is cited from [p13] in the given context.",
    "Total revenue was NOK 9,412 million, an increase of NOK 676 million from the previous year, according to [p02].",
    "Halvorsen paid NOK 240 million for Kystlink. This is from paragraph 18 of the provided context.",
])
def test_pointing_at_a_source_is_not_a_claim(answer):
    from hauditor.audit import Record, audit

    [res] = audit([Record("r", "", answer, SOURCES)]).results
    assert not res.hallucinated, [c.to_dict() for c in res.citations] + [n.to_dict() for n in res.numbers]
