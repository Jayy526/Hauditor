"""Command line entry point: ``hauditor audit outputs.jsonl``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .audit import audit, load_jsonl
from .grounding import GroundingChecker
from .report import render_html


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hauditor", description="Audit RAG answers for invented numbers.")
    p.add_argument("--version", action="version", version=f"hauditor {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="check a JSONL file of RAG outputs")
    a.add_argument("path", help="JSONL with question, contexts and answer per line")
    a.add_argument("--html", metavar="FILE", help="write an HTML report here")
    a.add_argument("--json", metavar="FILE", help="write per-answer results as JSON here")
    a.add_argument("--strict", action="store_true", help="only accept numbers quoted from sources, no arithmetic")
    a.add_argument("--fail-above", type=float, metavar="RATE",
                   help="exit 1 if the answer hallucination rate exceeds RATE (for CI)")
    args = p.parse_args(argv)

    checker = GroundingChecker(allow_derived=not args.strict)
    summary = audit(load_jsonl(args.path), checker)
    s = summary.to_dict()

    print(f"answers audited            {s['answers']}")
    print(f"numbers checked            {s['numbers_checked']}")
    print(f"numbers unsupported        {s['numbers_unsupported']} ({s['unsupported_number_rate']:.1%})")
    print(f"answers with invented num  {s['answers_with_unsupported_number']} ({s['answer_hallucination_rate']:.1%})")
    for r in summary.results:
        for n in r.unsupported:
            print(f"  [{r.record.id}] unsupported: {n.quantity.raw}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"summary": s, "results": [r.to_dict() for r in summary.results]}, f, indent=2)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(summary))

    if args.fail_above is not None and s["answer_hallucination_rate"] > args.fail_above:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
