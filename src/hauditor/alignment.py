"""Check that a quoted number is attached to the right thing.

A number can appear in the source and still be wrong in the answer: "revenue
was $706.9 million in 2025" when the source says $706.9 million was 2024's
figure and 2025's was $812.4 million. Every number is grounded, so the plain
grounding check can't see it.

This module compares the *slot* a number sits in. For each number it builds a
small signature from its own clause: the years mentioned there, and the words
just before and after it. A grounded answer number is *misaligned* when its
signature clearly matches a different figure's slot in the same source better
than any slot where its own value appears.

It is deliberately conservative. It only fires when the source has another
figure of the same kind whose slot is a clearly better fit, so loosely
paraphrased answers aren't flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import Kind, Quantity, extract

# Where one figure's clause ends and the next one's begins.
_BOUNDARY_RE = re.compile(
    r"[;:,.](?=\s)|\(|\)|\b(?:compared with|compared to|versus|vs\.?|from|while|whereas|of which|"
    r"and|but|after|including|before)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z][a-z-]+")
_IGNORE = set(
    """the a an of in on for to and is was were be been by with from at as it its this that these those
    than then also about approximately around roughly nearly fiscal year years year-end end total
    million billion thousand percent dollars which who whom per up down""".split()  # noqa: SIM905
)

# How much better another slot must fit before we call a number misaligned.
MARGIN = 0.34
# And how well it must fit in absolute terms.
MIN_FIT = 0.5


@dataclass(frozen=True)
class Signature:
    years: frozenset[str]
    words: frozenset[str]


def _stem(w: str) -> str:
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for m in _BOUNDARY_RE.finditer(text, 0, start):
        left = m.end()
    m = _BOUNDARY_RE.search(text, end)
    right = m.start() if m else len(text)
    return left, right


_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for m in _SENT_RE.finditer(text, 0, start):
        left = m.end()
    m = _SENT_RE.search(text, end)
    return left, (m.start() if m else len(text))


def signature(text: str, q: Quantity, window: int = 6) -> Signature:
    """The year closest to the number in its sentence, and the words around it.

    Years are taken from the whole sentence, nearest first, because writers put
    them anywhere ("In 2024, revenue was X" and "revenue was X in 2024").
    Words come from the number's own clause, so neighbouring figures don't blur.
    """
    s_left, s_right = _sentence(text, q.start, q.end)
    left, right = _clause(text, q.start, q.end)
    left, right = max(left, s_left), min(right, s_right)

    def gap(y: Quantity) -> int:
        return y.start - q.end if y.start >= q.end else q.start - y.end

    # Prefer a year in the number's own clause; otherwise the nearest in its sentence.
    years = [y for y in extract(text[s_left:s_right], ignore_small_ints=0) if y.kind == Kind.YEAR]
    years = [Quantity(y.raw, y.value, y.kind, y.start + s_left, y.end + s_left, y.tolerance) for y in years]
    in_clause = [y for y in years if left <= y.start and y.end <= right]
    pick = min(in_clause or years, key=gap) if years else None
    nearest = frozenset({str(int(pick.value))}) if pick else frozenset()

    before = [w for w in _WORD_RE.findall(text[left:q.start].lower()) if w not in _IGNORE][-window:]
    after = [w for w in _WORD_RE.findall(text[q.end:right].lower()) if w not in _IGNORE][:window]
    return Signature(nearest, frozenset(_stem(w) for w in before + after))


def fit(a: Signature, b: Signature) -> float:
    """How well two slots match.

    When both slots name a year, a different year outweighs any word overlap:
    it's the most reliable signal of which figure is meant, and the second
    figure in "X in 2025, compared with Y in 2024" has no subject words of its
    own. Between slots for the same year, or without years, the words around
    the number decide.
    """
    words = len(a.words & b.words) / len(a.words | b.words) if a.words or b.words else 0.0
    if a.years and b.years:
        return (2.0 if a.years & b.years else -2.0) + words
    return words


@dataclass
class Misalignment:
    better_raw: str
    detail: str


def check_alignment(answer: str, q: Quantity, source_text: str) -> Misalignment | None:
    """Return a misalignment if ``q`` sits in another figure's slot in ``source_text``."""
    ans = signature(answer, q)
    if not ans.words and not ans.years:
        return None
    own, others = [], []
    for sq in extract(source_text, ignore_small_ints=0):
        if sq.kind == Kind.YEAR:
            continue
        sig = signature(source_text, sq)
        if q.matches(sq.value) or q.matches(-sq.value):
            own.append((fit(ans, sig), sq, sig))
        elif sq.kind == q.kind:
            others.append((fit(ans, sig), sq, sig))
    if not own or not others:
        return None
    own_fit, own_q, own_sig = max(own, key=lambda t: t[0])
    best_fit, best_q, _ = max(others, key=lambda t: t[0])
    if best_fit >= MIN_FIT and best_fit >= own_fit + MARGIN:
        where = ""
        if ans.years and own_sig.years and not (ans.years & own_sig.years):
            where = f" The source gives {own_q.raw} for {', '.join(sorted(own_sig.years))}, not {', '.join(sorted(ans.years))}."
        return Misalignment(
            best_q.raw,
            f"In the source, {best_q.raw} fills this slot, not {q.raw}.{where}".strip(),
        )
    return None
