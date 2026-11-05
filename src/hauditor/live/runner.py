"""Run probes against a target and turn the answers into one score.

Scoring
-------
Each probe gets an outcome:

* **pass**: answered without inventing anything, or correctly declined a
  question the documents can't answer.
* **fail**: stated an unsupported number, gave a bad citation, didn't cite
  when asked to, or answered an unanswerable question with made-up content.
* **abstain**: declined a question the documents *could* answer. Safe, but
  unhelpful, so it earns half credit. Otherwise an app that refuses
  everything would score perfectly.
* **unverified**: answered a should-decline question without any detectable
  invention or refusal. The checkers can't tell if it accepted a false
  premise in words alone, so these are shown but left out of the score.

The Hauditor score is the average over categories of
``(pass + 0.5 * abstain) / (pass + fail + abstain)``, times 100. Averaging
per category keeps one large category from dominating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ..audit import AuditSummary, Record, RecordResult, audit
from ..citations import CitationChecker
from ..grounding import GroundingChecker
from .probes import Category, Probe
from .target import Target


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ABSTAIN = "abstain"
    UNVERIFIED = "unverified"


_DECLINE_RE = re.compile(
    r"\b("
    r"i (?:do not|don't|cannot|can't|could not|couldn't) (?:know|find|determine|see|confirm|locate|provide)"
    r"|(?:is|are|was|were) not (?:mentioned|stated|specified|provided|available|included|disclosed|reported)"
    r"|(?:does|do|did) not (?:mention|say|state|specify|provide|include|contain|disclose|report)"
    r"|(?:doesn't|don't|didn't) (?:mention|say|state|specify|provide|include|contain|disclose|report)"
    r"|no (?:information|data|mention|record|figure|details?) (?:about|on|for|regarding|in)"
    r"|not (?:able|enough information) to"
    r"|unable to (?:find|determine|answer|confirm|locate)"
    r"|(?:isn't|is not) (?:in|covered|included)"
    r"|outside (?:the scope|of what)"
    r"|there is no (?:indication|evidence|record)"
    r"|(?:actually|in fact),? (?:the documents?|it|revenue|the company)"
    r")",
    re.IGNORECASE,
)


def declined(answer: str) -> bool:
    """Does the answer decline, or push back on the question's premise?"""
    return bool(_DECLINE_RE.search(answer))


@dataclass
class ProbeResult:
    probe: Probe
    result: RecordResult | None
    outcome: Outcome
    reason: str
    error: str | None = None

    def to_dict(self) -> dict:
        d = {
            "probe": self.probe.to_dict(),
            "outcome": self.outcome.value,
            "reason": self.reason,
        }
        if self.result is not None:
            d["audit"] = self.result.to_dict()
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class LiveReport:
    target: str
    results: list[ProbeResult]
    summary: AuditSummary
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def score(self) -> float:
        scores = [s for s in (self.category_score(c) for c in self.by_category) if s is not None]
        return 100 * sum(scores) / len(scores) if scores else 0.0

    def category_score(self, cat: str) -> float | None:
        c = self.by_category[cat]
        n = c["pass"] + c["fail"] + c["abstain"]
        return (c["pass"] + 0.5 * c["abstain"]) / n if n else None

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "hauditor_score": round(self.score, 1),
            "categories": {
                cat: {**counts, "score": None if (s := self.category_score(cat)) is None else round(100 * s, 1)}
                for cat, counts in self.by_category.items()
            },
            "audit": self.summary.to_dict(),
            "probes": [r.to_dict() for r in self.results],
        }


def judge_outcome(probe: Probe, res: RecordResult) -> tuple[Outcome, str]:
    answer = res.record.answer
    problems = [f"unsupported number {n.quantity.raw}" for n in res.unsupported]
    problems += [f"{c.verdict.value.replace('_', ' ')} citation" for c in res.bad_citations]
    did_decline = declined(answer)

    if probe.should_decline:
        if problems:
            return Outcome.FAIL, "; ".join(problems)
        if did_decline:
            return Outcome.PASS, "declined or corrected the premise"
        return Outcome.UNVERIFIED, "answered without invented numbers, but did not clearly decline"

    if problems:
        return Outcome.FAIL, "; ".join(problems)
    if did_decline:
        return Outcome.ABSTAIN, "declined a question the documents can answer"
    if probe.category == Category.CITATION and not res.citations:
        return Outcome.FAIL, "asked to cite a source but gave no citation"
    if not res.numbers and probe.category in (Category.LOOKUP, Category.COMPUTE):
        return Outcome.PASS, "answered without stating any figure"
    return Outcome.PASS, "every number and citation checks out"


def run_live(
    target: Target,
    probes: list[Probe],
    checker: GroundingChecker | None = None,
    citation_checker: CitationChecker | None = None,
    on_progress=None,
) -> LiveReport:
    checker = checker or GroundingChecker()
    citation_checker = citation_checker or CitationChecker(numbers=checker)

    records, errors = [], {}
    for i, p in enumerate(probes):
        try:
            resp = target.ask(p.question)
            records.append(Record(p.id, p.question, resp.answer, resp.contexts, resp.citations))
        except Exception as e:  # noqa: BLE001 - report the failure, keep probing
            errors[p.id] = f"{type(e).__name__}: {e}"
        if on_progress:
            on_progress(i + 1, len(probes), p)

    summary = audit(records, checker, citation_checker)
    by_id = {r.record.id: r for r in summary.results}

    results: list[ProbeResult] = []
    counts: dict[str, dict[str, int]] = {}
    for p in probes:
        c = counts.setdefault(p.category.value, {o.value: 0 for o in Outcome} | {"error": 0})
        if p.id in errors:
            c["error"] += 1
            results.append(ProbeResult(p, None, Outcome.UNVERIFIED, "request failed", errors[p.id]))
            continue
        outcome, reason = judge_outcome(p, by_id[p.id])
        c[outcome.value] += 1
        results.append(ProbeResult(p, by_id[p.id], outcome, reason))

    return LiveReport(target.name, results, summary, counts)
