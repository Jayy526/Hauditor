"""Score Hauditor against a labelled benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audit import audit
from ..citations import CitationChecker
from ..grounding import GroundingChecker
from .generate import HALLUCINATED, HONEST, Case


@dataclass
class BenchResult:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    by_kind: dict[str, list[int]] = field(default_factory=dict)  # kind -> [correct, total]
    misses: list[tuple[Case, str]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def accuracy(self) -> float:
        n = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / n if n else 0.0

    def to_dict(self) -> dict:
        return {
            "cases": self.tp + self.fp + self.tn + self.fn,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "accuracy": round(self.accuracy, 3),
            "confusion": {"tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn},
            "by_kind": {k: {"correct": c, "total": t, "rate": round(c / t, 3)} for k, (c, t) in self.by_kind.items()},
        }

    def to_markdown(self, title: str = "Benchmark results") -> str:
        lines = [
            f"## {title}",
            "",
            (
                f"{self.tp + self.fp + self.tn + self.fn} cases. "
                f"Precision **{self.precision:.1%}**, recall **{self.recall:.1%}**, F1 **{self.f1:.1%}**."
            ),
            "",
            "| Case type | Should be | Hauditor got it right |",
            "|---|---|---|",
        ]
        for group, expected in ((HALLUCINATED, "flagged"), (HONEST, "passed")):
            for k in group:
                if k in self.by_kind:
                    c, t = self.by_kind[k]
                    lines.append(f"| {k} | {expected} | {c}/{t} ({c / t:.0%}) |")
        return "\n".join(lines) + "\n"


def run_bench(cases: list[Case], checker: GroundingChecker | None = None,
              citation_checker: CitationChecker | None = None) -> BenchResult:
    checker = checker or GroundingChecker()
    summary = audit([c.record for c in cases], checker, citation_checker or CitationChecker(numbers=checker))
    out = BenchResult()
    for case, res in zip(cases, summary.results):
        flagged = res.hallucinated
        if case.hallucinated and flagged:
            out.tp += 1
        elif case.hallucinated:
            out.fn += 1
        elif flagged:
            out.fp += 1
        else:
            out.tn += 1
        right = flagged == case.hallucinated
        stat = out.by_kind.setdefault(case.kind, [0, 0])
        stat[0] += right
        stat[1] += 1
        if not right:
            why = "; ".join(
                [f"number {n.quantity.raw} unsupported" for n in res.unsupported]
                + [f"{c.verdict.value} citation ({c.detail})" for c in res.bad_citations]
            ) or "nothing flagged"
            out.misses.append((case, why))
    return out
