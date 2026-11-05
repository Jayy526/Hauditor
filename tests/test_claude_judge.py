import json
from types import SimpleNamespace

from hauditor.claude_judge import ClaudeJudge


class FakeMessages:
    def __init__(self, verdict, stop_reason="end_turn"):
        self.verdict, self.stop_reason, self.calls = verdict, stop_reason, []

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps({"verdict": self.verdict, "reason": "because"})
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def judge_with(verdict, **kw):
    msgs = FakeMessages(verdict, **kw)
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return ClaudeJudge(client=client), msgs


def test_supported():
    j, msgs = judge_with("supported")
    r = j.judge("claim", "passage")
    assert r.supported and r.score == 1.0 and r.reason == "because"
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<passage>\npassage\n</passage>" in call["messages"][0]["content"]


def test_partial_counts_as_not_supported():
    j, _ = judge_with("partially_supported")
    r = j.judge("claim", "passage")
    assert not r.supported and r.score == 0.5


def test_refusal_is_not_supported():
    j, _ = judge_with("supported", stop_reason="refusal")
    assert not j.judge("claim", "passage").supported
