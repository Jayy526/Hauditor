"""Decide whether each number in an answer is supported by the sources.

A number is *grounded* when the same quantity appears in the retrieved
context (or the question), allowing for rounding. It is *derived* when it can
be reproduced from two source numbers with one arithmetic step: sum,
difference, ratio, or percent change. Everything else is *unsupported*: the
model produced a number nobody gave it.

All arithmetic here is plain Python. The model is never asked to check math.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import permutations

from .numbers import Kind, Quantity, extract


class Verdict(str, Enum):
    GROUNDED = "grounded"
    DERIVED = "derived"
    UNSUPPORTED = "unsupported"


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


def _src_values(src_q: Quantity, unit_hints: bool) -> list[tuple[float, str]]:
    """Ways to read a source quantity, with a note on the reading used."""
    out = [(src_q.value, "")]
    if src_q.kind == Kind.PERCENT:
        out.append((src_q.value / 100, ""))
    if unit_hints and src_q.kind in (Kind.PLAIN, Kind.CURRENCY) and src_q.tolerance <= 0.5:
        # Unscaled figure, possibly from a table "in millions/thousands".
        for scale, word in ((1e3, "thousands"), (1e6, "millions"), (1e9, "billions")):
            out.append((src_q.value * scale, f" (read in {word})"))
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
    ) -> None:
        self.allow_derived = allow_derived
        self.unit_hints = unit_hints
        self.ignore_small_ints = ignore_small_ints
        self.max_pairs_sources = max_pairs_sources

    def check(self, answer: str, sources: list[Source]) -> list[NumberCheck]:
        pool: list[tuple[float, str, Source]] = []
        for src in sources:
            # Sources keep small ints: "3 segments" in a source can ground "3".
            src.quantities = extract(src.text, ignore_small_ints=0)
            hint = self.unit_hints and _mentions_table_units(src.text)
            for sq in src.quantities:
                for val, note in _src_values(sq, hint):
                    pool.append((val, f"{sq.raw}{note}", src))

        results = []
        for q in extract(answer, ignore_small_ints=self.ignore_small_ints):
            results.append(self._check_one(q, pool))
        return results

    def _check_one(self, q: Quantity, pool: list[tuple[float, str, Source]]) -> NumberCheck:
        # 1) Direct match, allowing for the answer's rounding.
        for val, raw, src in pool:
            for cand_q in (q, _as_fraction(q)):
                if cand_q is not None and cand_q.matches(val):
                    return NumberCheck(q, Verdict.GROUNDED, f'"{raw}"', src.id)
        # Sign-insensitive match: "fell 5%" vs "-5%" in a table.
        for val, raw, src in pool:
            if q.matches(-val):
                return NumberCheck(q, Verdict.GROUNDED, f'"{raw}" (sign implied by wording)', src.id)

        if q.kind == Kind.YEAR or not self.allow_derived:
            return NumberCheck(q, Verdict.UNSUPPORTED)

        # 2) One arithmetic step over two distinct source numbers.
        seen: set[float] = set()
        base = []
        for val, raw, src in pool:
            if val in seen or val == 0:
                continue
            seen.add(val)
            base.append((val, raw, src))
            if len(base) >= self.max_pairs_sources:
                break

        for (a, ra, sa), (b, rb, sb) in permutations(base, 2):
            for value, expr in _derivations(a, ra, b, rb, q.kind):
                if q.matches(value) or q.matches(-value):
                    sid = sa.id if sa.id == sb.id else f"{sa.id}+{sb.id}"
                    return NumberCheck(q, Verdict.DERIVED, f"{expr} = {_fmt(value)}", sid)

        return NumberCheck(q, Verdict.UNSUPPORTED)


def _as_fraction(q: Quantity) -> Quantity | None:
    """Percent answer compared against a fraction in the source (12% vs 0.12)."""
    if q.kind != Kind.PERCENT:
        return None
    return Quantity(q.raw, q.value / 100, Kind.PLAIN, q.start, q.end, q.tolerance / 100)


def _derivations(a: float, ra: str, b: float, rb: str, kind: Kind):
    yield a + b, f"{ra} + {rb}"
    yield a - b, f"{ra} - {rb}"
    if kind == Kind.PERCENT:
        yield (a - b) / b * 100, f"({ra} - {rb}) / {rb} x 100"
        yield a / b * 100, f"{ra} / {rb} x 100"
    else:
        yield a / b, f"{ra} / {rb}"
        yield a * b, f"{ra} x {rb}"


_UNIT_WORDS = ("in thousands", "in millions", "in billions", "($000", "(000s", "000s)", "$m)", "$mm", "($ in")


def _mentions_table_units(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _UNIT_WORDS)
