"""Hauditor: audit RAG answers for numbers and citations the sources don't support."""

__version__ = "0.1.0"

from .audit import AuditSummary, Record, audit, load_jsonl
from .citations import CitationChecker, CitationVerdict, Claim, LexicalJudge, extract_claims
from .grounding import GroundingChecker, NumberCheck, Source, Verdict
from .numbers import Kind, Quantity, extract

__all__ = [
    "AuditSummary",
    "CitationChecker",
    "CitationVerdict",
    "Claim",
    "GroundingChecker",
    "Kind",
    "LexicalJudge",
    "NumberCheck",
    "Quantity",
    "Record",
    "Source",
    "Verdict",
    "audit",
    "extract",
    "extract_claims",
    "load_jsonl",
]
