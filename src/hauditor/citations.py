"""Check that the citations in an answer point at real sources that back the claim.

Three things can go wrong with a citation, and they're reported separately
because they mean different things about the system under test:

* **fabricated**: the cited source doesn't exist in what was retrieved. The
  model invented a reference.
* **wrong_numbers**: the sentence states a number the cited source doesn't
  contain or support. If another retrieved source has it, the citation is
  *misattributed*; if none does, the number itself is invented.
* **not_supported**: the source exists but doesn't say what the sentence says,
  according to a pluggable judge (a lexical baseline, or an LLM).

Numbers are always checked deterministically with :mod:`hauditor.grounding`,
before any judge is consulted, so an LLM judge never gets to wave through a
made-up figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .grounding import GroundingChecker, Source, Verdict

# [1], [2, 3], [doc-7], [^4]. Not markdown links "[text](url)". Ids have no
# spaces, so prose in brackets like "[in millions]" isn't mistaken for one.
MARKER_RE = re.compile(r"\[\^?([A-Za-z0-9][\w.:#/-]{0,40}(?:\s*[,;]\s*\^?[A-Za-z0-9][\w.:#/-]{0,40})*)\](?!\()")
_SENT_END_RE = re.compile(r"[.!?](?=\s|$)")


class CitationVerdict(str, Enum):
    SUPPORTED = "supported"
    FABRICATED = "fabricated"
    WRONG_NUMBERS = "wrong_numbers"
    NOT_SUPPORTED = "not_supported"


@dataclass
class Claim:
    """A sentence from the answer and the source ids it cites."""

    text: str
    source_ids: list[str]


@dataclass
class JudgeResult:
    supported: bool
    score: float
    reason: str = ""


class Judge(Protocol):
    name: str

    def judge(self, claim: str, passage: str) -> JudgeResult: ...


@dataclass
class CitationCheck:
    claim: Claim
    verdict: CitationVerdict
    detail: str = ""
    missing_ids: list[str] = field(default_factory=list)
    judge_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "claim": self.claim.text,
            "cited": self.claim.source_ids,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "missing_ids": self.missing_ids,
            "judge_score": self.judge_score,
        }


# --------------------------------------------------------------------------
# Finding claims and their citations in free text
# --------------------------------------------------------------------------


def _sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence, with start at its first non-space char."""
    spans, start = [], 0
    for end in [m.end() for m in _SENT_END_RE.finditer(text)] + [len(text)]:
        chunk = text[start:end]
        if chunk.strip():
            spans.append((start + len(chunk) - len(chunk.lstrip()), end))
        start = end
    return spans


def _looks_like_citation(ids: list[str], known: set[str] | None) -> bool:
    """Tell "[3]" or "[doc-7]" apart from editorial brackets like "[sic]".

    A bracket counts as a citation if any id contains a digit (fabricated ids
    usually do) or names a known source.
    """
    return any(any(ch.isdigit() for ch in i) or (known is not None and i.lower() in known) for i in ids)


def extract_claims(answer: str, source_ids: list[str] | None = None) -> list[Claim]:
    """Split ``answer`` into sentences and attach each inline citation marker.

    A marker attaches to the sentence it sits in, or to the sentence just
    before it when it trails the full stop ("... rose 11%. [2]").
    """
    known = {s.lower() for s in source_ids} if source_ids is not None else None
    clean_parts, markers, pos = [], [], 0
    clean_len = 0
    for m in MARKER_RE.finditer(answer):
        ids = [s.strip().lstrip("^") for s in re.split(r"[,;]", m.group(1)) if s.strip()]
        if not _looks_like_citation(ids, known):
            continue
        chunk = answer[pos:m.start()]
        clean_parts.append(chunk)
        clean_len += len(chunk)
        markers.append((clean_len, ids))
        pos = m.end()
    clean_parts.append(answer[pos:])
    clean = "".join(clean_parts)

    spans = _sentences(clean)
    cited: dict[int, list[str]] = {}
    for c, ids in markers:
        # The last sentence that starts strictly before the marker.
        idx = max((i for i, (s, _) in enumerate(spans) if s < c), default=0)
        cited.setdefault(idx, [])
        cited[idx] += [i for i in ids if i not in cited[idx]]

    claims = []
    for idx, ids in sorted(cited.items()):
        s, e = spans[idx] if spans else (0, len(clean))
        text = re.sub(r"\s+", " ", clean[s:e]).strip()
        text = re.sub(r"\s+([.,;:!?])", r"\1", text)
        claims.append(Claim(text=text, source_ids=ids))
    return claims


def resolve(source_id: str, sources: list[Source]) -> Source | None:
    """Find a cited source by id, or by 1-based position for numeric markers."""
    for s in sources:
        if s.id == source_id:
            return s
    low = source_id.lower()
    for s in sources:
        if s.id.lower() == low:
            return s
    if source_id.isdigit():
        n = int(source_id)
        if 1 <= n <= len(sources):
            return sources[n - 1]
    return None


# --------------------------------------------------------------------------
# Judges
# --------------------------------------------------------------------------

_STOP = set(
    """a an and are as at be been but by for from has have had in into is it its of on or that the
    their there these this those to was were which while with will would than then also about over
    under after before during not no more most less very such can could may might should our your
    they them he she we you i his her him who whom what when where why how all any both each few
    other some same so too only own just per vs thousand million billion trillion percent""".split()  # noqa: SIM905
)


def _stem(w: str) -> str:
    for suf in ("ingly", "edly", "ing", "ies", "ied", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]+", text.lower())
    return {_stem(w) for w in words if w not in _STOP and len(w) > 2}


class LexicalJudge:
    """Zero-dependency baseline: does the passage share the claim's content words?

    It catches citations to the wrong document well and paraphrase poorly.
    Use :class:`ClaudeJudge` when that matters; this one is free and
    deterministic, which makes it the right default for CI.
    """

    name = "lexical"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def judge(self, claim: str, passage: str) -> JudgeResult:
        words = _content_words(claim)
        if not words:
            return JudgeResult(True, 1.0, "no content words to compare")
        have = _content_words(passage)
        hit = words & have
        score = len(hit) / len(words)
        missing = sorted(words - have)
        reason = f"{len(hit)}/{len(words)} content words found"
        if missing:
            reason += "; missing: " + ", ".join(missing[:6])
        return JudgeResult(score >= self.threshold, score, reason)


# --------------------------------------------------------------------------
# Checker
# --------------------------------------------------------------------------


class CitationChecker:
    def __init__(self, judge: Judge | None = None, numbers: GroundingChecker | None = None) -> None:
        self.judge = judge or LexicalJudge()
        self.numbers = numbers or GroundingChecker()

    def check(self, claims: list[Claim], sources: list[Source]) -> list[CitationCheck]:
        return [self._check_one(c, sources) for c in claims]

    def _check_one(self, claim: Claim, sources: list[Source]) -> CitationCheck:
        found, missing = [], []
        for sid in claim.source_ids:
            src = resolve(sid, sources)
            (found if src else missing).append(src or sid)

        if not found:
            return CitationCheck(
                claim, CitationVerdict.FABRICATED,
                f"cites {', '.join(missing)}, which {'is' if len(missing) == 1 else 'are'} not among the retrieved sources",
                missing_ids=missing,
            )

        # Numbers first, deterministically, against the cited sources only.
        cited = [Source(s.id, s.text) for s in found]
        bad = [n for n in self.numbers.check(claim.text, cited) if n.verdict == Verdict.UNSUPPORTED]
        if bad:
            found_ids = {s.id for s in found}
            others = [Source(s.id, s.text) for s in sources if s.id not in found_ids]
            notes = []
            for n in bad:
                elsewhere = [
                    m for m in self.numbers.check(n.quantity.raw, others) if m.verdict != Verdict.UNSUPPORTED
                ] if others else []
                if elsewhere:
                    notes.append(f"{n.quantity.raw} is in {elsewhere[0].source_id}, not the cited source")
                else:
                    notes.append(f"{n.quantity.raw} is in no retrieved source")
            return CitationCheck(claim, CitationVerdict.WRONG_NUMBERS, "; ".join(notes), missing_ids=missing)

        passage = "\n\n".join(s.text for s in found)
        res = self.judge.judge(claim.text, passage)
        verdict = CitationVerdict.SUPPORTED if res.supported else CitationVerdict.NOT_SUPPORTED
        detail = f"{self.judge.name}: {res.reason}"
        if missing:
            # Partly fabricated: some ids resolved, some didn't. Keep the
            # judge's verdict but surface the invented ids.
            detail = f"also cites missing {', '.join(missing)}; " + detail
            if verdict == CitationVerdict.SUPPORTED:
                verdict = CitationVerdict.FABRICATED
        return CitationCheck(claim, verdict, detail, missing_ids=missing, judge_score=res.score)
