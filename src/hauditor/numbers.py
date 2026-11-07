"""Extract numeric quantities from text and normalize them to comparable values.

The goal is not to understand language, only to find every number a reader
would treat as a factual claim ("$4.2 billion", "12.5%", "1,204 units") and
record how precisely it was stated, so we can later tell whether a rounded
figure in an answer is consistent with a more precise one in the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    PLAIN = "plain"
    PERCENT = "percent"
    CURRENCY = "currency"
    YEAR = "year"


SCALE_WORDS = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "mm": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "tn": 1e12,
    "t": 1e12,
}

CURRENCY_SYMBOLS = "$€£¥₹"

# One regex that captures: optional accounting paren, sign, currency symbol,
# the digits, an optional scale word, and an optional percent marker.
# Accounting negatives come as "($342)" or "$(342)"; the closing paren is only
# consumed when an opening one was seen, so "(see 2024)" isn't misread.
_NUMBER_RE = re.compile(
    r"""
    (?P<p1>\()?                                       # accounting paren before the symbol
    (?P<neg>[-−–])?                                   # minus sign
    (?P<cur>[$€£¥₹]|(?:USD|EUR|GBP|INR)\s?)?          # currency
    (?P<p2>\()?                                       # or after it: "$(342)"
    (?<![\w.])                                        # not glued to a word or decimal
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?![\w.]*\d)                                      # don't stop midway through a token
    (?(p1)(?P<c1>\))?|(?(p2)(?P<c2>\))?))              # matching close paren, if opened
    (?:\s?(?P<scale>thousand|million|billion|trillion|mm|mn|bn|tn|k|m|b|t)\b)?
    (?P<pct>\s?(?:%|percent\b|per\s?cent\b|pct\b))?
    (?:\s?(?P<pp>percentage\s+points?|pp|bps|basis\s+points?)\b)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Words that make a number explicitly approximate: "about 3,200".
_HEDGE_RE = re.compile(r"(?:\b(?:about|approximately|approx\.|around|roughly|nearly|almost|some)|~)\s*$", re.IGNORECASE)
# Words that make a number negative: "negative 3.2%".
_SIGN_WORD_RE = re.compile(r"\b(?:negative|minus)\s+$", re.IGNORECASE)
# "page 15", "see note 7", "paragraph 18": references, not figures.
_REF_RE = re.compile(
    r"\b(?:page|pages|pp?\.|paragraph|para\.?|section|sec\.|item|note|chapter|table|figure|fig\.|exhibit|"
    r"slide|line|footnote|appendix)\s*$",
    re.IGNORECASE,
)
# "December 31" or "Dec. 31": a day of the month, not a figure.
_MONTH_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Quantity:
    """A number as stated in text, normalized to a float in base units."""

    raw: str
    value: float
    kind: Kind
    start: int
    end: int
    # Half-width of the interval implied by how the number was written.
    # "4.2 billion" -> 0.05e9: anything in [4.15e9, 4.25e9] is consistent.
    tolerance: float

    def matches(self, other_value: float) -> bool:
        """Is ``other_value`` consistent with this number as written?"""
        # Small epsilon absorbs float noise when the value was computed.
        eps = 1e-9 * max(1.0, abs(self.value))
        return abs(self.value - other_value) <= self.tolerance + eps


def _decimals(num: str) -> int:
    return len(num.split(".", 1)[1]) if "." in num else 0


def extract(text: str, *, ignore_small_ints: int = 10) -> list[Quantity]:
    """Return every quantity mentioned in ``text``.

    Bare integers no larger than ``ignore_small_ints`` with no unit attached
    are skipped: they are usually list markers or counts ("two reasons",
    "step 3") rather than data claims. Pass 0 to keep them all.
    """
    out: list[Quantity] = []
    for m in _NUMBER_RE.finditer(text):
        num = m.group("num")
        value = float(num.replace(",", ""))
        decimals = _decimals(num)
        scale_word = (m.group("scale") or "").lower()
        has_cur = bool(m.group("cur"))
        is_pct = bool(m.group("pct"))
        is_pp = bool(m.group("pp"))

        # A lone "m", "b" or "t" after a plain number is ambiguous ("5 m" could
        # be metres). Only treat it as a scale when there's a currency symbol.
        if scale_word in {"m", "b", "t"} and not has_cur:
            scale_word = ""
        scale = SCALE_WORDS.get(scale_word, 1.0)

        if is_pp and m.group("pp").lower().startswith(("bps", "basis")):
            # 50 bps == 0.5 percentage points
            value, decimals = value / 100, decimals + 2
            is_pct = True
        elif is_pp:
            is_pct = True

        value *= scale
        tolerance = 0.5 * (10 ** -decimals) * scale

        start = m.start("cur") if has_cur else m.start("num")
        if m.group("c2") and not has_cur:
            start = m.start("p2")
        if m.group("c1"):
            start = m.start("p1")
        before_text = text[max(0, start - 24):start]

        if decimals == 0 and scale == 1.0 and value <= 31 and _MONTH_RE.search(before_text):
            continue  # day of the month
        if decimals == 0 and scale == 1.0 and not m.group("cur") and not m.group("pct") and _REF_RE.search(before_text):
            continue  # "page 15", "paragraph 18": a pointer into a document, not a figure
        if value == 100 and re.search(r"[x*\u00d7]\s*$", before_text):
            continue  # "(a / b) x 100": turning a ratio into a percentage

        # "-5" is negative, but not the hyphen in "Q3-2024" or "2023-2024".
        # "(342)" and "$(342)" are negative (accounting style), but a year in
        # parentheses, "(2024)", is just a year.
        looks_like_year = decimals == 0 and scale == 1.0 and "," not in num and 1900 <= value <= 2100
        neg = m.group("neg")
        before = text[m.start("neg") - 1] if neg and m.start("neg") > 0 else " "
        if (m.group("c1") or m.group("c2")) and not looks_like_year or neg and not before.isalnum() or _SIGN_WORD_RE.search(before_text):
            value = -value

        # "about 3,200" claims only hundreds: widen tolerance by trailing zeros.
        if decimals == 0 and _HEDGE_RE.search(before_text):
            digits = num.replace(",", "")
            zeros = len(digits) - len(digits.rstrip("0"))
            if zeros and zeros < len(digits):
                tolerance = max(tolerance, 0.5 * (10**zeros) * scale)

        if is_pct:
            kind = Kind.PERCENT
        elif has_cur:
            kind = Kind.CURRENCY
        elif decimals == 0 and scale == 1.0 and "," not in num and 1900 <= value <= 2100:
            kind = Kind.YEAR
        else:
            kind = Kind.PLAIN

        if (
            kind == Kind.PLAIN
            and ignore_small_ints
            and decimals == 0
            and scale == 1.0
            and abs(value) <= ignore_small_ints
        ):
            continue

        out.append(
            Quantity(
                raw=text[start:m.end()].strip(),
                value=value,
                kind=kind,
                start=start,
                end=m.end(),
                tolerance=tolerance,
            )
        )
    return out
