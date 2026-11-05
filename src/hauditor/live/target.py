"""Talk to the RAG app under test.

A target takes a question and returns what the app said plus the passages it
retrieved. Hauditor needs the passages: without them there is nothing to
check the answer against. Most RAG APIs already return them as "sources" or
"context"; :class:`HttpTarget` is configured with where to find them.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..citations import Claim
from ..grounding import Source


@dataclass
class TargetResponse:
    answer: str
    contexts: list[Source]
    citations: list[Claim] | None = None
    raw: Any = None


class Target(Protocol):
    name: str

    def ask(self, question: str) -> TargetResponse: ...


def get_path(data: Any, path: str) -> Any:
    """Follow a dotted path like ``"data.sources"`` or ``"choices.0.text"``."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, list):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data[part]
        else:
            raise KeyError(f"cannot follow {part!r} into {type(data).__name__}")
    return data


def _fill(template: Any, question: str) -> Any:
    """Put the question wherever the template says ``{question}``."""
    if isinstance(template, str):
        return question if template == "{question}" else template.replace("{question}", question)
    if isinstance(template, dict):
        return {k: _fill(v, question) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, question) for v in template]
    return template


def contexts_from(items: Any, text_key: str = "text", id_key: str = "id") -> list[Source]:
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    out = []
    for i, c in enumerate(items):
        if isinstance(c, str):
            out.append(Source(id=str(i + 1), text=c))
        else:
            out.append(Source(id=str(c.get(id_key, i + 1)), text=str(get_path(c, text_key))))
    return out


@dataclass
class HttpTarget:
    """A RAG app behind an HTTP JSON endpoint.

    Example config (``target.json``)::

        {
          "name": "acme-docs-bot",
          "url": "http://localhost:8000/ask",
          "body": {"query": "{question}"},
          "answer_path": "answer",
          "contexts_path": "sources",
          "context_text_key": "content",
          "context_id_key": "doc_id"
        }
    """

    url: str
    name: str = "http-target"
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = field(default_factory=lambda: {"question": "{question}"})
    answer_path: str = "answer"
    contexts_path: str = "contexts"
    context_text_key: str = "text"
    context_id_key: str = "id"
    citations_path: str | None = None
    timeout: float = 60.0

    @classmethod
    def from_config(cls, path: str | Path) -> HttpTarget:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        # Let configs reference secrets without storing them: "$ENV_VAR".
        import os

        cfg["headers"] = {
            k: os.environ.get(v[1:], "") if isinstance(v, str) and v.startswith("$") else v
            for k, v in cfg.get("headers", {}).items()
        }
        return cls(**cfg)

    def ask(self, question: str) -> TargetResponse:
        payload = json.dumps(_fill(self.body, question)).encode() if self.method != "GET" else None
        url = self.url
        if self.method == "GET":
            from urllib.parse import quote

            url = _fill(self.url, quote(question))
        req = urllib.request.Request(
            url, data=payload, method=self.method,
            headers={"Content-Type": "application/json", **self.headers},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        citations = None
        if self.citations_path:
            citations = [
                Claim(c["claim"], [str(x) for x in c.get("source_ids", [])])
                for c in get_path(data, self.citations_path) or []
            ]
        return TargetResponse(
            answer=str(get_path(data, self.answer_path)),
            contexts=contexts_from(get_path(data, self.contexts_path), self.context_text_key, self.context_id_key),
            citations=citations,
            raw=data,
        )


@dataclass
class PythonTarget:
    """Wrap a Python function ``question -> dict`` as a target.

    The dict needs ``answer`` and ``contexts`` (strings or ``{id, text}``),
    and may include ``citations``.
    """

    fn: Callable[[str], dict]
    name: str = "python-target"

    def ask(self, question: str) -> TargetResponse:
        d = self.fn(question)
        cites = d.get("citations")
        return TargetResponse(
            answer=d["answer"],
            contexts=contexts_from(d.get("contexts")),
            citations=[Claim(c["claim"], [str(x) for x in c.get("source_ids", [])]) for c in cites] if cites else None,
            raw=d,
        )
