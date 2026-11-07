"""The leaderboard's data files and page builder, plus each stack against a fake Ollama."""

from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from hauditor.live import PythonTarget, load_probes, run_live

LB = Path(__file__).parent.parent / "leaderboard"
sys.path.insert(0, str(LB))

import build_site
import fake_ollama

DOCS = [json.loads(line) for line in (LB / "data" / "corpus.jsonl").read_text().splitlines() if line.strip()]
PROBES = load_probes(LB / "data" / "probes.jsonl")


def test_data_files_are_consistent():
    assert len({d["id"] for d in DOCS}) == len(DOCS)
    for d in DOCS:
        assert d["text"].startswith(f"[{d['id']}] ")
    assert len({p.id for p in PROBES}) == len(PROBES)
    assert {p.category.value for p in PROBES} == set(build_site.CATS)


@pytest.fixture(scope="module")
def ollama_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake_ollama.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.parametrize("stack,module", [
    ("langchain", "langchain_ollama"), ("llamaindex", "llama_index.llms.ollama"),
    ("haystack", "haystack_integrations.components.generators.ollama"),
])
def test_stack_runs_end_to_end(stack, module, ollama_url):
    pytest.importorskip(module)
    from stacks import STACKS

    ask = STACKS[stack](DOCS, model="fake", embed_model="fake-embed", base_url=ollama_url)
    out = ask("What was Halvorsen's total revenue in fiscal 2025?")
    assert out["contexts"] and all(c["id"].startswith("p") for c in out["contexts"])
    assert out["answer"].strip()
    report = run_live(PythonTarget(ask, name=stack), PROBES[:3])
    assert report.score > 0


def test_build_site_renders(tmp_path):
    run = {
        "target": "x", "hauditor_score": 72.5,
        "categories": {c: {"pass": 1, "fail": 1, "abstain": 0, "unverified": 0, "error": 0, "score": 50.0}
                       for c in build_site.CATS},
        "audit": {"numbers_unsupported": 3, "citations_checked": 4, "citations_by_verdict": {"supported": 2}},
        "probes": [{"probe": {"id": "a", "category": "lookup", "question": "Q <b>?"}, "outcome": "fail",
                    "reason": "unsupported number 5", "audit": {"answer": "It was 5."}}],
        "meta": {"stack": "langchain", "model": "m:1b", "embed_model": "e", "packages": {"p": "1"},
                 "probes": 1, "run_at": "2026-11-07T00:00:00+00:00"},
    }
    (tmp_path / "r.json").write_text(json.dumps(run))
    build_site.main(["--results", str(tmp_path), "--out", str(tmp_path / "site")])
    html = (tmp_path / "site" / "index.html").read_text()
    assert "Q &lt;b&gt;?" in html and "unsupported number 5" in html
    assert "| 1 | langchain | m:1b | **72** |" in (tmp_path / "site" / "leaderboard.md").read_text()
