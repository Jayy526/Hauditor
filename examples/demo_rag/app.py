"""A tiny RAG app to point Hauditor at. No LLM, no dependencies.

It retrieves passages from a fictional company's annual report by keyword
overlap and answers with the best-matching sentence. It has two modes:

* ``honest``: quotes its sources, cites them, and says when it can't find
  something.
* ``sloppy``: behaves like a typical overconfident chatbot. Under pressure,
  or when retrieval comes up short, it makes up a plausible figure, and it
  sometimes cites the wrong passage.

Run it as a server::

    python examples/demo_rag/app.py --mode sloppy --port 8765

then audit it::

    hauditor live --target examples/demo_rag/target.json --probes examples/demo_rag/probes.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

DOCS = [
    {"id": "ar-p12", "text": "Northwind Robotics reported total revenue of $812.4 million in fiscal 2025, "
                             "compared with $706.9 million in fiscal 2024. Growth was driven by the warehouse "
                             "automation segment."},
    {"id": "ar-p13", "text": "The warehouse automation segment generated $524.0 million of revenue in fiscal 2025. "
                             "The field services segment generated $288.4 million."},
    {"id": "ar-p18", "text": "Gross margin was 41.3% in fiscal 2025, down from 43.0% in fiscal 2024, mainly due to "
                             "higher component costs."},
    {"id": "ar-p22", "text": "Research and development expense was $97.2 million in fiscal 2025. The company "
                             "employed 3,150 people at year end, including 940 engineers."},
    {"id": "ar-p30", "text": "Net income was $61.8 million in fiscal 2025. The board approved a share repurchase "
                             "program of up to $150 million."},
    {"id": "ar-p41", "text": "Northwind operates manufacturing sites in Ohio and Monterrey, Mexico. The company has "
                             "no operations in Asia."},
]

_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "is", "was", "what", "how", "much", "many",
    "did", "does", "do", "by", "with", "from", "at", "as", "it", "its", "company", "northwind", "please",
    "give", "me", "your", "tell",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def retrieve(question: str, k: int = 2) -> list[tuple[int, dict]]:
    """Rank passages by how many question words they share."""
    q = _words(question)
    scored = [(len(q & _words(d["text"])), d) for d in DOCS]
    return sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])[:k]


def _best_sentence(question: str, text: str) -> str:
    q = _words(question)
    sents = re.split(r"(?<=[.!?])\s+", text)
    return max(sents, key=lambda s: len(q & _words(s)))


_PRESSURE = re.compile(r"best (precise )?estimate|from memory|no caveats|always has the data|"
                       r"don't tell me you can't|exact number", re.IGNORECASE)


def answer(question: str, mode: str = "honest") -> dict:
    hits = retrieve(question)
    contexts = [d for _, d in hits]
    top_score = hits[0][0] if hits else 0
    # Years in the question that the best passage never mentions, e.g. "2027".
    missing_years = set(re.findall(r"\b20\d\d\b", question)) - set(re.findall(r"\b20\d\d\b", contexts[0]["text"])) \
        if contexts else set()
    pressured = bool(_PRESSURE.search(question))
    # A stable pseudo-random number per question, so runs are reproducible.
    h = int(hashlib.sha256(question.encode()).hexdigest(), 16)

    if top_score < 2 or missing_years:
        if mode == "sloppy":
            return {"answer": f"It was approximately ${(h % 900) / 10 + 10:.1f} million [ar-p{h % 50}].",
                    "contexts": contexts}
        return {"answer": "The documents don't mention that.", "contexts": contexts}

    doc = contexts[0]
    sentence = _best_sentence(question, doc["text"])
    if mode == "sloppy" and pressured:
        # Overconfident: tweaks the figure and cites the neighbouring passage.
        fudged = re.sub(r"\d+\.\d", lambda m: f"{float(m.group()) * 1.07:.1f}", sentence, count=1)
        wrong = contexts[1]["id"] if len(contexts) > 1 else doc["id"]
        return {"answer": f"{fudged.rstrip('.')} [{wrong}].", "contexts": contexts}
    return {"answer": f"{sentence.rstrip('.')} [{doc['id']}].", "contexts": contexts}


class _Handler(BaseHTTPRequestHandler):
    mode = "honest"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        result = answer(body.get("question", ""), self.mode)
        payload = json.dumps({"answer": result["answer"], "sources": result["contexts"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


def serve(port: int = 8765, mode: str = "honest") -> HTTPServer:
    handler = type("Handler", (_Handler,), {"mode": mode})
    return HTTPServer(("127.0.0.1", port), handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["honest", "sloppy"], default="honest")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"demo RAG app ({args.mode}) on http://127.0.0.1:{args.port}/ask")
    serve(args.port, args.mode).serve_forever()
