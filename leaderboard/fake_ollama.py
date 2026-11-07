"""A stand-in for the Ollama server, for testing the leaderboard plumbing offline.

It speaks just enough of Ollama's HTTP API (chat, generate, embed) for the
three stacks to run end to end. Embeddings are hashed bags of words, and the
"model" answers by quoting the context sentence that best overlaps the
question. Scores from it mean nothing; it only proves the wiring works.

    python leaderboard/fake_ollama.py --port 11435
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 64
_WORD = re.compile(r"[a-z0-9]+")


def embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in _WORD.findall(text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def answer(prompt: str) -> str:
    q = re.search(r"Question:\s*(.+?)\s*(?:\n|Context:|Answer:|$)", prompt) or re.search(r"Query:\s*(.+)", prompt)
    words = set(_WORD.findall((q.group(1) if q else prompt[-300:]).lower()))
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prompt) if re.search(r"\d", s) and "Question" not in s and "?" not in s]
    best = max(sents, key=lambda s: len(words & set(_WORD.findall(s.lower()))), default="")
    if not best or len(words & set(_WORD.findall(best.lower()))) < 2:
        return "I don't know."
    return re.sub(r"^.*?(\[p\d+\])", r"\1", best)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": []} if self.path.startswith("/api/tags") else {"version": "0.0.0"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        now = datetime.now(timezone.utc).isoformat()
        base = {"model": req.get("model", "fake"), "created_at": now, "done": True, "done_reason": "stop",
                "prompt_eval_count": 1, "eval_count": 1, "total_duration": 1, "load_duration": 1,
                "prompt_eval_duration": 1, "eval_duration": 1}
        if self.path == "/api/embed":
            inp = req["input"] if isinstance(req["input"], list) else [req["input"]]
            return self._send({"model": base["model"], "embeddings": [embed(t) for t in inp]})
        if self.path == "/api/embeddings":
            return self._send({"embedding": embed(req["prompt"])})
        if self.path == "/api/chat":
            prompt = "\n".join(str(m.get("content", "")) for m in req.get("messages", []))
            msg = {"role": "assistant", "content": answer(prompt)}
            if req.get("stream", True):
                lines = [json.dumps({**base, "message": msg, "done": False}),
                         json.dumps({**base, "message": {"role": "assistant", "content": ""}})]
                body = ("\n".join(lines) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self._send({**base, "message": msg})
        if self.path == "/api/generate":
            return self._send({**base, "response": answer(req.get("prompt", ""))})
        if self.path == "/api/show":
            return self._send({"model_info": {"general.context_length": 8192}, "capabilities": ["completion"]})
        self.send_response(404)
        self.end_headers()
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ThreadingHTTPServer(("127.0.0.1", ap.parse_args().port), Handler).serve_forever()
