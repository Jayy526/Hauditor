"""Run the checks over a dataset of RAG outputs and aggregate the scores."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .citations import MARKER_RE, CitationCheck, CitationChecker, CitationVerdict, Claim, extract_claims
from .grounding import GroundingChecker, NumberCheck, Source, Verdict


@dataclass
class Record:
    """One RAG interaction: what was asked, what was retrieved, what was said."""

    id: str
    question: str
    answer: str
    contexts: list[Source]
    # Structured citations, for apps that return them separately from the text.
    # When absent, inline markers like [1] or [doc-3] are parsed from the answer.
    citations: list[Claim] | None = None

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
            citations=[
                Claim(text=c["claim"], source_ids=[str(x) for x in c.get("source_ids", [])])
                for c in d["citations"]
            ] if d.get("citations") else None,
        )


@dataclass
class RecordResult:
    record: Record
    numbers: list[NumberCheck] = field(default_factory=list)
    citations: list[CitationCheck] = field(default_factory=list)

    @property
    def unsupported(self) -> list[NumberCheck]:
        return [n for n in self.numbers if n.verdict == Verdict.UNSUPPORTED]

    @property
    def bad_citations(self) -> list[CitationCheck]:
        return [c for c in self.citations if c.verdict != CitationVerdict.SUPPORTED]

    @property
    def hallucinated(self) -> bool:
        return bool(self.unsupported or self.bad_citations)

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
            "citations": [c.to_dict() for c in self.citations],
            "hallucinated": self.hallucinated,
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
        cites = [c for r in self.results for c in r.citations]
        by_verdict = {v.value: sum(1 for c in cites if c.verdict == v) for v in CitationVerdict}
        bad = len(cites) - by_verdict["supported"]
        flagged = sum(1 for r in self.results if r.hallucinated)
        return {
            "answers": n,
            "numbers_checked": self.total_numbers,
            "numbers_unsupported": self.total_unsupported,
            "unsupported_number_rate": (
                self.total_unsupported / self.total_numbers if self.total_numbers else 0.0
            ),
            "answers_with_unsupported_number": self.answers_with_unsupported,
            "citations_checked": len(cites),
            "citations_by_verdict": by_verdict,
            "bad_citation_rate": bad / len(cites) if cites else 0.0,
            "answers_with_bad_citation": sum(1 for r in self.results if r.bad_citations),
            "answers_flagged": flagged,
            # An answer is flagged if it has an invented number or a bad citation.
            "answer_hallucination_rate": flagged / n if n else 0.0,
        }


def load_jsonl(path: str | Path) -> list[Record]:
    records = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                records.append(Record.from_dict(json.loads(line), i))
    return records


def _blank_markers(text: str) -> str:
    """Replace citation markers with spaces so "[12]" isn't read as a number.

    Same length as the original, so number spans still index into the answer.
    """
    return MARKER_RE.sub(lambda m: " " * len(m.group(0)), text)


def audit(
    records: Iterable[Record],
    checker: GroundingChecker | None = None,
    citation_checker: CitationChecker | None = None,
) -> AuditSummary:
    checker = checker or GroundingChecker()
    citation_checker = citation_checker or CitationChecker(numbers=checker)
    results = []
    for rec in records:
        # The question counts as a source for numbers: echoing a figure the
        # user gave is fine. It is not citable, so citations use contexts only.
        sources = list(rec.contexts)
        if rec.question:
            sources.append(Source(id="question", text=rec.question))
        numbers = checker.check(_blank_markers(rec.answer), sources)
        claims = rec.citations if rec.citations is not None else extract_claims(rec.answer, [c.id for c in rec.contexts])
        cites = citation_checker.check(claims, rec.contexts)
        results.append(RecordResult(rec, numbers, cites))
    return AuditSummary(results)
