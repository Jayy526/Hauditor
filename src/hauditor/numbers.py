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

# One regex that captures: optional sign/paren, optional currency symbol,
# the digits, an optional scale word, and an optional percent marker.
_NUMBER_RE = re.compile(
    r"""
    (?P<neg>[-−–(])?                                  # minus sign or accounting paren
    (?P<cur>[$€£¥₹]|(?:USD|EUR|GBP|INR)\s?)?          # currency
    (?<![\w.])                                        # not glued to a word or decimal
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?![\w.]*\d)                                      # don't stop midway through a token
    (?:\s?(?P<scale>thousand|million|billion|trillion|mm|mn|bn|tn|k|m|b|t)\b)?
    (?P<pct>\s?(?:%|percent\b|per\s?cent\b|pct\b))?
    (?:\s?(?P<pp>percentage\s+points?|pp|bps|basis\s+points?)\b)?
    """,
    re.IGNORECASE | re.VERBOSE,
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

        # "-5" is negative, but not the hyphen in "Q3-2024" or "2023-2024".
        # "(5)" is negative (accounting style), but "(5 items)" is an aside.
        neg = m.group("neg")
        before = text[m.start() - 1] if m.start() > 0 else " "
        if neg == "(":
            if text[m.end():m.end() + 1] == ")":
                value = -value
        elif neg and not before.isalnum():
            value = -value

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

        start = m.start("cur") if has_cur else m.start("num")
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
