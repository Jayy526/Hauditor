"""Grade saved answers again with the current checker, without asking the models.

    python leaderboard/regrade.py

Each saved run is replayed: the same answers and the same retrieved passages
go back through ``run_live``. Use it after changing the checker, so scores
reflect the checker and not a new roll of the model's dice.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from hauditor import __version__
from hauditor.live import Probe, PythonTarget, run_live

HERE = Path(__file__).parent


def main(argv: list[str] | None = None) -> int:
    results = Path(argv[0] if argv else HERE / "results")
    corpus = {
        d["id"]: d["text"]
        for d in map(json.loads, (HERE / "data" / "corpus.jsonl").read_text(encoding="utf-8").splitlines())
    }
    for path in sorted(results.glob("*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        saved = {
            p["probe"]["id"]: p for p in old["probes"] if "audit" in p and "context_ids" in p
        }
        probes = [Probe.from_dict(p["probe"]) for p in old["probes"] if p["probe"]["id"] in saved]
        by_question = {
            p.question: {
                "answer": saved[p.id]["audit"]["answer"],
                "contexts": [{"id": i, "text": corpus[i]} for i in saved[p.id]["context_ids"]],
            }
            for p in probes
        }
        report = run_live(PythonTarget(lambda q, m=by_question: m[q], name=old["target"]), probes)
        new = report.to_dict()
        new["meta"] = {**old["meta"], "graded_with": __version__,
                       "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        print(f"{path.stem:<28} {old['hauditor_score']:5.1f} -> {new['hauditor_score']:5.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
