"""Build a labelled benchmark by planting hallucinations in honest answers.

Every case starts from a passage and an honest answer quoting it with a
citation. Then one controlled edit is applied. Because we make the edit, we
know the true label, with no human or LLM grading involved:

Planted hallucinations (Hauditor should flag these)
    number_shift          one figure moved by 4-30%
    digit_swap            two adjacent digits transposed (812.4 -> 821.4)
    scale_swap            million <-> billion
    wrong_year            a year changed to one no source mentions
    fabricated_citation   cites a source id that wasn't retrieved
    misattributed         cites the other retrieved passage, which lacks the figures
    invented_claim        adds a forecast figure nobody stated
    wrong_math            adds a difference of two stated figures, computed wrong
    swapped_figures       two stated figures trade places (2025's value given for 2024)

Honest variants (Hauditor should pass these)
    quote                 the sentence as written
    rounded               a figure rounded to fewer decimals (812.4 -> 812)
    rounded_coarse        an integer rounded to two significant figures (3,150 -> 3,200)
    derived               adds a correct difference of two stated figures
    unit_words            "$812.4 million" -> "812.4 million dollars", "%" -> " percent"
    reordered             the two periods restated in the other order, in new words
    no_year               a figure restated without its year ("Net income was $61.8 million.")

``swapped_figures`` is the hard one: every number in the answer appears in the
source and the wording matches, so neither the number check nor a lexical
judge can see it. It is here to measure that blind spot honestly.

``rounded_coarse`` was the first weakness the benchmark exposed. Hauditor reads tolerance from the
digits written, so "3,200" claims precision to the unit and will be flagged.
Hedge words ("about", "approximately") now widen the tolerance to match.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from ..audit import Record
from ..grounding import Source
from ..numbers import Kind, Quantity, extract

HALLUCINATED = [
    "number_shift", "digit_swap", "scale_swap", "wrong_year",
    "fabricated_citation", "misattributed", "invented_claim", "wrong_math", "swapped_figures",
]
HONEST = ["quote", "rounded", "rounded_coarse", "derived", "unit_words", "reordered", "no_year"]

_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")


@dataclass
class Case:
    record: Record
    hallucinated: bool
    kind: str
    note: str = ""

    def to_dict(self) -> dict:
        r = self.record
        return {
            "id": r.id,
            "question": r.question,
            "contexts": [{"id": s.id, "text": s.text} for s in r.contexts],
            "answer": r.answer,
            "label": {"hallucinated": self.hallucinated, "kind": self.kind, "note": self.note},
        }

    @classmethod
    def from_dict(cls, d: dict) -> Case:
        lab = d["label"]
        return cls(Record.from_dict(d), lab["hallucinated"], lab["kind"], lab.get("note", ""))


def load_corpus(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --------------------------------------------------------------------------
# Number surgery
# --------------------------------------------------------------------------

def _fmt_like(original: str, value: float) -> str:
    """Format ``value`` with the same decimals and comma style as ``original``."""
    decimals = len(original.split(".", 1)[1]) if "." in original else 0
    s = f"{abs(value):,.{decimals}f}" if "," in original else f"{abs(value):.{decimals}f}"
    if original.startswith(".") and s.startswith("0."):
        s = s[1:]
    return s


def _digits_span(text: str, q: Quantity) -> tuple[int, int]:
    m = _DIGITS.search(text, q.start, q.end)
    assert m, (text, q)
    return m.start(), m.end()


def _replace(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _figures(text: str) -> list[Quantity]:
    """Quantities worth editing: not years, not tiny counts."""
    return [q for q in extract(text) if q.kind != Kind.YEAR]


# --------------------------------------------------------------------------
# Edits. Each returns (new_answer_body, note) or None if it doesn't apply.
# --------------------------------------------------------------------------

def _number_shift(body, rng, ctx_text):
    qs = _figures(body)
    if not qs:
        return None
    q = rng.choice(qs)
    s, e = _digits_span(body, q)
    raw = body[s:e]
    base = float(raw.replace(",", ""))
    for _ in range(20):
        d = rng.uniform(0.04, 0.30) * rng.choice([-1, 1])
        new = _fmt_like(raw, base * (1 + d))
        if new != raw and new not in ctx_text:
            return _replace(body, s, e, new), f"{raw} -> {new}"
    return None


def _digit_swap(body, rng, ctx_text):
    qs = _figures(body)
    rng.shuffle(qs)
    for q in qs:
        s, e = _digits_span(body, q)
        raw = body[s:e]
        idx = [i for i in range(len(raw) - 1) if raw[i].isdigit() and raw[i + 1].isdigit() and raw[i] != raw[i + 1]]
        if idx:
            i = rng.choice(idx)
            new = raw[:i] + raw[i + 1] + raw[i] + raw[i + 2:]
            if new not in ctx_text:
                return _replace(body, s, e, new), f"{raw} -> {new}"
    return None


def _scale_swap(body, rng, ctx_text):
    m = re.search(r"\b(million|billion)\b", body)
    if not m:
        return None
    new = "billion" if m.group(1) == "million" else "million"
    return _replace(body, m.start(), m.end(), new), f"{m.group(1)} -> {new}"


def _wrong_year(body, rng, ctx_text):
    years = [q for q in extract(body) if q.kind == Kind.YEAR]
    if not years:
        return None
    q = rng.choice(years)
    y = int(q.value)
    for cand in rng.sample([y - 2, y - 1, y + 1, y + 2], 4):
        if str(cand) not in ctx_text:
            return _replace(body, q.start, q.end, str(cand)), f"{y} -> {cand}"
    return None


def _invented_claim(body, rng, ctx_text):
    for _ in range(20):
        n = f"{rng.uniform(5, 25):.1f}"
        if n not in ctx_text:
            return body + f" Management expects growth of {n}% next year.", f"added forecast {n}%"
    return None


def _rounded(body, rng, ctx_text):
    for q in _figures(body):
        s, e = _digits_span(body, q)
        raw = body[s:e]
        if "." in raw:
            dec = len(raw.split(".")[1])
            new = _fmt_like(raw.split(".")[0] + ("." + "0" * (dec - 1) if dec > 1 else ""),
                            round(float(raw.replace(",", "")), dec - 1))
            return _replace(body, s, e, new), f"{raw} -> {new}"
    return None


def _rounded_coarse(body, rng, ctx_text):
    for q in _figures(body):
        s, e = _digits_span(body, q)
        raw = body[s:e]
        v = float(raw.replace(",", ""))
        if "." not in raw and v >= 100:
            digits = len(str(int(v)))
            new_v = round(v, -(digits - 2))
            if new_v != v:
                new = _fmt_like(raw, new_v)
                # "about" goes before any currency symbol: "about $410 million".
                at = q.start if body[q.start] in "$€£¥₹" else s
                return _replace(body, at, e, "about " + body[at:s] + new), f"{raw} -> about {new}"
    return None


def _derived(body, rng, ctx_text):
    for a, b in _like_pairs(_figures(body)):
        text = _diff_text(a, b, abs(a.value - b.value))
        return body + f" That is a difference of {text}.", f"|{a.raw} - {b.raw}| = {text}"
    return None


def _diff_text(a: Quantity, b: Quantity, diff: float) -> str:
    if a.kind == Kind.PERCENT:
        return f"{diff:.1f} percentage points"
    cur = "$" if a.kind == Kind.CURRENCY else ""
    if diff >= 1e9:
        return f"{cur}{diff / 1e9:.2f} billion"
    if diff >= 1e6:
        return f"{cur}{diff / 1e6:.1f} million"
    return f"{cur}{diff:,.2f}" if a.kind == Kind.CURRENCY else f"{diff:,.0f}"


def _like_pairs(qs: list[Quantity]):
    for i in range(len(qs)):
        for j in range(i + 1, len(qs)):
            a, b = qs[i], qs[j]
            # Only compare like with like: same kind, same order of magnitude.
            if a.kind == b.kind and a.value != b.value and b.value and 0.1 <= abs(a.value / b.value) <= 10:
                yield a, b


def _wrong_math(body, rng, ctx_text):
    for a, b in _like_pairs(_figures(body)):
        diff = abs(a.value - b.value) * (1 + rng.uniform(0.1, 0.3) * rng.choice([-1, 1]))
        text = _diff_text(a, b, diff)
        return body + f" That is a difference of {text}.", f"|{a.raw} - {b.raw}| stated as {text}"
    return None


def _swapped_figures(body, rng, ctx_text):
    for a, b in _like_pairs(_figures(body)):
        sa, ea = _digits_span(body, a)
        sb, eb = _digits_span(body, b)
        ra, rb = body[sa:ea], body[sb:eb]
        if ra == rb:
            continue
        swapped = body[:sa] + rb + body[ea:sb] + ra + body[eb:]
        return swapped, f"{ra} <-> {rb}"
    return None


def _unit_words(body, rng, ctx_text):
    m = re.search(r"\$([\d,.]+) (million|billion)", body)
    if m:
        return _replace(body, m.start(), m.end(), f"{m.group(1)} {m.group(2)} dollars"), "currency in words"
    m = re.search(r"(\d)%", body)
    if m:
        return _replace(body, m.start(), m.end(), m.group(1) + " percent"), "percent in words"
    return None


_FIG = r"\$?\(?[\d][\d,]*(?:\.\d+)?\)?(?: (?:million|billion|thousand))?%?"
_PERIOD_PAIR = re.compile(
    r"^(?P<subj>[A-Z][^.]*?) (?:was|were|reached|totaled|rose to|grew to|fell to|improved to|of) "
    rf"(?P<a>{_FIG}) (?:in|for) (?P<fa>fiscal )?(?P<ya>20\d\d),? "
    r"(?:compared with|compared to|up from|down from|versus|from|an increase from) "
    rf"(?P<b>{_FIG}) (?:in|for) (?P<fb>fiscal )?(?P<yb>20\d\d)"
)


def _reordered(body, rng, ctx_text):
    m = _PERIOD_PAIR.search(body)
    if not m:
        return None
    subj = m.group("subj")
    # Lowercase a generic opening word ("Net income"), keep company names.
    if not re.match(r"[A-Z][a-z]+ [A-Z]", subj):
        subj = subj[0].lower() + subj[1:]
    fa, fb = m.group("fa") or "", m.group("fb") or ""
    text = (f"In {fb}{m.group('yb')}, {subj} stood at {m.group('b')}. "
            f"By {fa}{m.group('ya')} it was {m.group('a')}.")
    rest = body[m.end():].lstrip(" ,.")
    if rest:
        text += " " + rest[0].upper() + rest[1:]
    return text, "periods restated in reverse order"


def _no_year(body, rng, ctx_text):
    years = [q for q in extract(body) if q.kind == Kind.YEAR]
    if not years:
        return None
    # Keep only the first sentence, with its first year phrase removed.
    first = re.split(r"(?<=[.!?])\s+", body)[0]
    stripped = re.sub(r",? (?:in|for|during|at the end of|as of)(?: fiscal)? 20\d\d\b", "", first, count=1)
    if stripped == first or not _figures(stripped):
        return None
    # Only safe when the sentence has one figure per year mentioned; otherwise the
    # figure without its year is genuinely ambiguous.
    if len([q for q in extract(stripped) if q.kind == Kind.YEAR]) > 0:
        return None
    return stripped, "year dropped"


_EDITS = {
    "number_shift": _number_shift,
    "digit_swap": _digit_swap,
    "scale_swap": _scale_swap,
    "wrong_year": _wrong_year,
    "invented_claim": _invented_claim,
    "wrong_math": _wrong_math,
    "swapped_figures": _swapped_figures,
    "rounded": _rounded,
    "rounded_coarse": _rounded_coarse,
    "derived": _derived,
    "unit_words": _unit_words,
    "reordered": _reordered,
    "no_year": _no_year,
}


# --------------------------------------------------------------------------
# Building cases
# --------------------------------------------------------------------------

def build_cases(corpus: list[dict], seed: int = 7) -> list[Case]:
    """Create every applicable case for every passage, reproducibly."""
    rng = random.Random(seed)
    by_company: dict[str, list[dict]] = {}
    for p in corpus:
        by_company.setdefault(p.get("company", ""), []).append(p)

    cases: list[Case] = []
    for p in corpus:
        peers = [q for q in by_company[p.get("company", "")] if q["id"] != p["id"]] or [
            q for q in corpus if q["id"] != p["id"]
        ]
        distractor = rng.choice(peers)
        contexts = [Source(p["id"], p["text"]), Source(distractor["id"], distractor["text"])]
        ctx_text = p["text"] + "\n" + distractor["text"]
        question = f"What does the annual report say about {p.get('company', 'the company')}?"
        body = p["text"]

        def add(kind, answer_body, cite, hallucinated, note="", pid=p["id"], q=question, ctx=contexts):
            ans = f"{answer_body.rstrip()} [{cite}]" if cite else answer_body
            cases.append(Case(Record(f"{pid}:{kind}", q, ans, list(ctx)), hallucinated, kind, note))

        add("quote", body, p["id"], False)
        add("fabricated_citation", body, f"{p['id'].split('-')[0]}-{rng.randint(90, 99)}", True, "id not retrieved")
        figures = _figures(body)
        # Misattribution only counts if the distractor lacks the cited figures.
        if figures and not any(q.raw in distractor["text"] for q in figures):
            add("misattributed", body, distractor["id"], True, f"cites {distractor['id']} instead of {p['id']}")
        for kind, fn in _EDITS.items():
            out = fn(body, rng, ctx_text)
            if out is None:
                continue
            new_body, note = out
            add(kind, new_body, p["id"], kind in HALLUCINATED, note)
    return cases


def save_cases(cases: list[Case], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(c.to_dict()) + "\n" for c in cases)


def load_cases(path: str | Path) -> list[Case]:
    with open(path, encoding="utf-8") as f:
        return [Case.from_dict(json.loads(line)) for line in f if line.strip()]
