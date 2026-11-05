"""Run the checks over a dataset of RAG outputs and aggregate the scores."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .grounding import GroundingChecker, NumberCheck, Source, Verdict


@dataclass
class Record:
    """One RAG interaction: what was asked, what was retrieved, what was said."""

    id: str
    question: str
    answer: str
    contexts: list[Source]

    @classmethod
    def from_dict(cls, d: dict, index: int = 0) -> Record:
        raw_ctx = d.get("contexts") or d.get("context") or []
        if isinstance(raw_ctx, str):
            raw_ctx = [raw_ctx]
        contexts = []
        for i, c in enumerate(raw_ctx):
            if isinstance(c, str):
                contexts.append(Source(id=f"ctx{i}", text=c))
            else:
                contexts.append(Source(id=str(c.get("id", f"ctx{i}")), text=c["text"]))
        return cls(
            id=str(d.get("id", index)),
            question=d.get("question", ""),
            answer=d["answer"],
            contexts=contexts,
        )


@dataclass
class RecordResult:
    record: Record
    numbers: list[NumberCheck] = field(default_factory=list)

    @property
    def unsupported(self) -> list[NumberCheck]:
        return [n for n in self.numbers if n.verdict == Verdict.UNSUPPORTED]

    @property
    def score(self) -> float | None:
        """Fraction of numbers in the answer that are unsupported (lower is better)."""
        if not self.numbers:
            return None
        return len(self.unsupported) / len(self.numbers)

    def to_dict(self) -> dict:
        return {
            "id": self.record.id,
            "question": self.record.question,
            "answer": self.record.answer,
            "numbers": [n.to_dict() for n in self.numbers],
            "unsupported_number_rate": self.score,
        }


@dataclass
class AuditSummary:
    results: list[RecordResult]

    @property
    def total_numbers(self) -> int:
        return sum(len(r.numbers) for r in self.results)

    @property
    def total_unsupported(self) -> int:
        return sum(len(r.unsupported) for r in self.results)

    @property
    def answers_with_unsupported(self) -> int:
        return sum(1 for r in self.results if r.unsupported)

    def to_dict(self) -> dict:
        n = len(self.results)
        return {
            "answers": n,
            "numbers_checked": self.total_numbers,
            "numbers_unsupported": self.total_unsupported,
            "unsupported_number_rate": (
                self.total_unsupported / self.total_numbers if self.total_numbers else 0.0
            ),
            "answers_with_unsupported_number": self.answers_with_unsupported,
            "answer_hallucination_rate": self.answers_with_unsupported / n if n else 0.0,
        }


def load_jsonl(path: str | Path) -> list[Record]:
    records = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                records.append(Record.from_dict(json.loads(line), i))
    return records


def audit(records: Iterable[Record], checker: GroundingChecker | None = None) -> AuditSummary:
    checker = checker or GroundingChecker()
    results = []
    for rec in records:
        # The question counts as a source: echoing a number the user gave is fine.
        sources = list(rec.contexts)
        if rec.question:
            sources.append(Source(id="question", text=rec.question))
        results.append(RecordResult(rec, checker.check(rec.answer, sources)))
    return AuditSummary(results)
