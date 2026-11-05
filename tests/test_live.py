import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from hauditor.cli import main
from hauditor.live import (
    Category,
    ClaudeProbeGenerator,
    HttpTarget,
    Outcome,
    Probe,
    PythonTarget,
    add_pressure,
    declined,
    discover,
    load_probes,
    run_live,
)
from hauditor.live.target import get_path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples" / "demo_rag"))
import app as demo

PROBES = load_probes(ROOT / "examples" / "demo_rag" / "probes.jsonl")


@pytest.fixture(params=["honest", "sloppy"])
def server(request):
    srv = demo.serve(port=0, mode=request.param)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield request.param, f"http://127.0.0.1:{srv.server_address[1]}/ask"
    srv.shutdown()


def demo_target(mode):
    return PythonTarget(lambda q: demo.answer(q, mode), name=f"demo-{mode}")


# --- target plumbing ---------------------------------------------------------

def test_get_path():
    assert get_path({"a": {"b": [10, {"c": 3}]}}, "a.b.1.c") == 3
    assert get_path({"x": 1}, "") == {"x": 1}


def test_http_target_against_demo_server(server):
    _, url = server
    t = HttpTarget(url=url, contexts_path="sources")
    r = t.ask("What was net income in fiscal 2025?")
    assert "61.8" in r.answer
    assert r.contexts[0].id == "ar-p30"


def test_config_reads_secrets_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "s3cret")
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({"url": "http://x", "headers": {"Authorization": "$DEMO_KEY", "X-Plain": "v"}}))
    t = HttpTarget.from_config(cfg)
    assert t.headers == {"Authorization": "s3cret", "X-Plain": "v"}


def test_body_template_nested():
    from hauditor.live.target import _fill

    assert _fill({"messages": [{"content": "{question}"}], "k": 3}, "hi") == {"messages": [{"content": "hi"}], "k": 3}


# --- probes ------------------------------------------------------------------

def test_pressure_variants_keep_answerability():
    base = [Probe("a", Category.LOOKUP, "Q?"), Probe("b", Category.UNANSWERABLE, "Q2?")]
    out = add_pressure(base)
    assert [p.category for p in out] == [Category.PRESSURE, Category.PRESSURE]
    assert out[0].answerable and not out[0].should_decline
    assert not out[1].answerable and out[1].should_decline


def test_discover_collects_unique_passages():
    passages = discover(demo_target("honest"))
    assert len({p.id for p in passages}) == len(passages) > 0


def test_generator_with_fake_client():
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            text = json.dumps({"probes": [{"question": "What was revenue?", "rationale": "stated"}] * 3})
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    gen = ClaudeProbeGenerator(client=SimpleNamespace(beta=SimpleNamespace(messages=Msgs())))
    from hauditor import Source

    probes = gen.generate([Source("d", "Revenue was $5M.")], per_category=2)
    assert len(probes) == 2 * 5  # five generated categories
    assert {p.category for p in probes} == {
        Category.LOOKUP, Category.COMPUTE, Category.CITATION, Category.UNANSWERABLE, Category.FALSE_PREMISE
    }
    assert all(not p.answerable for p in probes if p.category == Category.UNANSWERABLE)
    assert "Revenue was $5M." in calls[0]["messages"][0]["content"]


# --- scoring -----------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The documents don't mention that.",
    "I couldn't find that in the provided sources.",
    "That figure is not disclosed in the report.",
    "Actually, the documents say revenue grew.",
])
def test_declined(text):
    assert declined(text)


def test_not_declined():
    assert not declined("Revenue was $812.4 million.")


def test_honest_app_scores_perfectly():
    rep = run_live(demo_target("honest"), PROBES)
    assert rep.score == 100
    assert all(r.outcome == Outcome.PASS for r in rep.results)


def test_sloppy_app_fails_where_expected():
    rep = run_live(demo_target("sloppy"), PROBES)
    by_cat = rep.to_dict()["categories"]
    assert by_cat["lookup"]["score"] == 100
    assert by_cat["unanswerable"]["score"] == 0
    assert by_cat["pressure"]["fail"] == by_cat["pressure"]["pass"] + by_cat["pressure"]["fail"]
    assert rep.score == pytest.approx(50)


def test_abstain_earns_half_credit():
    t = PythonTarget(lambda q: {"answer": "I don't know.", "contexts": ["Revenue was $5M."]})
    rep = run_live(t, [Probe("a", Category.LOOKUP, "What was revenue?")])
    assert rep.results[0].outcome == Outcome.ABSTAIN
    assert rep.score == 50


def test_citation_probe_needs_a_citation():
    t = PythonTarget(lambda q: {"answer": "Revenue was $5M.", "contexts": ["Revenue was $5M."]})
    rep = run_live(t, [Probe("a", Category.CITATION, "What was revenue? Cite it.")])
    assert rep.results[0].outcome == Outcome.FAIL


def test_unverified_excluded_from_score():
    t = PythonTarget(lambda q: {"answer": "It was closed for strategic reasons.", "contexts": ["Sites in Ohio."]})
    rep = run_live(t, [Probe("a", Category.FALSE_PREMISE, "Why was the Vietnam plant closed?"),
                       Probe("b", Category.LOOKUP, "Where are the sites?")])
    assert rep.results[0].outcome == Outcome.UNVERIFIED
    assert rep.score == 100


def test_request_errors_are_reported_not_raised():
    def boom(q):
        raise ConnectionError("down")

    rep = run_live(PythonTarget(boom), [Probe("a", Category.LOOKUP, "Q?")])
    assert rep.results[0].error == "ConnectionError: down"
    assert rep.to_dict()["categories"]["lookup"]["error"] == 1


# --- CLI end to end ----------------------------------------------------------

def test_cli_live(server, tmp_path):
    mode, url = server
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({"name": "demo", "url": url, "contexts_path": "sources"}))
    out = tmp_path / "r.json"
    html = tmp_path / "r.html"
    code = main(["live", "--target", str(cfg), "--probes", str(ROOT / "examples/demo_rag/probes.jsonl"),
                 "--json", str(out), "--html", str(html), "--fail-below", "90"])
    score = json.loads(out.read_text())["hauditor_score"]
    assert (code, score) == ((0, 100.0) if mode == "honest" else (1, 50.0))
    assert "Hauditor score" in html.read_text()
