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
    r"[;,.](?=\s)|\s[-\u2013+=]\s|\n|\u2022|\(|\)|\b(?:compared with|compared to|versus|vs\.?|from|while|whereas|of which|"
    r"and|but|after|including|before)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z][a-z-]+")
_IGNORE = set(
    """the a an of in on for to and is was were be been by with from at as it its this that these those
    than then also about approximately around roughly nearly fiscal year years year-end end total
    million billion thousand percent dollars which who whom per up down
    nok usd eur gbp sek dkk chf jpy cny inr cad aud""".split()  # noqa: SIM905
)

# A year written right after a figure belongs to it: "X in fiscal 2025",
# "X (year-end 2024)", "X for 2024", "X at the end of 2025".
_YEAR_AFTER_RE = re.compile(
    r"\s*(?:%|percent|million|billion|thousand|per share)?\s*[(,]?\s*"
    r"(?:in|for|during|at|as of|by|of)?\s*(?:the\s+)?(?:end\s+of\s+|year-end\s+|fiscal\s+|fy\s*|calendar\s+)*"
    r"((?:19|20)\d\d)\b(?!\s*[,.]?\d)",
    re.IGNORECASE,
)
# ...and so does one that labels it just before: "2024: 1,410", "end of 2024 = X".
_YEAR_BEFORE_RE = re.compile(r"((?:19|20)\d\d)\)?[ \t]*:[ \t]*(?:[A-Za-z$\u20ac\u00a3]{1,3}[ \t]?)?$")
# "1,410 (customers at year-end 2024)": a short note in brackets right after it.
_YEAR_NOTE_RE = re.compile(r"[ \t]*\((?:[A-Za-z-]+[ \t]+){0,4}((?:19|20)\d\d)\)")

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


_YEAR_SPAN_RE = re.compile(r"\b(?:between|from)\s+((?:19|20)\d\d)\s+(?:and|to|-)\s+((?:19|20)\d\d)\b", re.IGNORECASE)
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n\s*\n")


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for m in _SENT_RE.finditer(text, 0, start):
        left = m.end()
    m = _SENT_RE.search(text, end)
    return left, (m.start() if m else len(text))


def _bound_year(text: str, q: Quantity) -> tuple[str, int] | None:
    """The year grammatically attached to ``q`` and where it is, if there is one."""
    m = _YEAR_NOTE_RE.match(text, q.end)
    if m:
        return m.group(1), m.start(1)
    m = _YEAR_AFTER_RE.match(text, q.end)
    if m and m.end() - q.end <= 40:
        return m.group(1), m.start(1)
    lo = max(0, q.start - 16)
    m = _YEAR_BEFORE_RE.search(text[lo:q.start])
    return (m.group(1), lo + m.start(1)) if m else None


def signature(text: str, q: Quantity, window: int = 6) -> Signature:
    """The year this figure is about, and the words around it.

    The year is, in order of preference: one written right next to the
    figure ("X in fiscal 2025", "2024: X"); otherwise the nearest one in its
    own clause; otherwise the only one in its sentence. Years attached to a
    different figure are never borrowed. When none of these applies the
    signature has no year, and only words are compared.
    Words come from the figure's own clause, so neighbouring figures don't blur.
    """
    s_left, s_right = _sentence(text, q.start, q.end)
    left, right = _clause(text, q.start, q.end)
    left, right = max(left, s_left), min(right, s_right)

    sent = text[s_left:s_right]
    found = extract(sent, ignore_small_ints=0)
    shift = [Quantity(x.raw, x.value, x.kind, x.start + s_left, x.end + s_left, x.tolerance) for x in found]
    years = [y for y in shift if y.kind == Kind.YEAR]
    figures = [x for x in shift if x.kind != Kind.YEAR]

    own = _bound_year(text, q)
    pick = own[0] if own else None
    if pick is None:
        others = [f for f in figures if (f.start, f.end) != (q.start, q.end)]
        bound = {f: _bound_year(text, f) for f in others}
        taken = {b[1] for b in bound.values() if b}
        # "between 2024 and 2025", "from 2024 to 2025": a span, not one figure's year.
        for m in _YEAR_SPAN_RE.finditer(text, s_left, s_right):
            taken |= {m.start(1), m.start(2)}
        free = [y for y in years if y.start not in taken]
        # The whole sentence's year only stands for this figure when no other
        # figure in the sentence is also waiting for one: "(9,412 - 8,736)"
        # next to "in fiscal 2025" says nothing about which is 2025's.
        alone = not any(b is None for b in bound.values())

        def gap(y: Quantity) -> int:
            return y.start - q.end if y.start >= q.end else q.start - y.end

        in_clause = [y for y in free if left <= y.start and y.end <= right]
        if in_clause:
            pick = str(int(min(in_clause, key=gap).value))
        elif alone and len({int(y.value) for y in free}) == 1:
            pick = str(int(free[0].value))
    nearest = frozenset({pick}) if pick else frozenset()

    before = [w for w in _WORD_RE.findall(text[left:q.start].lower()) if w not in _IGNORE][-window:]
    # Labels come before a figure ("Port revenue: NOK 2,614 million") far more
    # often than after it, and words after it often belong to the next figure.
    # Use them only when nothing precedes it.
    after = [] if before else [w for w in _WORD_RE.findall(text[q.end:right].lower()) if w not in _IGNORE][:window]
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
    if not own_sig.words and not own_sig.years:
        # "..., up from X": the source never says what X is on its own, so
        # there is nothing to compare the answer's use of it against.
        return None
    if not own_sig.years:
        # Without a year on the source's own use of the value, years elsewhere
        # in the source can't show it was misused. Compare words only.
        def words_only(sig: Signature) -> float:
            return fit(Signature(frozenset(), ans.words), Signature(frozenset(), sig.words))

        own_fit = max(words_only(sig) for _, _, sig in own)
        others = [(words_only(sig), sq, sig) for _, sq, sig in others]
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
