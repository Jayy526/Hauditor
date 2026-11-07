"""Decide whether each number in an answer is supported by the sources.

A number is *grounded* when the same quantity appears in the retrieved
context (or the question), allowing for rounding. It is *derived* when it can
be reproduced from two source numbers with one arithmetic step: sum,
difference, ratio, or percent change. Everything else is *unsupported*: the
model produced a number nobody gave it.

All arithmetic here is plain Python. The model is never asked to check math.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations, permutations

from .alignment import check_alignment
from .numbers import Kind, Quantity, extract


class Verdict(str, Enum):
    GROUNDED = "grounded"
    DERIVED = "derived"
    UNSUPPORTED = "unsupported"
    # The number is in a source, but there it belongs to a different metric
    # or period ("2024's revenue given as 2025's").
    MISALIGNED = "misaligned"

    @property
    def is_bad(self) -> bool:
        return self in (Verdict.UNSUPPORTED, Verdict.MISALIGNED)


@dataclass
class NumberCheck:
    quantity: Quantity
    verdict: Verdict
    # Human-readable evidence: the matching source text, or the expression.
    evidence: str = ""
    source_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "raw": self.quantity.raw,
            "value": self.quantity.value,
            "kind": self.quantity.kind.value,
            "span": [self.quantity.start, self.quantity.end],
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "source_id": self.source_id,
        }


@dataclass
class Source:
    id: str
    text: str
    quantities: list[Quantity] = field(default_factory=list)


def _fmt(x: float) -> str:
    if abs(x) >= 1e9:
        return f"{x / 1e9:,.4g}B"
    if abs(x) >= 1e6:
        return f"{x / 1e6:,.4g}M"
    return f"{x:,.6g}"


_RELATIVE_YEAR_RE = re.compile(
    r"\b(?:a year (?:earlier|before|ago)|(?:the )?(?:prior|previous|preceding) year|last year)\b", re.IGNORECASE
)
_MANTISSA_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")

# How an operand may be used in a derivation: "amount" (plain or currency),
# "percent" (as written, 12 for 12%) or "fraction" (0.12 for 12%).
AMOUNT, PERCENT, FRACTION = "amount", "percent", "fraction"


def _src_values(src_q: Quantity, unit_hints: bool) -> list[tuple[float, str, str]]:
    """Ways to read a source quantity: (value, note on the reading, operand role)."""
    if src_q.kind == Kind.PERCENT:
        return [(src_q.value, "", PERCENT), (src_q.value / 100, "", FRACTION)]
    out = [(src_q.value, "", AMOUNT)]
    if src_q.kind in (Kind.PLAIN, Kind.CURRENCY):
        # "NOK 5,980 million" is often restated as plain "5,980" in a calculation.
        m = _MANTISSA_RE.search(src_q.raw)
        mant = float(m.group().replace(",", "")) if m else 0.0
        if mant and abs(src_q.value) > abs(mant) * 999:
            out.append((math.copysign(mant, src_q.value), " (without its unit)", AMOUNT))
    if unit_hints and src_q.kind in (Kind.PLAIN, Kind.CURRENCY) and src_q.tolerance <= 0.5:
        # Unscaled figure, possibly from a table "in millions/thousands".
        for scale, word in ((1e3, "thousands"), (1e6, "millions"), (1e9, "billions")):
            out.append((src_q.value * scale, f" (read in {word})", AMOUNT))
    return out


class GroundingChecker:
    """Check every number in an answer against a set of sources.

    Parameters
    ----------
    allow_derived:
        Accept numbers reproducible by one arithmetic step from two source
        numbers. Turn off for a stricter "quote only" policy.
    unit_hints:
        Let unscaled source numbers be read in thousands/millions/billions,
        which is how financial tables are usually printed. Only enabled when a
        source mentions such a unit, to avoid accidental matches.
    max_pairs_sources:
        Upper bound on source numbers considered for derivations, to keep the
        pairwise search cheap on long contexts.
    """

    def __init__(
        self,
        *,
        allow_derived: bool = True,
        unit_hints: bool = True,
        ignore_small_ints: int = 10,
        max_pairs_sources: int = 80,
        align: bool = True,
    ) -> None:
        self.allow_derived = allow_derived
        self.align = align
        self.unit_hints = unit_hints
        self.ignore_small_ints = ignore_small_ints
        self.max_pairs_sources = max_pairs_sources

    def check(self, answer: str, sources: list[Source]) -> list[NumberCheck]:
        pool: list[tuple[float, str, Source, str]] = []
        for src in sources:
            # Sources keep small ints: "3 segments" in a source can ground "3".
            src.quantities = extract(src.text, ignore_small_ints=0)
            hint = self.unit_hints and _mentions_table_units(src.text)
            for sq in src.quantities:
                if sq.kind == Kind.YEAR:
                    pool.append((sq.value, sq.raw, src, "year"))
                    continue
                for val, note, role in _src_values(sq, hint):
                    pool.append((val, f"{sq.raw}{note}", src, role))

        results = []
        stated = extract(answer, ignore_small_ints=0)
        for q in extract(answer, ignore_small_ints=self.ignore_small_ints):
            res = self._check_one(q, pool, stated)
            if self.align and res.verdict == Verdict.GROUNDED and q.kind != Kind.YEAR:
                res = self._align(answer, res, sources)
            results.append(res)
        return results

    def _align(self, answer: str, res: NumberCheck, sources: list[Source]) -> NumberCheck:
        """Downgrade a grounded number that sits in another figure's slot.

        Only fires when every source containing the value disagrees; if any
        source uses it the way the answer does, it stays grounded.
        """
        problems = []
        for src in sources:
            if not any(res.quantity.matches(q.value) or res.quantity.matches(-q.value) for q in src.quantities):
                continue
            mis = check_alignment(answer, res.quantity, src.text)
            if mis is None:
                return res
            problems.append((src, mis))
        if not problems:
            return res
        src, mis = problems[0]
        return NumberCheck(res.quantity, Verdict.MISALIGNED, mis.detail, src.id)

    def _check_one(
        self, q: Quantity, pool: list[tuple[float, str, Source, str]], stated: list[Quantity] | None = None,
    ) -> NumberCheck:
        # 1) Direct match, allowing for the answer's rounding.
        for val, raw, src, _ in pool:
            for cand_q in (q, _as_fraction(q)):
                if cand_q is not None and cand_q.matches(val):
                    return NumberCheck(q, Verdict.GROUNDED, f'"{raw}"', src.id)
        # Sign-insensitive match: "fell 5%" vs "-5%" in a table.
        for val, raw, src, role in pool:
            if role != "year" and q.matches(-val):
                return NumberCheck(q, Verdict.GROUNDED, f'"{raw}" (sign implied by wording)', src.id)

        if q.kind == Kind.YEAR:
            # "6,515 a year earlier" next to "end of 2025" is 2024's figure.
            if self.allow_derived:
                for val, raw, src, role in pool:
                    if role == "year" and q.value == val - 1 and _RELATIVE_YEAR_RE.search(src.text):
                        return NumberCheck(q, Verdict.DERIVED, f"the year before {raw} (\"{_RELATIVE_YEAR_RE.search(src.text).group()}\")", src.id)
            return NumberCheck(q, Verdict.UNSUPPORTED)
        if not self.allow_derived:
            return NumberCheck(q, Verdict.UNSUPPORTED)

        # 2) One arithmetic step over two distinct source numbers.
        seen: set[tuple[float, str]] = set()
        base = []
        for val, raw, src, role in pool:
            if role == "year" or val == 0 or (val, role, src.id) in seen:
                continue
            in_answer = any(x is not q and x.matches(val) for x in stated or [])
            if "(without its unit)" in raw and not in_answer:
                continue  # a bare restatement only counts where the answer shows it
            seen.add((val, role, src.id))
            base.append((val, raw, src, role, in_answer))
            if len(base) >= self.max_pairs_sources:
                break

        # Operands must be related: from the same passage, or shown in the answer
        # itself ("9,412 - 8,736 = 676"). Otherwise, with a dozen passages, some
        # pair of unrelated figures will produce almost any number by chance.
        for (a, ra, sa, ka, ia), (b, rb, sb, kb, ib) in permutations(base, 2):
            if sa.id != sb.id and not (ia or ib):
                continue
            for value, expr in _derivations(a, ra, ka, b, rb, kb, q.kind):
                if q.matches(value) or q.matches(-value):
                    sid = sa.id if sa.id == sb.id else f"{sa.id}+{sb.id}"
                    return NumberCheck(q, Verdict.DERIVED, f"{expr} = {_fmt(value)}", sid)

        # 3) A total of three figures from the same passage ("A + B + C").
        if q.kind != Kind.PERCENT:
            by_src: dict[str, list[tuple[float, str]]] = {}
            for val, raw, src, role, _ in base:
                if role == AMOUNT:
                    by_src.setdefault(src.id, []).append((val, raw))
            for sid, vals in by_src.items():
                for (a, ra), (b, rb), (c, rc) in combinations(vals[:30], 3):
                    if q.matches(a + b + c):
                        return NumberCheck(q, Verdict.DERIVED, f"{ra} + {rb} + {rc} = {_fmt(a + b + c)}", sid)

        return NumberCheck(q, Verdict.UNSUPPORTED)


def _as_fraction(q: Quantity) -> Quantity | None:
    """Percent answer compared against a fraction in the source (12% vs 0.12)."""
    if q.kind != Kind.PERCENT:
        return None
    return Quantity(q.raw, q.value / 100, Kind.PLAIN, q.start, q.end, q.tolerance / 100)


def _derivations(a: float, ra: str, ka: str, b: float, rb: str, kb: str, kind: Kind):
    """Results of one sensible arithmetic step. Operands must make sense together.

    Amounts add, subtract and divide with amounts; a percentage answer can be
    a change or ratio of two amounts, or a difference of two percentages; an
    amount can be a percentage *of* an amount. Arbitrary mixes ("31% / 5%",
    "74% / 1.80") are not derivations, just coincidences.
    """
    if kind == Kind.PERCENT:
        if ka == kb == AMOUNT:
            yield (a - b) / b * 100, f"({ra} - {rb}) / {rb} x 100"
            yield a / b * 100, f"{ra} / {rb} x 100"
        if ka == kb == PERCENT:
            yield a - b, f"{ra} - {rb}"
            yield a + b, f"{ra} + {rb}"
        return
    if ka == kb == AMOUNT:
        yield a + b, f"{ra} + {rb}"
        yield a - b, f"{ra} - {rb}"
        yield a / b, f"{ra} / {rb}"
        yield a * b, f"{ra} x {rb}"
    elif ka == AMOUNT and kb == FRACTION:
        yield a * b, f"{rb} of {ra}"


_UNIT_WORDS = ("in thousands", "in millions", "in billions", "($000", "(000s", "000s)", "$m)", "$mm", "($ in")


def _mentions_table_units(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _UNIT_WORDS)
