"""Hauditor: audit RAG answers for numbers and citations the sources don't support."""

__version__ = "0.1.0"

from .audit import AuditSummary, Record, audit, load_jsonl
from .grounding import GroundingChecker, NumberCheck, Source, Verdict
from .numbers import Kind, Quantity, extract

__all__ = [
    "AuditSummary",
    "GroundingChecker",
    "Kind",
    "NumberCheck",
    "Quantity",
    "Record",
    "Source",
    "Verdict",
    "audit",
    "extract",
    "load_jsonl",
]
